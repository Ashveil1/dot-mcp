"""dot-mcp: single-file MCP agent like dots for Termux.
Run:  python3 dot_mcp.py
Endpoint: http://127.0.0.1:33041/mcp/  (health: /health)
Auth on /mcp/: Bearer <JWT from OAuth> OR Bearer <owner secret>.
No header (even loopback) -> 401. Tunnelled traffic looks like
loopback, so there is intentionally NO localhost bypass.
"""
from __future__ import annotations
import base64, hashlib, hmac, json, logging, os, secrets, signal, subprocess, tempfile, threading, time, uuid
from pathlib import Path
from urllib.parse import urlencode

HOME = Path(os.environ.get("HOME", str(Path.home()))).resolve()
TMPDIR = Path(os.environ.get("TMPDIR", str(HOME / "tmp")))
TMPDIR.mkdir(parents=True, exist_ok=True)
DATA_DIR = Path(os.environ.get("DOT_MCP_DATA", str(HOME / ".dot-mcp")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
MEM_FILE = DATA_DIR / "memory.json"
STATE_FILE = DATA_DIR / "state.json"
INBOX_FILE = DATA_DIR / "inbox.json"
SCHED_FILE = DATA_DIR / "schedules.json"
AUDIT_FILE = DATA_DIR / "audit.jsonl"
HOST = os.environ.get("MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("MCP_PORT", "33041"))
# Owner secret: accepted as Bearer credential on /mcp/ (for local scripts)
# and as the consent gate on /authorize. Empty env = ephemeral per-boot secret.
SECRET = os.environ.get("MCP_PATH_SECRET") or secrets.token_urlsafe(32)
MCP_PATH = "/mcp/"

def _map_tmp(p: Path) -> Path:
    # Android has no /tmp writable; map to $TMPDIR.
    # Skip mapping when $TMPDIR itself lives under /tmp (writable already),
    # and never remap paths already inside $TMPDIR.
    try:
        t = TMPDIR.resolve()
        if str(t) == "/tmp" or str(t).startswith("/tmp/"):
            return p
        if str(p) == "/tmp":
            return t
        if str(p).startswith("/tmp/"):
            try:
                if p.resolve().is_relative_to(t):
                    return p
            except Exception:
                if str(p).startswith(str(t).rstrip("/") + "/"):
                    return p
            rel = os.path.relpath(str(p), "/tmp")
            return (t / rel)
    except Exception:
        pass
    return p

def resolve(p: str) -> Path:
    s = os.path.expanduser(p.strip() or ".")
    q = Path(s)
    if not q.is_absolute():
        q = (HOME / q)
    q = _map_tmp(q)
    # safety: stay inside HOME, TMPDIR, or /sdcard if shared storage
    r = q.resolve() if q.exists() else (q.parent.resolve() / q.name if q.parent.exists() else q.absolute())
    allowed = [HOME.resolve(), TMPDIR.resolve()]
    for extra in (Path("/sdcard"), HOME / "storage"):
        try:
            if extra.exists():
                allowed.append(extra.resolve())
        except Exception:
            pass
    def _inside(path: Path, root: Path) -> bool:
        try:
            return path.is_relative_to(root)
        except Exception:
            pass
        try:
            rs, ps = str(root).rstrip("/"), str(path)
            return ps == rs or ps.startswith(rs + "/")
        except Exception:
            return False
    if not any(_inside(r, a) for a in allowed):
        raise ValueError(f"path outside allowed roots ({HOME}): {p}")
    return r

def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for ch in iter(lambda: f.read(65536), b""):
            h.update(ch)
    return h.hexdigest()

def load_json(f: Path, default):
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return default

try:
    import fcntl  # POSIX advisory locks (Linux, macOS, Termux)
    _HAS_FCNTL = True
except Exception:
    fcntl = None  # type: ignore
    _HAS_FCNTL = False

from contextlib import contextmanager

@contextmanager
def _file_lock(f: Path, exclusive: bool):
    """Cross-process file lock. No-op where fcntl is unavailable."""
    if not _HAS_FCNTL:
        yield
        return
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        with open(f.parent / (f.name + ".lock"), "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)
    except Exception:
        yield  # never break serving because locking failed

def save_json(f: Path, obj):
    with _file_lock(f, exclusive=True):
        _write_json(f, obj)

def _write_json(f: Path, obj):
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, f)

def mutate_json(f: Path, default, fn):
    """Atomically load, transform, save. fn(data) -> (new_data, result)."""
    with DATA_LOCK, _file_lock(f, exclusive=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            data = default
        new_data, result = fn(data)
        _write_json(f, new_data)
        return result

# ---- dot-only primitives: inbox + scheduler ----
# shell ทำเองไม่ได้: การฝากข้อความข้ามแชท,
# งานประจำที่รันเองตอนไม่มีแชท
DATA_LOCK = threading.Lock()

def inbox_push(kind: str, text: str, ref: dict | None = None) -> dict:
    def _add(box):
        if not isinstance(box, list):
            box = []
        item = {"id": uuid.uuid4().hex[:8], "ts": time.time(), "kind": kind,
                "text": text, "ref": ref or {}, "acked": False}
        box.append(item)
        return box[-300:], item
    return mutate_json(INBOX_FILE, [], _add)

def inbox_list(unacked_only: bool = True, limit: int = 20) -> dict:
    try:
        limit = int(limit)
    except Exception:
        limit = 20
    limit = max(1, min(limit, 100))
    box = load_json(INBOX_FILE, [])
    out = [m for m in box if (not unacked_only or not m.get("acked"))]
    return {"count": len(out), "items": out[-limit:]}

def inbox_ack(iid: str) -> dict:
    if not (iid or "").strip():
        raise ValueError("empty id: refusing to ack all (pass a real inbox id)")
    def _ack(box):
        if not isinstance(box, list):
            box = []
        for m in box:
            if m.get("id", "").startswith(iid): m["acked"] = True
        return box, {"ok": True}
    return mutate_json(INBOX_FILE, [], _ack)

def _shell() -> str:
    import shutil
    for cand in ("/bin/sh", "/system/bin/sh", os.path.join(os.environ.get("PREFIX", ""), "bin/sh")):
        if cand and os.path.exists(cand):
            return cand
    return shutil.which("sh") or "/bin/sh"

def _sched_loop():
    while True:
        try:
            time.sleep(30)
            now = time.time()
            def _tick(jobs):
                if not isinstance(jobs, list):
                    jobs = []
                changed = False
                for j in jobs:
                    if not isinstance(j, dict):
                        continue
                    if j.get("paused"): continue
                    try:
                        iv = int(j.get("interval_seconds", 3600))
                    except Exception:
                        iv = 3600
                    iv = max(60, iv)
                    if now >= j.get("next_run", 0):
                        j["next_run"] = now + iv
                        changed = True
                        cmd, cwd = j.get("command", ""), j.get("cwd", "~")
                        jid = j.get("id", "?")
                        threading.Thread(target=_sched_run, args=(jid, cmd, cwd), daemon=True).start()
                return jobs, changed
            mutate_json(SCHED_FILE, [], _tick)
        except Exception:
            time.sleep(30)

def _sched_run(jid: str, cmd: str, cwd: str):
    try:
        d = resolve(cwd or "~")
        p = subprocess.run([_shell(), "-c", cmd], cwd=str(d), capture_output=True, timeout=120)
        out = (p.stdout or b"").decode("utf-8", errors="replace")[-2000:]
        inbox_push("schedule", f"[{jid}] exit={p.returncode}: {out[:1500]}", {"job_id": jid})
    except Exception as e:
        inbox_push("schedule", f"[{jid}] failed: {e}", {"job_id": jid})
    try:
        def _mark(jobs):
            if not isinstance(jobs, list):
                jobs = []
            for j in jobs:
                if isinstance(j, dict) and j.get("id") == jid: j["last_run"] = time.time()
            return jobs, None
        mutate_json(SCHED_FILE, [], _mark)
    except Exception: pass

_SCHED_STARTED = False
def sched_ensure():
    global _SCHED_STARTED
    if not _SCHED_STARTED:
        _SCHED_STARTED = True
        threading.Thread(target=_sched_loop, daemon=True).start()

# ---- background shell sessions ----
SESSIONS: dict[str, dict] = {}
SESSIONS_LOCK = threading.Lock()
SESSION_CAP = int(os.environ.get("MCP_SESSION_CAP", "50"))

def _sessions_prune() -> None:
    while len(SESSIONS) > SESSION_CAP:
        done_sid = next((sid for sid, v in SESSIONS.items() if v.get("done")), None)
        sid = done_sid or next(iter(SESSIONS))
        SESSIONS.pop(sid, None)

def _run_bg(sid: str, cmd: str, cwd: Path, timeout: int):
    proc = subprocess.Popen([_shell(), "-c", cmd], cwd=str(cwd),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=os.setsid)
    out = {"proc": proc, "command": cmd, "cwd": str(cwd), "started": time.time(),
           "stdout": b"", "stderr": b"", "done": False, "code": None, "err": ""}
    with SESSIONS_LOCK:
        SESSIONS[sid] = out
        _sessions_prune()
    try:
        so, se = proc.communicate(timeout=timeout)
        out["stdout"], out["stderr"], out["code"], out["done"] = so, se, proc.returncode, True
    except subprocess.TimeoutExpired:
        try: os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception: proc.kill()
        so, se = proc.communicate()
        out["stdout"], out["stderr"] = so, se
        out["code"], out["done"], out["err"] = proc.returncode, True, f"timeout after {timeout}s, continued in session {sid}"

TRUNC = int(os.environ.get("MCP_TRUNC_LIMIT", "8192"))

def _pack_session(sid: str, stream: str, offset: int, length: int) -> dict:
    if stream not in ("stdout", "stderr"):
        raise ValueError("stream must be stdout|stderr")
    if offset < 0:
        raise ValueError("offset must be >= 0")
    if length <= 0:
        raise ValueError("length must be > 0")
    length = min(length, 65536)
    with SESSIONS_LOCK:
        s = SESSIONS.get(sid)
        if not s: raise ValueError("unknown session_id")
        data = s["stdout"] if stream == "stdout" else s["stderr"]
    total = len(data)
    chunk = data[offset:offset + length]
    try: text = chunk.decode("utf-8", errors="replace")
    except Exception: text = base64.b64encode(chunk).decode()
    return {"session_id": sid, "stream": stream, "offset": offset,
            "next_offset": offset + len(chunk), "total_bytes": total,
            "done": s["done"], "exit_code": s["code"], "text": text}

# ---- MCP server (v2 MCPServer + MCP Apps, fallback v1 FastMCP) ----
_APPS = None
try:
    from mcp.server.mcpserver import MCPServer as _Server
    from mcp.server.apps import Apps as _Apps
    _APPS = _Apps()
    _V2 = True
    srv = _Server("dot-agent", extensions=[_APPS])
except Exception:
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore
    _V2 = False
    srv = _Server("dot-agent")

instructions = ("Persistent Termux agent (dot-like): shell + files + memory + goals + tasks. "
    "Relative paths resolve from $HOME. Use memory tools to keep context across chats.")
try: srv.instructions = instructions
except Exception: pass

@srv.tool()
def run_command(command: str, cwd: str = "~", timeout: int = 30) -> dict:
    """Run shell in Termux. Returns output; if truncated/timeout, continue with read_output."""
    d = resolve(cwd if cwd else "~")
    d.mkdir(parents=True, exist_ok=True)
    sid = uuid.uuid4().hex[:12]
    t = threading.Thread(target=_run_bg, args=(sid, command, d, timeout), daemon=True)
    t.start(); t.join(timeout + 5)
    with SESSIONS_LOCK:
        s = SESSIONS.get(sid, {})
    if not s: return {"session_id": sid, "done": False, "text": "starting..."}
    for stream in ("stdout", "stderr"):
        data = s.get(stream, b"") or b""
        if len(data) > TRUNC:
            p = _pack_session(sid, stream, 0, TRUNC)
            other = "stderr" if stream == "stdout" else "stdout"
            p[f"{other}_preview"] = (s.get(other, b"") or b"")[:1000].decode("utf-8", errors="replace")
            p["hint"] = "truncated, use read_output(session_id,...)"
            return p
    return {"session_id": sid, "done": s.get("done"),
        "exit_code": s.get("code"), "error": s.get("err", ""),
        "stdout": (s.get("stdout", b"") or b"").decode("utf-8", errors="replace"),
        "stderr": (s.get("stderr", b"") or b"").decode("utf-8", errors="replace")}

@srv.tool()
def read_output(session_id: str, stream: str = "stdout", offset: int = 0, length: int = 8192) -> dict:
    """Continue reading a background command output."""
    return _pack_session(session_id, stream, offset, length)

@srv.tool()
def list_dir(path: str = "~", limit: int = 100) -> dict:
    """List directory."""
    try:
        limit = int(limit)
    except Exception:
        limit = 100
    limit = max(1, min(limit, 1000))
    d = resolve(path)
    items = [{"name": p.name, "dir": p.is_dir(), "size": p.stat().st_size if p.is_file() else 0} for p in sorted(d.iterdir(), key=lambda x: x.name)[:limit]]
    return {"path": str(d), "items": items}

@srv.tool()
def read_file(path: str, offset: int = 1, limit: int | None = 200, line_numbers: bool = True) -> dict:
    """Read UTF-8 text file paginated (1-based lines). Returns sha256 for guarded writes."""
    p = resolve(path)
    txt = p.read_text(encoding="utf-8", errors="replace").splitlines()
    total = len(txt)
    lo = max(1, offset) - 1
    if limit is None:
        limit = 2000
    try:
        limit = int(limit)
    except Exception:
        limit = 200
    limit = max(1, min(limit, 2000))
    seg = txt[lo: lo + limit]
    out = "\n".join(f"{i+lo+1}: {l}" if line_numbers else l for i, l in enumerate(seg))
    return {"path": str(p), "sha256": sha256_file(p), "total_lines": total, "text": out}

@srv.tool()
def write_file(path: str, content: str, expected_sha256: str | None = None, create_only: bool = False) -> dict:
    """Atomically create/replace one UTF-8 file (+parents). Guards: expected_sha256 / create_only."""
    p = resolve(path)
    if create_only and p.exists(): raise ValueError("exists (create_only)")
    if expected_sha256 and p.exists() and sha256_file(p) != expected_sha256:
        raise ValueError("stale file (sha mismatch), re-read first")
    p.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(p.parent), encoding="utf-8") as f:
        f.write(content); tmp = f.name
    os.replace(tmp, p)
    return {"path": str(p), "sha256": sha256_file(p)}

@srv.tool()
def append_file(path: str, content: str) -> dict:
    """Append text to file (creates parents)."""
    p = resolve(path); p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f: f.write(content)
    return {"path": str(p), "sha256": sha256_file(p)}

@srv.tool()
def edit_file(path: str, match_text: str, write_text: str, mode: str = "replace_match") -> dict:
    """Single edit: replace_match | insert_before | insert_after. match must be unique."""
    p = resolve(path)
    src = p.read_text(encoding="utf-8")
    n = src.count(match_text)
    if n != 1: raise ValueError(f"match found {n}x, need exactly 1")
    if mode == "replace_match": dst = src.replace(match_text, write_text)
    elif mode == "insert_before": dst = src.replace(match_text, write_text + match_text)
    elif mode == "insert_after": dst = src.replace(match_text, match_text + write_text)
    else: raise ValueError("bad mode")
    p.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(p.parent), encoding="utf-8") as f:
        f.write(dst); tmp = f.name
    os.replace(tmp, p)
    return {"path": str(p), "sha256": sha256_file(p)}

@srv.tool()
def remember(note: str, tag: str = "general") -> dict:
    """Save persistent memory (dot-like notes). Survives across chats. Dedupes identical note+tag."""
    def _add(mem):
        if not isinstance(mem, list):
            mem = []
        for m in mem:
            if m.get("note") == note and m.get("tag") == tag: return mem, m
        item = {"id": uuid.uuid4().hex[:8], "ts": time.time(), "tag": tag, "note": note}
        mem.append(item)
        return mem[-500:], item
    return mutate_json(MEM_FILE, [], _add)

@srv.tool()
def recall(query: str = "", tag: str = "", limit: int = 20) -> dict:
    """Search persistent memory."""
    try:
        limit = int(limit)
    except Exception:
        limit = 20
    limit = max(1, min(limit, 100))
    mem = load_json(MEM_FILE, [])
    q = query.lower()
    out = [m for m in mem if (not tag or m.get("tag") == tag) and (not q or q in m.get("note", "").lower())]
    return {"count": len(out), "items": out[-limit:]}

@srv.tool()
def forget(mid: str) -> dict:
    """Delete one memory by id."""
    def _drop(mem):
        if not isinstance(mem, list):
            mem = []
        return [m for m in mem if m.get("id") != mid], {"ok": True}
    return mutate_json(MEM_FILE, [], _drop)

@srv.tool()
def set_goal(goal: str) -> dict:
    """Set the dot's current responsibility; progress tracked in get_state."""
    def _set(st):
        if not isinstance(st, dict):
            st = {}
        st["goal"] = goal; st["updates"] = []; st["updated"] = time.time()
        return st, st
    return mutate_json(STATE_FILE, {}, _set)

@srv.tool()
def add_update(text: str) -> dict:
    """Append progress update (dot works between conversations)."""
    def _add(st):
        if not isinstance(st, dict):
            st = {}
        st.setdefault("updates", []).append({"ts": time.time(), "text": text})
        st["updated"] = time.time()
        return st, st
    return mutate_json(STATE_FILE, {}, _add)

@srv.tool()
def get_state() -> dict:
    """Get goal + recent updates + memory count (dot status)."""
    st = load_json(STATE_FILE, {})
    mem = load_json(MEM_FILE, [])
    st["memory_count"] = len(mem)
    st["home"] = str(HOME); st["data_dir"] = str(DATA_DIR)
    return st

@srv.tool()
def add_task(title: str, detail: str = "") -> dict:
    """Add todo task."""
    def _add(st):
        if not isinstance(st, dict):
            st = {}
        tasks = st.setdefault("tasks", [])
        t = {"id": uuid.uuid4().hex[:8], "title": title, "detail": detail, "done": False, "ts": time.time()}
        tasks.append(t)
        return st, t
    return mutate_json(STATE_FILE, {}, _add)

@srv.tool()
def complete_task(tid: str) -> dict:
    """Mark task done (prefix match on id)."""
    if not (tid or "").strip():
        raise ValueError("empty id: refusing to complete all tasks")
    def _done(st):
        if not isinstance(st, dict):
            st = {}
        matched = 0
        for t in st.get("tasks", []):
            if isinstance(t, dict) and t.get("id", "").startswith(tid):
                t["done"] = True
                matched += 1
        return st, {"ok": True, "matched": matched}
    return mutate_json(STATE_FILE, {}, _done)

@srv.tool()
def notify_user(text: str, kind: str = "progress") -> dict:
    """Leave a message in the inbox (dot messages you with progress/questions/decisions). Client polls via poll_inbox."""
    return inbox_push(kind, text)

@srv.tool()
def poll_inbox(unacked_only: bool = True, limit: int = 20) -> dict:
    """Read inbox: progress, questions, schedule results."""
    return inbox_list(unacked_only, limit)

@srv.tool()
def ack_inbox(iid: str) -> dict:
    """Mark one inbox item read."""
    return inbox_ack(iid)

@srv.tool()
def add_schedule(name: str, command: str, interval_seconds: int = 3600, cwd: str = "~") -> dict:
    """Recurring background job (dot proactive work). Runs even between chats, result goes to inbox."""
    if interval_seconds < 60: raise ValueError("minimum interval 60s")
    def _add(jobs):
        if not isinstance(jobs, list):
            jobs = []
        j = {"id": uuid.uuid4().hex[:8], "name": name, "command": command, "cwd": cwd,
             "interval_seconds": interval_seconds, "next_run": time.time() + interval_seconds,
             "paused": False, "last_run": None}
        jobs.append(j)
        return jobs, j
    return mutate_json(SCHED_FILE, [], _add)

@srv.tool()
def list_schedules() -> dict:
    """List background jobs."""
    return {"jobs": load_json(SCHED_FILE, [])}

@srv.tool()
def remove_schedule(jid: str) -> dict:
    """Delete a background job by id prefix."""
    if not (jid or "").strip():
        raise ValueError("empty id: refusing to remove all schedules")
    def _drop(jobs):
        if not isinstance(jobs, list):
            jobs = []
        return [j for j in jobs if not (isinstance(j, dict) and j.get("id", "").startswith(jid))], {"ok": True}
    return mutate_json(SCHED_FILE, [], _drop)

# ---- Minimal single-user OAuth 2.1 AS (DCR + PKCE) for ChatGPT/Claude/Gemini ----
# Free, self-hosted: this server is both authorization server and resource server.
# Consent is gated by the path SECRET (holder == owner). Capability-URL access
# (no Bearer header) keeps working for local scripts.
OAUTH_FILE = DATA_DIR / "oauth.json"
OAUTH_SCOPE = "dots:full"

def _oauth_state() -> dict:
    st = load_json(OAUTH_FILE, {})
    if not isinstance(st, dict):
        st = {}
    st.setdefault("clients", {})
    st.setdefault("codes", {})
    st.setdefault("refresh", {})
    return st

def _oauth_ensure() -> dict:
    """Ensure as_secret + maps exist (atomic; safe at first boot)."""
    def _mk(st):
        if not isinstance(st, dict):
            st = {}
        if not st.get("as_secret"):
            st["as_secret"] = secrets.token_urlsafe(48)
        st.setdefault("clients", {})
        st.setdefault("codes", {})
        st.setdefault("refresh", {})
        return st, st
    return mutate_json(OAUTH_FILE, {}, _mk)

def _oauth_save(st: dict) -> None:
    save_json(OAUTH_FILE, st)
    _oauth_chmod()

def _oauth_chmod() -> None:
    try:
        os.chmod(OAUTH_FILE, 0o600)  # holds refresh tokens + client secret
    except Exception:
        pass

def _oauth_static_client() -> dict:
    """Pre-provisioned confidential client for connectors with manual
    Client ID/Secret fields (no DCR). Stable per machine. Secret shown
    by `dot-mcp start`; consent page still gates code issuance."""
    _oauth_ensure()
    def _mk(st):
        sc = st.get("static_client")
        if not isinstance(sc, dict) or not sc.get("client_id") or not sc.get("client_secret"):
            sc = {"client_id": "dots-" + uuid.uuid4().hex[:8],
                  "client_secret": secrets.token_urlsafe(32),
                  "created": time.time()}
            st["static_client"] = sc
        return st, st["static_client"]
    return mutate_json(OAUTH_FILE, {}, _mk)

def _oauth_find_client(st: dict, cid: str) -> tuple[dict | None, bool]:
    """Return (record, is_static). Static client allows any https redirect (owner-only)."""
    if isinstance(cid, str) and cid:
        sc = st.get("static_client")
        if isinstance(sc, dict) and hmac.compare_digest(cid, str(sc.get("client_id", ""))):
            return sc, True
        cl = st.get("clients", {}).get(cid)
        if isinstance(cl, dict):
            return cl, False
    return None, False

def _issuer() -> str:
    pub = [x.strip() for x in os.environ.get("MCP_PUBLIC_HOST", "").split(",") if x.strip()]
    if pub:
        return "https://" + pub[0].split("://")[-1].split("/")[0]
    return f"http://{HOST}:{PORT}"

def _resource_id() -> str:
    # Canonical resource id (no secret inside — tokens must not leak the URL).
    return _issuer() + "/mcp"

def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

def _b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))

def _jwt_issue(secret: str, client_id: str, scope: str, ttl: int = 3600) -> str:
    now = int(time.time())
    h = _b64u(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    p = _b64u(json.dumps({"iss": _issuer(), "aud": _resource_id(), "sub": "owner",
        "client_id": client_id, "scope": scope, "iat": now, "exp": now + ttl,
        "jti": uuid.uuid4().hex[:12]}, separators=(",", ":")).encode())
    s = _b64u(hmac.new(secret.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest())
    return f"{h}.{p}.{s}"

def _jwt_verify(token: str) -> dict | None:
    try:
        h, p, s = token.split(".")
        st = _oauth_state()
        want = _b64u(hmac.new(st["as_secret"].encode(), f"{h}.{p}".encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(want, s):
            return None
        claims = json.loads(_b64u_dec(p))
        if int(claims.get("exp", 0)) < int(time.time()):
            return None
        if claims.get("iss") != _issuer() or claims.get("aud") != _resource_id():
            return None
        if OAUTH_SCOPE not in (claims.get("scope") or "").split():
            return None
        return claims
    except Exception:
        return None

def _oauth_metadata() -> dict:
    iss = _issuer()
    return {
        "issuer": iss,
        "authorization_endpoint": iss + "/authorize",
        "token_endpoint": iss + "/token",
        "registration_endpoint": iss + "/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": [OAUTH_SCOPE],
    }

def _oauth_protected_resource() -> dict:
    return {
        "resource": _resource_id(),
        "authorization_servers": [_issuer()],
        "scopes_supported": [OAUTH_SCOPE],
        "bearer_methods_supported": ["header"],
    }

def _valid_redirect(uri: str) -> bool:
    try:
        if uri.startswith("https://"):
            return True
        if uri.startswith("http://localhost") or uri.startswith("http://127.0.0.1"):
            return True
    except Exception:
        pass
    return False

async def _oauth_meta_req(req):
    from starlette.responses import JSONResponse
    return JSONResponse(_oauth_metadata())

async def _oauth_pr_req(req):
    from starlette.responses import JSONResponse
    return JSONResponse(_oauth_protected_resource())

async def _oauth_register_req(req):
    from starlette.responses import JSONResponse
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    uris = body.get("redirect_uris") if isinstance(body, dict) else None
    if not isinstance(uris, list) or not uris or not all(isinstance(u, str) and _valid_redirect(u) for u in uris):
        return JSONResponse({"error": "invalid_redirect_uri"}, status_code=400)
    cid = "c-" + uuid.uuid4().hex[:12]
    def _add(st):
        if not isinstance(st, dict):
            st = {}
        st.setdefault("clients", {})[cid] = {"redirect_uris": uris,
            "name": str(body.get("client_name", ""))[:80], "created": time.time()}
        return st, cid
    mutate_json(OAUTH_FILE, {}, _add)
    _oauth_chmod()
    return JSONResponse({"client_id": cid, "client_id_issued_at": int(time.time()),
        "redirect_uris": uris, "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"], "token_endpoint_auth_method": "none",
        "scope": OAUTH_SCOPE}, status_code=201)

async def _oauth_authorize_req(req):
    import html as _html
    from starlette.responses import JSONResponse, HTMLResponse, RedirectResponse
    if req.method == "GET":
        q = req.query_params
        st = _oauth_state()
        cl, static = _oauth_find_client(st, q.get("client_id", ""))
        ruri = q.get("redirect_uri", "")
        ok_uri = (ruri.startswith("https://") if static
                  else bool(cl and ruri in cl.get("redirect_uris", [])))
        if not cl or not ok_uri:
            return JSONResponse({"error": "invalid_request",
                "hint": "unknown client or redirect_uri — POST /register first"}, status_code=400)
        if q.get("response_type") != "code":
            return JSONResponse({"error": "unsupported_response_type"}, status_code=400)
        if not static and (not q.get("code_challenge") or q.get("code_challenge_method", "S256") != "S256"):
            return JSONResponse({"error": "invalid_request",
                "hint": "PKCE S256 code_challenge required for dynamic clients"}, status_code=400)
        scope = q.get("scope", OAUTH_SCOPE)
        hidden = "".join(f'<input type="hidden" name="{k}" value="{_html.escape(q.get(k, ""))}">'
            for k in ("client_id", "redirect_uri", "code_challenge", "scope", "state", "resource"))
        page = ("<!doctype html><html><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>Authorize dot-mcp</title></head>"
            "<body style='font-family:system-ui;max-width:520px;margin:40px auto;padding:0 16px'>"
            "<h2>Authorize dot-mcp</h2>"
            f"<p>Client <b>{_html.escape(cl.get('name') or q.get('client_id', ''))}</b> requests scope <code>{_html.escape(scope)}</code>.</p>"
            f"<form method=post>{hidden}<label>Owner secret (path secret):<br>"
            "<input type=password name=owner_secret style='width:100%;padding:8px' autocomplete=off></label><br><br>"
            "<button type=submit style='padding:10px 20px'>Approve</button></form></body></html>")
        return HTMLResponse(page)
    form = await req.form()
    g = lambda k: str(form.get(k, ""))
    origin = req.headers.get("origin", "") or req.headers.get("referer", "")
    if origin:
        try:
            from urllib.parse import urlparse
            oh = (urlparse(origin).hostname or "").lower()
            ih = (urlparse(_issuer()).hostname or "").lower()
            if oh and ih and oh != ih:
                return JSONResponse({"error": "invalid_request"}, status_code=403)
        except Exception:
            pass
    st = _oauth_state()
    cl, static = _oauth_find_client(st, g("client_id"))
    ruri = g("redirect_uri")
    ok_uri = (ruri.startswith("https://") if static
              else bool(cl and ruri in (cl.get("redirect_uris", []) if cl else [])))
    if not cl or not ok_uri:
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    if not hmac.compare_digest(g("owner_secret").strip(), SECRET):
        return HTMLResponse("<h3>Wrong owner secret.</h3>", status_code=403)
    code = secrets.token_urlsafe(32)
    now = time.time()
    def _addcode(st):
        if not isinstance(st, dict):
            st = {}
        codes = st.setdefault("codes", {})
        for k in [k for k, v in codes.items() if v.get("exp", 0) <= now]:
            codes.pop(k, None)
        codes[code] = {"client_id": g("client_id"), "redirect_uri": g("redirect_uri"),
            "challenge": g("code_challenge"), "scope": g("scope") or OAUTH_SCOPE, "exp": now + 600}
        return st, None
    mutate_json(OAUTH_FILE, {}, _addcode)
    _oauth_chmod()
    sep = "&" if "?" in g("redirect_uri") else "?"
    params = {"code": code}
    if g("state"):
        params["state"] = g("state")
    return RedirectResponse(g("redirect_uri") + sep + urlencode(params), status_code=302)

async def _oauth_token_req(req):
    from starlette.responses import JSONResponse
    try:
        form = await req.form()
    except Exception:
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    g = lambda k: str(form.get(k, ""))
    now = time.time()
    presented = g("client_secret")
    if not presented:
        auth = req.headers.get("authorization", "")
        if auth.lower().startswith("basic "):
            try:
                presented = base64.b64decode(auth[6:].strip()).decode().partition(":")[2]
            except Exception:
                presented = ""

    def _client_ok(st: dict, cid: str) -> str | None:
        """Verify client for token use. Returns 'static' | 'public' | None (reject)."""
        rec, static = _oauth_find_client(st, cid)
        if not rec:
            return None
        if not static:
            return "public"  # DCR public client: PKCE is the credential
        if presented and hmac.compare_digest(presented, str(rec.get("client_secret", ""))):
            return "static"
        return None

    def _pkce_ok(rec: dict, verifier: str) -> bool:
        chal = rec.get("challenge") or ""
        if not chal:
            return True  # manual confidential client without PKCE
        try:
            digest = _b64u(hashlib.sha256(verifier.encode()).digest())
        except Exception:
            return False
        return bool(digest) and hmac.compare_digest(digest, chal)

    def _prune_refresh(st: dict, now_: float) -> dict:
        refresh = st.setdefault("refresh", {})
        for k in [k for k, v in refresh.items() if v.get("exp", 0) <= now_]:
            refresh.pop(k, None)
        return refresh

    if g("grant_type") == "authorization_code":
        code_in, ruri_in, verifier = g("code"), g("redirect_uri"), g("code_verifier")
        def _exchange(st):
            if not isinstance(st, dict):
                st = {}
            rec = st.setdefault("codes", {}).pop(code_in, None)
            if not rec or rec.get("exp", 0) < now:
                return st, ("invalid_grant", 400, None)
            if ruri_in != rec.get("redirect_uri"):
                return st, ("invalid_grant", 400, None)
            if _client_ok(st, rec.get("client_id", "")) is None:
                return st, ("invalid_client", 401, None)
            if not _pkce_ok(rec, verifier):
                return st, ("invalid_grant", 400, None)
            cid, scope = rec.get("client_id", ""), rec.get("scope") or OAUTH_SCOPE
            ref = secrets.token_urlsafe(32)
            _prune_refresh(st, now)[hashlib.sha256(ref.encode()).hexdigest()] = {
                "client_id": cid, "scope": scope, "exp": now + 30 * 86400}
            access = _jwt_issue(st.get("as_secret", ""), cid, scope)
            return st, (None, 200, {"access_token": access, "token_type": "Bearer",
                "expires_in": 3600, "scope": scope, "refresh_token": ref})
        _, out = mutate_json(OAUTH_FILE, {}, _exchange)
        _oauth_chmod()
        err, status, payload = out
        if err:
            return JSONResponse({"error": err}, status_code=status)
        return JSONResponse(payload)
    if g("grant_type") == "refresh_token":
        ref_in, cid_in = g("refresh_token"), g("client_id")
        def _rotate(st):
            if not isinstance(st, dict):
                st = {}
            key = hashlib.sha256(ref_in.encode()).hexdigest() if ref_in else ""
            rec = st.setdefault("refresh", {}).pop(key, None)
            if not rec or rec.get("exp", 0) < now:
                return st, ("invalid_grant", 400, None)
            if cid_in and cid_in != rec.get("client_id", ""):
                return st, ("invalid_grant", 400, None)
            if _client_ok(st, rec.get("client_id", "")) is None:
                return st, ("invalid_client", 401, None)
            cid, scope = rec.get("client_id", ""), rec.get("scope") or OAUTH_SCOPE
            ref = secrets.token_urlsafe(32)
            _prune_refresh(st, now)[hashlib.sha256(ref.encode()).hexdigest()] = {
                "client_id": cid, "scope": scope, "exp": now + 30 * 86400}
            access = _jwt_issue(st.get("as_secret", ""), cid, scope)
            return st, (None, 200, {"access_token": access, "token_type": "Bearer",
                "expires_in": 3600, "scope": scope, "refresh_token": ref})
        _, out = mutate_json(OAUTH_FILE, {}, _rotate)
        _oauth_chmod()
        err, status, payload = out
        if err:
            return JSONResponse({"error": err}, status_code=status)
        return JSONResponse(payload)
    return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

# ---- Optional Codex-inspired embedded workspace UI (MCP Apps / v2 SDK) ----
def open_workspace() -> str:
    """Open a Codex-inspired dashboard for Dots tasks, memory, sessions and schedules."""
    state = load_json(STATE_FILE, {})
    memory = load_json(MEM_FILE, [])
    inbox = load_json(INBOX_FILE, [])
    schedules = load_json(SCHED_FILE, [])
    with SESSIONS_LOCK:
        sessions = [{"session_id": sid, "command": v.get("command", ""),
                     "done": bool(v.get("done")), "exit_code": v.get("code")}
                    for sid, v in SESSIONS.items()]
    tasks = state.get("tasks", []) if isinstance(state, dict) else []
    memory = memory if isinstance(memory, list) else []
    inbox = inbox if isinstance(inbox, list) else []
    snapshot = {
        "home": str(HOME), "data_dir": str(DATA_DIR),
        "goal": state.get("goal", "") if isinstance(state, dict) else "",
        "updates": state.get("updates", [])[-8:] if isinstance(state, dict) else [],
        "tasks": tasks[-50:], "open_tasks": sum(1 for t in tasks if isinstance(t, dict) and not t.get("done")),
        "memory": memory[-30:], "memory_count": len(memory),
        "unread_inbox": sum(1 for m in inbox if isinstance(m, dict) and not m.get("acked")),
        "sessions": sessions[-20:], "active_sessions": sum(1 for x in sessions if not x["done"]),
        "schedules": schedules,
    }
    return json.dumps({"workspace": snapshot}, ensure_ascii=False)

_UI_URI = "ui://dot-agent/workspace.html"
if _APPS is not None:
    try:
        _html = (Path(__file__).resolve().parent / "ui" / "workspace.html").read_text(encoding="utf-8")
    except Exception:
        _html = None
    try:
        _APPS.tool(resource_uri=_UI_URI, name="open_workspace", title="Open Dots Workspace",
                   description="Open a Codex-inspired dashboard for Dots tasks, memory, sessions and schedules.")(open_workspace)
        if _html is not None:
            _APPS.add_html_resource(_UI_URI, _html, title="Dots Workspace")
        # The server was created before this module's tools were declared, so attach the app contributions explicitly.
        srv.add_tool(open_workspace, name="open_workspace", title="Open Dots Workspace",
                     description="Open a Codex-inspired dashboard for Dots tasks, memory, sessions and schedules.",
                     meta={"ui": {"resourceUri": _UI_URI}})
        for _resource_binding in _APPS.resources():
            srv.add_resource(_resource_binding.resource)
    except Exception:
        srv.tool(name="open_workspace")(open_workspace)
else:
    # Older MCP SDKs: text-only fallback with the canonical name.
    srv.tool(name="open_workspace")(open_workspace)

# ---- Rate limiting + audit log (per-process, in-memory buckets) ----
RATE_MCP_PER_MIN = int(os.environ.get("MCP_RATE_MCP_PER_MIN", "300"))
RATE_AUTH_PER_MIN = int(os.environ.get("MCP_RATE_AUTH_PER_MIN", "30"))
_RATE: dict[tuple[str, str], list[float]] = {}
_RATE_LOCK = threading.Lock()

def _rate_ok(bucket: str, ip: str, limit: int) -> bool:
    now = time.time()
    key = (bucket, ip)
    with _RATE_LOCK:
        hits = [t for t in _RATE.get(key, []) if now - t < 60]
        if len(hits) >= max(1, limit):
            _RATE[key] = hits
            return False
        hits.append(now)
        _RATE[key] = hits
        if len(_RATE) > 5000:  # bound memory: drop idle buckets
            for k in [k for k, v in _RATE.items() if not v or now - v[-1] > 300]:
                _RATE.pop(k, None)
        return True

def _audit(ip: str, method: str, path: str, status: int, tool: str = "", ms: int = 0):
    try:
        line = json.dumps({"ts": time.time(), "ip": ip, "method": method,
                           "path": path, "status": status, "tool": tool,
                           "ms": ms}, ensure_ascii=False)
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] in ("--list-tools", "--check"):
        tools = []
        try:
            for t in __import__("asyncio").run(srv.list_tools()):
                tools.append(getattr(t, "name", str(t)))
        except Exception as e:
            tools = [f"<list failed: {e}>"]
        print(json.dumps(tools, indent=2)); raise SystemExit(0)
    if _V2:
        # DNS rebinding protection: loopback + optional public tunnel host(s)
        ts_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
        ts_origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
        for h in [x.strip() for x in os.environ.get("MCP_PUBLIC_HOST", "").split(",") if x.strip()]:
            h = h.split("://")[-1].split("/")[0]
            ts_hosts.append(h); ts_origins.append(f"https://{h}")
        try:
            from mcp.server.transport_security import TransportSecuritySettings
            ts = TransportSecuritySettings(enable_dns_rebinding_protection=True,
                allowed_hosts=ts_hosts, allowed_origins=ts_origins)
        except Exception:
            ts = None
        app = srv.streamable_http_app(streamable_http_path=MCP_PATH, stateless_http=True,
            **({"transport_security": ts} if ts else {}))
    else:
        app = srv.streamable_http_app()
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    async def health(req):
        try:
            tools = await srv.list_tools()
            n = len(tools)
        except Exception:
            n = 22
        return JSONResponse({"ok": True, "tools": n, "home": str(HOME)})
    try: app.routes.append(Route("/health", health))
    except Exception: pass
    for _route in (
        Route("/.well-known/oauth-authorization-server", _oauth_meta_req),
        Route("/.well-known/oauth-protected-resource", _oauth_pr_req),
        Route("/register", _oauth_register_req, methods=["POST"]),
        Route("/authorize", _oauth_authorize_req, methods=["GET", "POST"]),
        Route("/token", _oauth_token_req, methods=["POST"]),
    ):
        try: app.routes.append(_route)
        except Exception: pass
    from starlette.middleware.base import BaseHTTPMiddleware
    class OAuthBearer(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            t0 = time.time()
            path = request.url.path
            ip = request.client.host if request.client else "?"
            tool = ""
            if path.startswith("/mcp"):
                if not _rate_ok("mcp", ip, RATE_MCP_PER_MIN):
                    _audit(ip, request.method, path, 429, "", 0)
                    return JSONResponse({"error": "rate_limited"}, status_code=429)
                if request.method == "POST":
                    try:
                        payload = json.loads(await request.body())
                        if isinstance(payload, dict) and payload.get("method") == "tools/call":
                            tool = str((payload.get("params") or {}).get("name", ""))[:64]
                    except Exception:
                        pass
                got = request.headers.get("authorization", "")
                token = got[7:].strip() if got.lower().startswith("bearer ") else ""
                ok = bool(token) and hmac.compare_digest(token, SECRET)
                if not ok and not (token and _jwt_verify(token)):
                    _audit(ip, request.method, path, 401, tool, int((time.time() - t0) * 1000))
                    return JSONResponse({"error": "unauthorized"}, status_code=401)
            elif path in ("/authorize", "/token", "/register") and request.method == "POST":
                if not _rate_ok("auth", ip, RATE_AUTH_PER_MIN):
                    _audit(ip, request.method, path, 429, "", 0)
                    return JSONResponse({"error": "rate_limited"}, status_code=429)
            try:
                resp = await call_next(request)
            except Exception:
                _audit(ip, request.method, path, 500, tool, int((time.time() - t0) * 1000))
                raise
            _audit(ip, request.method, path, resp.status_code, tool, int((time.time() - t0) * 1000))
            return resp
    OAuthBearer.__name__ = "OAuthBearer"
    app.add_middleware(OAuthBearer)
    import uvicorn
    sched_ensure()
    _oauth_static_client()  # ensure manual-connector credentials exist
    # Silence per-connection INFO spam from the MCP SDK ("Terminating session: ...").
    logging.getLogger("mcp.server").setLevel(logging.WARNING)
    logging.getLogger("uvicorn").setLevel(logging.ERROR)
    logging.getLogger("uvicorn.error").setLevel(logging.ERROR)
    logging.getLogger("uvicorn.access").setLevel(logging.ERROR)
    uvicorn.run(app, host=HOST, port=PORT, access_log=False, log_level="error")
