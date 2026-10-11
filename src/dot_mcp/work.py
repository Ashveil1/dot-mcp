"""Durable work records and bounded async commands for a single Linux owner.

No LLM/API worker: the connected client still decides what to do next.
Commands are never automatically replayed after a server restart.
"""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import threading
import time
import uuid

from dot_mcp import lifecycle

TASK_STATES = {"pending", "running", "blocked", "done", "failed", "cancelled", "interrupted"}
TERMINAL = {"done", "failed", "cancelled", "interrupted"}
SESSION_ACTIVE = {"starting", "running"}


class WorkStore:
    def __init__(self, data_dir: Path, resolve, shell, max_sessions=4,
                 output_limit=4 * 1024 * 1024, max_timeout=3600):
        self.root = Path(data_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)
        self.db = self.root / "work.sqlite3"
        self.output_dir = self.root / "command-output"
        self.output_dir.mkdir(exist_ok=True)
        self.output_dir.chmod(0o700)
        self.resolve, self.shell = resolve, shell
        self.max_sessions, self.output_limit, self.max_timeout = max_sessions, output_limit, max_timeout
        self._processes = {}
        self._threads = {}
        self._lock = threading.Lock()
        # Only one process may own the execution/recovery loop for this data dir.
        import fcntl
        self._owner = open(self.root / "work-owner.lock", "a")
        try:
            fcntl.flock(self._owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._owner.close()
            raise RuntimeError("work data directory is already owned by another server") from None
        with self.connection() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, detail TEXT NOT NULL,
                    state TEXT NOT NULL, result TEXT NOT NULL DEFAULT '',
                    blocker TEXT NOT NULL DEFAULT '', created REAL NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                    kind TEXT NOT NULL, text TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, task_id TEXT, command TEXT NOT NULL, cwd TEXT NOT NULL,
                    timeout INTEGER NOT NULL, state TEXT NOT NULL, exit_code INTEGER,
                    reason TEXT NOT NULL DEFAULT '', created REAL NOT NULL, updated REAL NOT NULL,
                    identity TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0,
                    idempotency_key TEXT UNIQUE, request_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL, path TEXT NOT NULL,
                    name TEXT NOT NULL, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL,
                    created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS inbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL UNIQUE,
                    text TEXT NOT NULL, acked INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
            """)
        self.db.chmod(0o600)
        try:
            self._migrate_legacy()
            self._recover()
        except BaseException:
            self._owner.close()
            raise

    @contextmanager
    def connection(self):
        c = sqlite3.connect(self.db, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=10000")
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def _migrate_legacy(self):
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            if c.execute("SELECT 1 FROM meta WHERE key='legacy_import_v1'").fetchone():
                return
            state_path = self.root / "state.json"
            state = {}
            if state_path.exists():
                # Preserve source bytes. No destructive migration or credential copying.
                raw = state_path.read_bytes()
                try:
                    state = json.loads(raw)
                except (ValueError, UnicodeError):
                    raise ValueError("cannot import invalid state.json; restore it before starting") from None
                backup = self.root / "state.pre-work-v1.json"
                if not backup.exists():
                    fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "wb") as f:
                        f.write(raw)
            now = time.time()
            for task in state.get("tasks", []) if isinstance(state, dict) else []:
                if not isinstance(task, dict) or not task.get("id"):
                    continue
                c.execute("INSERT OR IGNORE INTO tasks VALUES (?,?,?,?,?,?,?,?)", (
                    task["id"], str(task.get("title", "")), str(task.get("detail", "")),
                    "done" if task.get("done") else "pending", "", "", task.get("ts", now), now))
            c.execute("INSERT INTO meta VALUES ('legacy_import_v1', '1')")

    def _recover(self):
        with self.connection() as c:
            for row in c.execute("SELECT * FROM sessions WHERE state IN ('starting','running')").fetchall():
                info = json.loads(row["identity"]) if row["identity"] else None
                # If the old process survives, stop its verified process group.
                # Exit status cannot be reconstructed, so never report success.
                if info and lifecycle.identity(info["pid"]) == info:
                    try:
                        os.killpg(info["pid"], signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                c.execute("UPDATE sessions SET state='interrupted', reason=?, updated=? WHERE id=?", (
                    "server restarted; command not replayed", time.time(), row["id"]))
                c.execute("INSERT OR IGNORE INTO inbox(session_id,text,created) VALUES (?,?,?)", (
                    row["id"], f'Command {row["id"]} interrupted by server restart.', time.time()))
                if row["task_id"]:
                    c.execute("UPDATE tasks SET state='interrupted', updated=? WHERE id=? AND state='running'", (
                        time.time(), row["task_id"]))
                    self._event(c, row["task_id"], "interrupted", "server restarted; inspect before retrying")

    def _event(self, c, tid, kind, text):
        c.execute("INSERT INTO events(task_id,kind,text,created) VALUES (?,?,?,?)", (tid, kind, text, time.time()))

    def _task(self, c, tid):
        if not tid or not tid.strip():
            raise ValueError("task id required")
        rows = c.execute("SELECT * FROM tasks WHERE substr(id,1,?)=?", (len(tid), tid)).fetchall()
        # IDs only contain hex; don't allow SQL wildcard prefixes.
        rows = [r for r in rows if r["id"].startswith(tid)]
        if len(rows) != 1:
            raise ValueError("unknown or ambiguous task id")
        return rows[0]

    def create_task(self, title, detail=""):
        if not title.strip() or len(title) > 500 or len(detail) > 20000:
            raise ValueError("task needs a title up to 500 chars and detail up to 20000 chars")
        now, tid = time.time(), uuid.uuid4().hex[:12]
        with self.connection() as c:
            c.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?)", (tid, title, detail, "pending", "", "", now, now))
            self._event(c, tid, "created", title)
        return self.get_task(tid)

    def get_task(self, tid):
        with self.connection() as c:
            task = dict(self._task(c, tid))
            task["events"] = [dict(r) for r in c.execute("SELECT kind,text,created FROM events WHERE task_id=? ORDER BY id DESC LIMIT 10", (task["id"],))]
            task["sessions"] = [dict(r) for r in c.execute("SELECT id,state,exit_code,reason FROM sessions WHERE task_id=? ORDER BY created DESC LIMIT 10", (task["id"],))]
            task["artifacts"] = [dict(r) for r in c.execute("SELECT * FROM artifacts WHERE task_id=? ORDER BY created DESC LIMIT 10", (task["id"],))]
            return task

    def update_task(self, tid, state, note="", result="", blocker=""):
        if state not in TASK_STATES:
            raise ValueError("invalid task state")
        if state == "blocked" and not blocker.strip():
            raise ValueError("blocked task needs a clear blocker/question")
        if state == "done" and not result.strip():
            raise ValueError("done task needs a result")
        if any(len(x) > 20000 for x in (note, result, blocker)):
            raise ValueError("task text too long")
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            task = self._task(c, tid)
            if state in TERMINAL and c.execute("SELECT 1 FROM sessions WHERE task_id=? AND state IN ('starting','running')", (task["id"],)).fetchone():
                raise ValueError("task still has an active command; finish or cancel it first")
            c.execute("UPDATE tasks SET state=?,result=?,blocker=?,updated=? WHERE id=?", (
                state, result, blocker, time.time(), task["id"]))
            self._event(c, task["id"], state, note or result or blocker)
            tid = task["id"]
        return self.get_task(tid)

    def list_tasks(self, state="", limit=20):
        if state and state not in TASK_STATES:
            raise ValueError("invalid task state")
        limit = max(1, min(int(limit), 100))
        with self.connection() as c:
            clause, args = ("WHERE state=?", [state]) if state else ("", [])
            count = c.execute(f"SELECT count(*) FROM tasks {clause}", args).fetchone()[0]
            rows = c.execute(f"SELECT * FROM tasks {clause} ORDER BY updated DESC LIMIT ?", args + [limit])
            return {"count": count, "items": [dict(r) for r in rows]}

    def start(self, command, cwd="~", timeout=300, task_id="", idempotency_key=""):
        if not command.strip() or len(command) > 64000:
            raise ValueError("command must be nonempty and at most 64000 chars")
        if not 1 <= int(timeout) <= self.max_timeout:
            raise ValueError(f"timeout must be 1..{self.max_timeout} seconds")
        if len(idempotency_key) > 200:
            raise ValueError("idempotency key too long")
        directory = self.resolve(cwd)
        if not directory.is_dir():
            raise ValueError("cwd must exist and be a directory")
        fingerprint = hashlib.sha256(json.dumps([command, str(directory), int(timeout), task_id]).encode()).hexdigest()
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            if idempotency_key:
                existing = c.execute("SELECT * FROM sessions WHERE idempotency_key=?", (idempotency_key,)).fetchone()
                if existing:
                    if existing["request_hash"] != fingerprint:
                        raise ValueError("idempotency key was used for a different request")
                    return {"session_id": existing["id"], "task_id": existing["task_id"], "state": existing["state"], "reused": True}
            # Refuse new work at the disk budget; never silently delete unread results.
            if sum(p.stat().st_size for p in self.output_dir.iterdir() if p.is_file()) >= 64 * 1024 * 1024:
                raise ValueError("saved output budget reached; purge a reviewed completed session first")
            if c.execute("SELECT count(*) FROM sessions WHERE state IN ('starting','running')").fetchone()[0] >= self.max_sessions:
                raise ValueError("command concurrency limit reached; wait or cancel an active command")
            tid = self._task(c, task_id)["id"] if task_id else None
            if tid:
                task = self._task(c, tid)
                if task["state"] in TERMINAL:
                    raise ValueError("reopen terminal task before starting more work")
                c.execute("UPDATE tasks SET state='running',updated=? WHERE id=?", (time.time(), tid))
            sid, now = uuid.uuid4().hex[:12], time.time()
            c.execute("""INSERT INTO sessions(id,task_id,command,cwd,timeout,state,created,updated,idempotency_key,request_hash)
                         VALUES (?,?,?,?,?,'starting',?,?,?,?)""", (sid, tid, command, str(directory), int(timeout), now, now, idempotency_key or None, fingerprint))
            if tid:
                self._event(c, tid, "command_started", sid)
        thread = threading.Thread(target=self._run, args=(sid,), daemon=True)
        with self._lock:
            self._threads = {k: t for k, t in self._threads.items() if t.is_alive()}
            self._threads[sid] = thread
        thread.start()
        return {"session_id": sid, "task_id": tid, "state": "starting", "reused": False}

    def status(self, sid):
        with self.connection() as c:
            row = c.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
            if row is None:
                raise ValueError("unknown session id")
            result = dict(row)
        result.pop("identity", None)
        result.pop("request_hash", None)
        result["session_id"] = sid
        result["done"] = result["state"] not in SESSION_ACTIVE
        result["stdout_bytes"] = self._size(sid, "stdout")
        result["stderr_bytes"] = self._size(sid, "stderr")
        return result

    def _size(self, sid, stream):
        try:
            return (self.output_dir / f"{sid}.{stream}").stat().st_size
        except FileNotFoundError:
            return 0

    def cancel(self, sid):
        status = self.status(sid)
        if status["done"]:
            return status
        with self.connection() as c:
            c.execute("UPDATE sessions SET cancel_requested=1 WHERE id=?", (sid,))
        return self.status(sid)

    def _run(self, sid):
        process = None
        reason, state, exit_code = "", "failed", None
        overflow = threading.Event()
        reader_errors = []
        budget_lock, used = threading.Lock(), [0]
        def read_pipe(pipe, stream):
            path = self.output_dir / f"{sid}.{stream}"
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "wb", buffering=0) as output:
                    while True:
                        chunk = os.read(pipe.fileno(), 4096)
                        if not chunk:
                            break
                        with budget_lock:
                            remaining = max(0, self.output_limit - used[0])
                            used[0] += len(chunk)
                        output.write(chunk[:remaining])
                        if len(chunk) > remaining:
                            overflow.set()
            except OSError as exc:
                reader_errors.append(str(exc))
                overflow.set()
            finally:
                pipe.close()
        try:
            status = self.status(sid)
            if status["cancel_requested"]:
                state, reason = "cancelled", "cancelled before launch"
                return
            process = subprocess.Popen([self.shell(), "-c", status["command"]], cwd=status["cwd"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            info = lifecycle.identity(process.pid)
            with self.connection() as c:
                c.execute("UPDATE sessions SET state='running',identity=?,updated=? WHERE id=?", (json.dumps(info), time.time(), sid))
            with self._lock:
                self._processes[sid] = process
            readers = [threading.Thread(target=read_pipe, args=(process.stdout, "stdout")),
                       threading.Thread(target=read_pipe, args=(process.stderr, "stderr"))]
            for reader in readers: reader.start()
            end = time.monotonic() + status["timeout"]
            while process.poll() is None:
                if self.status(sid)["cancel_requested"]:
                    state, reason = "cancelled", "cancel requested"
                    break
                if overflow.is_set():
                    state, reason = "output_limit", "combined stdout/stderr limit reached"
                    break
                if time.monotonic() >= end:
                    state, reason = "timed_out", f'timeout after {status["timeout"]}s'
                    break
                time.sleep(0.05)
            if reason:
                self._kill_group(process)
            exit_code = process.wait()
            # Shells may exit leaving a descendant holding the output pipes.
            # Kill the group rather than hang forever or let an untracked job run.
            for reader in readers: reader.join(timeout=0.5)
            if any(r.is_alive() for r in readers):
                self._kill_group(process)
                for reader in readers: reader.join(timeout=2)
                if not reason:
                    state, reason = "failed", "shell exited with background descendants; use a tracked command"
            if not reason:
                if overflow.is_set():
                    state, reason = "output_limit", "combined stdout/stderr limit reached"
                elif reader_errors:
                    state, reason = "failed", "output capture failed"
                else:
                    state = "completed" if exit_code == 0 else "failed"
        except Exception as exc:
            state, reason = "failed", str(exc)[:1000]
            if process:
                self._kill_group(process)
                process.wait()
        finally:
            with self._lock:
                self._processes.pop(sid, None)
            with self.connection() as c:
                c.execute("UPDATE sessions SET state=?,reason=?,exit_code=?,updated=? WHERE id=?", (state, reason, exit_code, time.time(), sid))
                row = c.execute("SELECT task_id FROM sessions WHERE id=?", (sid,)).fetchone()
                text = f"Command {sid}: {state}, exit={exit_code}. {reason}"
                c.execute("INSERT OR IGNORE INTO inbox(session_id,text,created) VALUES (?,?,?)", (sid, text, time.time()))
                if row and row["task_id"]:
                    self._event(c, row["task_id"], "command_finished", text)
                    # Exit 0 is not task completion; the client must verify a result.
                    if state != "completed":
                        task_state = "cancelled" if state == "cancelled" else "failed"
                        c.execute("UPDATE tasks SET state=?,updated=? WHERE id=? AND state='running'", (task_state, time.time(), row["task_id"]))

    def _kill_group(self, process):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def read_output(self, sid, stream="stdout", offset=0, length=8192):
        if stream not in ("stdout", "stderr") or offset < 0 or length <= 0:
            raise ValueError("stream stdout|stderr, offset >= 0 and length > 0 required")
        status = self.status(sid)
        path = self.output_dir / f"{sid}.{stream}"
        try:
            with path.open("rb") as f:
                f.seek(offset)
                chunk = f.read(min(length, 65536))
        except FileNotFoundError:
            chunk = b""
        return {"session_id": sid, "stream": stream, "offset": offset,
            "next_offset": offset + len(chunk), "total_bytes": self._size(sid, stream),
            "done": status["done"], "exit_code": status["exit_code"], "state": status["state"],
            "text": chunk.decode("utf-8", errors="replace")}

    def artifact(self, tid, path, name=""):
        file = self.resolve(path)
        if not file.is_file():
            raise ValueError("artifact must be an existing file")
        digest = hashlib.sha256()
        with file.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                digest.update(chunk)
        aid = uuid.uuid4().hex[:12]
        with self.connection() as c:
            task = self._task(c, tid)
            c.execute("INSERT INTO artifacts VALUES (?,?,?,?,?,?,?)", (
                aid, task["id"], str(file), name or file.name, digest.hexdigest(), file.stat().st_size, time.time()))
            self._event(c, task["id"], "artifact", aid)
        return {"artifact_id": aid, "path": str(file), "name": name or file.name,
                "sha256": digest.hexdigest(), "note": "local file reference; not a hosted download URL"}

    def context(self, limit=10):
        limit = max(1, min(int(limit), 20))
        with self.connection() as c:
            tasks = [dict(r) for r in c.execute("SELECT * FROM tasks WHERE state NOT IN ('done','cancelled') ORDER BY updated DESC LIMIT ?", (limit,))]
            for task in tasks:
                for key in ("detail", "result", "blocker"):
                    task[key] = task[key][:1000]
            return {"tasks": tasks,
                "recent_events": [dict(r) for r in c.execute("SELECT task_id,kind,substr(text,1,1000) AS text,created FROM events ORDER BY id DESC LIMIT ?", (limit,))],
                "unread_results": [dict(r) for r in c.execute("SELECT * FROM inbox WHERE acked=0 ORDER BY id DESC LIMIT ?", (limit,))],
                "recent_tasks": [dict(r) for r in c.execute("SELECT id,title,state,substr(result,1,1000) AS result,updated FROM tasks WHERE state IN ('done','cancelled') ORDER BY updated DESC LIMIT ?", (limit,))],
                "unread_count": c.execute("SELECT count(*) FROM inbox WHERE acked=0").fetchone()[0],
                "artifacts": [dict(r) for r in c.execute("SELECT * FROM artifacts ORDER BY created DESC LIMIT ?", (limit,))]}

    def ack_result(self, result_id):
        with self.connection() as c:
            changed = c.execute("UPDATE inbox SET acked=1 WHERE id=?", (int(result_id),)).rowcount
            return {"ok": bool(changed)}

    def purge_output(self, sid):
        status = self.status(sid)
        if not status["done"]:
            raise ValueError("cannot purge active output")
        with self.connection() as c:
            row = c.execute("SELECT acked FROM inbox WHERE session_id=?", (sid,)).fetchone()
            if row and not row["acked"]:
                raise ValueError("acknowledge the command result before purging output")
        for stream in ("stdout", "stderr"):
            (self.output_dir / f"{sid}.{stream}").unlink(missing_ok=True)
        return {"ok": True, "session_id": sid, "note": "output purged; metadata and events preserved"}

    def close(self):
        # Test/support teardown. A normal restart reconciles persistent rows.
        with self.connection() as c:
            c.execute("UPDATE sessions SET cancel_requested=1 WHERE state IN ('starting','running')")
        with self._lock:
            processes = list(self._processes.values())
            threads = list(self._threads.values())
        for process in processes:
            self._kill_group(process)
        for process in processes:
            process.wait()
        for thread in threads:
            thread.join(timeout=5)
        if any(thread.is_alive() for thread in threads):
            raise RuntimeError("execution thread did not stop; retaining ownership")
        self._owner.close()
