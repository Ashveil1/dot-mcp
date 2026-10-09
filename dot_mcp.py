"""dot-mcp: single-file MCP agent like dots for Termux.
Run:  MCP_AUTH_TOKEN=secret python3 dot_mcp.py
Endpoint: http://127.0.0.1:8088/mcp  (health: /health)
"""
from __future__ import annotations
import base64, hashlib, json, os, shlex, signal, subprocess, tempfile, threading, time, uuid
from pathlib import Path

HOME = Path(os.environ.get("HOME", str(Path.home()))).resolve()
TMPDIR = Path(os.environ.get("TMPDIR", str(HOME / "tmp")))
TMPDIR.mkdir(parents=True, exist_ok=True)
DATA_DIR = Path(os.environ.get("DOT_MCP_DATA", str(HOME / ".dot-mcp")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
MEM_FILE = DATA_DIR / "memory.json"
STATE_FILE = DATA_DIR / "state.json"
INBOX_FILE = DATA_DIR / "inbox.json"
RULES_FILE = DATA_DIR / "rules.json"
SCHED_FILE = DATA_DIR / "schedules.json"
APPROVAL_FILE = DATA_DIR / "approvals.json"
HOST = os.environ.get("MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("MCP_PORT", "8088"))
TOKEN = os.environ.get("MCP_AUTH_TOKEN", "")

def _map_tmp(p: Path) -> Path:
    # Android has no /tmp writable; map to $TMPDIR
    try:
        if str(p).startswith("/tmp/") or str(p) == "/tmp":
            rel = os.path.relpath(str(p), "/tmp")
            return (TMPDIR / rel).resolve() if rel != "." else TMPDIR.resolve()
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
    if not any(str(r).startswith(str(a)) for a in allowed):
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

def save_json(f: Path, obj):
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(f)

# ---- dot-only primitives: inbox + approvals + rules + scheduler ----
# shell ทำเองไม่ได้: การฝากข้อความข้ามแชท, การขออนุมัติก่อนทำ,
# กฎบังคับฝั่งเซิร์ฟเวอร์, งานประจำที่รันเองตอนไม่มีแชท
import re
DATA_LOCK = threading.Lock()

def inbox_push(kind: str, text: str, ref: dict | None = None) -> dict:
    with DATA_LOCK:
        box = load_json(INBOX_FILE, [])
        item = {"id": uuid.uuid4().hex[:8], "ts": time.time(), "kind": kind,
                "text": text, "ref": ref or {}, "acked": False}
        box.append(item); save_json(INBOX_FILE, box[-300:])
    return item

def inbox_list(unacked_only: bool = True, limit: int = 20) -> dict:
    box = load_json(INBOX_FILE, [])
    out = [m for m in box if (not unacked_only or not m.get("acked"))]
    return {"count": len(out), "items": out[-limit:]}

def inbox_ack(iid: str) -> dict:
    with DATA_LOCK:
        box = load_json(INBOX_FILE, [])
        for m in box:
            if m.get("id", "").startswith(iid): m["acked"] = True
        save_json(INBOX_FILE, box)
    return {"ok": True}

def rules_list() -> list:
    return load_json(RULES_FILE, [])

def rule_match(command: str) -> dict | None:
    for r in rules_list():
        try:
            if re.search(r["pattern"], command): return r
        except Exception: pass
    return None

def approval_check(command: str, approval_id: str | None) -> None:
    """Enforce ask/block rules. Block -> deny. Ask -> need granted approval."""
    r = rule_match(command)
    if not r: return
    if r["action"] == "block":
        raise ValueError(f"blocked by rule '{r['name']}': {command[:120]}")
    if r["action"] == "ask" and not approval_id:
        ap = {"id": uuid.uuid4().hex[:8], "ts": time.time(), "command": command,
              "rule": r["name"], "status": "pending"}
        with DATA_LOCK:
            aps = load_json(APPROVAL_FILE, []); aps.append(ap); save_json(APPROVAL_FILE, aps)
        inbox_push("approval", f"needs approval [{ap['id']}] ({r['name']}): {command[:200]}", {"approval_id": ap["id"]})
        raise ValueError(f"needs approval id={ap['id']} (call answer_approval then retry with approval_id)")
    if r["action"] == "ask" and approval_id:
        aps = load_json(APPROVAL_FILE, [])
        ok = any(a.get("id", "").startswith(approval_id) and a.get("status") == "granted"
                 and a.get("command") == command for a in aps)
        if not ok: raise ValueError("approval invalid/expired: re-request without approval_id")

def _sched_loop():
    while True:
        try:
            time.sleep(30)
            now = time.time()
            changed = False
            with DATA_LOCK:
                jobs = load_json(SCHED_FILE, [])
                for j in jobs:
                    if j.get("paused"): continue
                    if now >= j.get("next_run", 0):
                        j["next_run"] = now + int(j.get("interval_seconds", 3600))
                        changed = True
                        cmd, cwd = j.get("command", ""), j.get("cwd", "~")
                        threading.Thread(target=_sched_run, args=(j["id"], cmd, cwd), daemon=True).start()
                if changed: save_json(SCHED_FILE, jobs)
        except Exception:
            time.sleep(30)

def _sched_run(jid: str, cmd: str, cwd: str):
    try:
        d = resolve(cwd or "~")
        p = subprocess.run(["/bin/sh", "-c", cmd], cwd=str(d), capture_output=True, timeout=120)
        out = (p.stdout or b"").decode("utf-8", errors="replace")[-2000:]
        inbox_push("schedule", f"[{jid}] exit={p.returncode}: {out[:1500]}", {"job_id": jid})
    except Exception as e:
        inbox_push("schedule", f"[{jid}] failed: {e}", {"job_id": jid})
    try:
        with DATA_LOCK:
            jobs = load_json(SCHED_FILE, [])
            for j in jobs:
                if j["id"] == jid: j["last_run"] = time.time()
            save_json(SCHED_FILE, jobs)
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

def _run_bg(sid: str, cmd: str, cwd: Path, timeout: int):
    proc = subprocess.Popen(["/bin/sh", "-c", cmd], cwd=str(cwd),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=os.setsid)
    out = {"proc": proc, "stdout": b"", "stderr": b"", "done": False, "code": None, "err": ""}
    with SESSIONS_LOCK:
        SESSIONS[sid] = out
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

# ---- MCP server (v2 MCPServer, fallback v1 FastMCP) ----
try:
    from mcp.server.mcpserver import MCPServer as _Server
    _V2 = True
except Exception:
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore
    _V2 = False

srv = _Server("dot-agent")
instructions = ("Persistent Termux agent (dot-like): shell + files + memory + goals + tasks. "
    "Relative paths resolve from $HOME. Use memory tools to keep context across chats.")
try: srv.instructions = instructions
except Exception: pass

@srv.tool()
def run_command(command: str, cwd: str = "~", timeout: int = 30, approval_id: str | None = None) -> dict:
    """Run shell in Termux. Returns output; if truncated/timeout, continue with read_output."""
    approval_check(command, approval_id)
    d = resolve(cwd if cwd else "~")
    d.mkdir(parents=True, exist_ok=True)
    sid = uuid.uuid4().hex[:12]
    t = threading.Thread(target=_run_bg, args=(sid, command, d, timeout), daemon=True)
    t.start(); t.join(timeout + 5)
    with SESSIONS_LOCK:
        s = SESSIONS.get(sid, {})
    if not s: return {"session_id": sid, "done": False, "text": "starting..."}
    for stream in ("stdout", "stderr"):
        data = s.get(stream, b"")
        if len(data) > TRUNC:
            p = _pack_session(sid, "stdout", 0, TRUNC)
            p["stderr_preview"] = s.get("stderr", b"")[:1000].decode("utf-8", errors="replace")
            p["hint"] = "truncated, use read_output(session_id,...)"
            return p
    return {"session_id": sid, "done": s.get("done"),
        "exit_code": s.get("code"), "error": s.get("err", ""),
        "stdout": s.get("stdout", b"").decode("utf-8", errors="replace"),
        "stderr": s.get("stderr", b"").decode("utf-8", errors="replace")}

@srv.tool()
def read_output(session_id: str, stream: str = "stdout", offset: int = 0, length: int = 8192) -> dict:
    """Continue reading a background command output."""
    return _pack_session(session_id, stream, offset, length)

@srv.tool()
def list_dir(path: str = "~", limit: int = 100) -> dict:
    """List directory."""
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
    seg = txt[lo: lo + limit if limit else None]
    out = "\n".join(f"{i+lo+1}: {l}" if line_numbers else l for i, l in enumerate(seg))
    return {"path": str(p), "sha256": sha256_file(p), "total_lines": total, "text": out}

@srv.tool()
def write_file(path: str, content: str, expected_sha256: str | None = None, create_only: bool = False, approval_id: str | None = None) -> dict:
    """Atomically create/replace one UTF-8 file (+parents). Guards: expected_sha256 / create_only."""
    approval_check(f"write {path}", approval_id)
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
def edit_file(path: str, match_text: str, write_text: str, mode: str = "replace_match", approval_id: str | None = None) -> dict:
    """Single edit: replace_match | insert_before | insert_after. match must be unique."""
    approval_check(f"edit {path}", approval_id)
    p = resolve(path)
    src = p.read_text(encoding="utf-8")
    n = src.count(match_text)
    if n != 1: raise ValueError(f"match found {n}x, need exactly 1")
    if mode == "replace_match": dst = src.replace(match_text, write_text)
    elif mode == "insert_before": dst = src.replace(match_text, write_text + match_text)
    elif mode == "insert_after": dst = src.replace(match_text, match_text + write_text)
    else: raise ValueError("bad mode")
    p.write_text(dst, encoding="utf-8")
    return {"path": str(p), "sha256": sha256_file(p)}

@srv.tool()
def remember(note: str, tag: str = "general") -> dict:
    """Save persistent memory (dot-like notes). Survives across chats. Dedupes identical note+tag."""
    mem = load_json(MEM_FILE, [])
    for m in mem:
        if m.get("note") == note and m.get("tag") == tag: return m
    item = {"id": uuid.uuid4().hex[:8], "ts": time.time(), "tag": tag, "note": note}
    mem.append(item); save_json(MEM_FILE, mem[-500:])
    return item

@srv.tool()
def recall(query: str = "", tag: str = "", limit: int = 20) -> dict:
    """Search persistent memory."""
    mem = load_json(MEM_FILE, [])
    q = query.lower()
    out = [m for m in mem if (not tag or m.get("tag") == tag) and (not q or q in m.get("note", "").lower())]
    return {"count": len(out), "items": out[-limit:]}

@srv.tool()
def forget(mid: str) -> dict:
    """Delete one memory by id."""
    mem = load_json(MEM_FILE, [])
    mem = [m for m in mem if m.get("id") != mid]
    save_json(MEM_FILE, mem)
    return {"ok": True}

@srv.tool()
def set_goal(goal: str) -> dict:
    """Set the dot's current responsibility; progress tracked in get_state."""
    st = load_json(STATE_FILE, {})
    st["goal"] = goal; st["updates"] = []; st["updated"] = time.time()
    save_json(STATE_FILE, st); return st

@srv.tool()
def add_update(text: str) -> dict:
    """Append progress update (dot works between conversations)."""
    st = load_json(STATE_FILE, {})
    st.setdefault("updates", []).append({"ts": time.time(), "text": text})
    st["updated"] = time.time(); save_json(STATE_FILE, st); return st

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
    st = load_json(STATE_FILE, {})
    tasks = st.setdefault("tasks", [])
    t = {"id": uuid.uuid4().hex[:8], "title": title, "detail": detail, "done": False, "ts": time.time()}
    tasks.append(t); save_json(STATE_FILE, st); return t

@srv.tool()
def complete_task(tid: str) -> dict:
    """Mark task done (prefix match on id)."""
    st = load_json(STATE_FILE, {})
    for t in st.get("tasks", []):
        if t["id"].startswith(tid): t["done"] = True
    save_json(STATE_FILE, st); return {"ok": True}

@srv.tool()
def notify_user(text: str, kind: str = "progress") -> dict:
    """Leave a message in the inbox (dot messages you with progress/questions/decisions). Client polls via poll_inbox."""
    return inbox_push(kind, text)

@srv.tool()
def poll_inbox(unacked_only: bool = True, limit: int = 20) -> dict:
    """Read inbox: progress, questions, schedule results, approval requests."""
    return inbox_list(unacked_only, limit)

@srv.tool()
def ack_inbox(iid: str) -> dict:
    """Mark one inbox item read."""
    return inbox_ack(iid)

@srv.tool()
def request_approval(command: str, reason: str = "") -> dict:
    """Ask the user to approve a risky command. Returns approval id; user answers via answer_approval."""
    ap = {"id": uuid.uuid4().hex[:8], "ts": time.time(), "command": command,
          "reason": reason, "rule": "manual", "status": "pending"}
    with DATA_LOCK:
        aps = load_json(APPROVAL_FILE, []); aps.append(ap); save_json(APPROVAL_FILE, aps)
    inbox_push("approval", f"needs approval [{ap['id']}]: {command[:200]} {reason}", {"approval_id": ap["id"]})
    return ap

@srv.tool()
def answer_approval(approval_id: str, granted: bool) -> dict:
    """Grant or deny a pending approval. Granted id is passed back as approval_id to run/write/edit."""
    with DATA_LOCK:
        aps = load_json(APPROVAL_FILE, [])
        for a in aps:
            if a.get("id", "").startswith(approval_id):
                a["status"] = "granted" if granted else "denied"
        save_json(APPROVAL_FILE, aps)
    return {"ok": True, "granted": granted}

@srv.tool()
def add_rule(name: str, pattern: str, action: str = "ask") -> dict:
    """Custom rule enforced server-side: action ask (need approval) or block (deny). pattern is regex on command/path."""
    if action not in ("ask", "block"): raise ValueError("action must be ask|block")
    re.compile(pattern)
    with DATA_LOCK:
        rules = load_json(RULES_FILE, [])
        rules = [r for r in rules if r.get("name") != name]
        r = {"name": name, "pattern": pattern, "action": action}
        rules.append(r); save_json(RULES_FILE, rules)
    return r

@srv.tool()
def list_rules() -> dict:
    """List custom rules."""
    return {"rules": rules_list()}

@srv.tool()
def remove_rule(name: str) -> dict:
    """Delete a rule by name."""
    with DATA_LOCK:
        rules = [r for r in rules_list() if r.get("name") != name]
        save_json(RULES_FILE, rules)
    return {"ok": True}

@srv.tool()
def add_schedule(name: str, command: str, interval_seconds: int = 3600, cwd: str = "~") -> dict:
    """Recurring background job (dot proactive work). Runs even between chats, result goes to inbox."""
    if interval_seconds < 60: raise ValueError("minimum interval 60s")
    with DATA_LOCK:
        jobs = load_json(SCHED_FILE, [])
        j = {"id": uuid.uuid4().hex[:8], "name": name, "command": command, "cwd": cwd,
             "interval_seconds": interval_seconds, "next_run": time.time() + interval_seconds,
             "paused": False, "last_run": None}
        jobs.append(j); save_json(SCHED_FILE, jobs)
    return j

@srv.tool()
def list_schedules() -> dict:
    """List background jobs."""
    return {"jobs": load_json(SCHED_FILE, [])}

@srv.tool()
def remove_schedule(jid: str) -> dict:
    """Delete a background job by id prefix."""
    with DATA_LOCK:
        jobs = [j for j in load_json(SCHED_FILE, []) if not j.get("id", "").startswith(jid)]
        save_json(SCHED_FILE, jobs)
    return {"ok": True}

def _check_auth(scope, receive, send):
    from starlette.responses import JSONResponse
    if not TOKEN: return None
    path = scope.get("path", "")
    if path in ("/health",): return None
    async def app(s, r, se):
        h = {k.decode().lower(): v.decode() for k, v in s.get("headers", [])}
        got = h.get("authorization", "")
        if got.lower().startswith("bearer "): got = got[7:]
        if not got: got = h.get("x-api-key", "")
        if got != TOKEN:
            resp = JSONResponse({"error": "unauthorized"}, status_code=401)
            await resp(s, r, se); return
        from starlette.applications import Starlette as _S
        inner = _inner_app
        await inner(s, r, se)
    return app

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
        app = srv.streamable_http_app(streamable_http_path="/mcp", stateless_http=True,
            **({"transport_security": ts} if ts else {}))
    else:
        app = srv.streamable_http_app()
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    async def health(req): return JSONResponse({"ok": True, "tools": 26, "home": str(HOME)})
    try: app.routes.append(Route("/health", health))
    except Exception: pass
    _inner_app = app
    if TOKEN:
        from starlette.middleware.base import BaseHTTPMiddleware
        class Auth(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                if request.url.path == "/health": return await call_next(request)
                got = request.headers.get("authorization", "")
                if got.lower().startswith("bearer "): got = got[7:]
                if not got: got = request.headers.get("x-api-key", "")
                if got != TOKEN: return JSONResponse({"error": "unauthorized"}, status_code=401)
                return await call_next(request)
        Auth.__name__ = "Auth"
        app.add_middleware(Auth)
    import uvicorn
    sched_ensure()
    print(f"dot-mcp on http://{HOST}:{PORT}/mcp  auth={'on' if TOKEN else 'off(loopback only)'}  data={DATA_DIR}", flush=True)
    uvicorn.run(app, host=HOST, port=PORT)
