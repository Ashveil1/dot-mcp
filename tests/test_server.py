"""Production test suite for dot-mcp. Run:  .venv/bin/python -m pytest tests/ -q

Isolates ALL state: fresh HOME/DOT_MCP_DATA/TMPDIR per session, so the real
~/.dot-mcp is never touched.
"""
import base64
import hashlib
import importlib
import json
import os
import sys
import tempfile
import threading

import pytest

_TMP = tempfile.mkdtemp(prefix="dotmcp-test-")
os.environ["HOME"] = _TMP
os.environ["DOT_MCP_DATA"] = os.path.join(_TMP, ".dot-mcp")
os.environ["TMPDIR"] = os.path.join(_TMP, "tmp")
os.environ["MCP_PATH_SECRET"] = "test-owner-secret"
os.makedirs(os.environ["TMPDIR"], exist_ok=True)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import dot_mcp.server as srv

importlib.reload(srv)


@pytest.fixture()
def data_dir(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    return d


# ---- path safety ----

def test_resolve_blocks_absolute_escape():
    with pytest.raises(ValueError):
        srv.resolve("/etc/passwd")


def test_resolve_blocks_sibling_prefix():
    # /home/u-evil must not pass as inside /home/u
    evil = str(srv.HOME) + "-evil/x"
    with pytest.raises(ValueError):
        srv.resolve(evil)


def test_resolve_allows_home():
    p = srv.resolve("somefile.txt")
    assert str(p).startswith(str(srv.HOME))


def test_map_tmp_no_double_mapping():
    # TMPDIR under /tmp must not be remapped recursively
    from pathlib import Path
    out = srv._map_tmp(Path("/tmp/foo"))
    assert str(out).count("tmp") <= 3


# ---- empty-id guards (must never wipe whole tables) ----

def test_inbox_ack_empty_rejected():
    srv.inbox_push("test", "hello")
    with pytest.raises(ValueError):
        srv.inbox_ack("")
    assert srv.inbox_list(True, 10)["count"] >= 1


def test_complete_task_empty_rejected():
    srv.add_task("t1")
    srv.add_task("t2")
    with pytest.raises(ValueError):
        srv.complete_task("  ")
    st = srv.load_json(srv.STATE_FILE, {})
    assert all(not t["done"] for t in st["tasks"])


def test_remove_schedule_empty_rejected():
    srv.add_schedule("s1", "echo hi", 3600)
    with pytest.raises(ValueError):
        srv.remove_schedule("")
    assert len(srv.load_json(srv.SCHED_FILE, [])) >= 1


# ---- session packing validation ----

def test_pack_rejects_bad_stream():
    srv.SESSIONS["probe"] = {"stdout": b"A", "stderr": b"B", "done": True, "code": 0}
    with pytest.raises(ValueError):
        srv._pack_session("probe", "bogus", 0, 10)
    with pytest.raises(ValueError):
        srv._pack_session("probe", "stdout", -1, 10)
    with pytest.raises(ValueError):
        srv._pack_session("probe", "stdout", 0, 0)


# ---- atomic storage: no lost updates under threads ----

def test_mutate_json_concurrent_no_loss(data_dir):
    f = data_dir / "c.json"
    f.write_text("[]")
    srv2_state = {"file": f}

    def worker(n):
        def _add(box):
            box.append(n)
            return box, None
        srv.mutate_json(f, [], _add)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    got = json.loads(f.read_text())
    assert sorted(got) == list(range(50)), f"lost updates: {len(got)}/50"


def test_remember_dedupes():
    a = srv.remember("dup-note-xyz", "t")
    b = srv.remember("dup-note-xyz", "t")
    assert a["id"] == b["id"]


def test_empty_inputs_rejected():
    for fn in (lambda: srv.remember("  "),
               lambda: srv.notify_user(""),
               lambda: srv.add_update("  "),
               lambda: srv.add_task("   "),
               lambda: srv.add_schedule("  ", "echo x"),
               lambda: srv.add_schedule("n", "   ")):
        with pytest.raises(ValueError):
            fn()


def test_add_schedule_enforces_minimum():
    with pytest.raises(ValueError):
        srv.add_schedule("fast", "echo", 5)


# ---- OAuth crypto ----

def test_jwt_roundtrip_and_tamper():
    st = srv._oauth_ensure()
    tok = srv._jwt_issue(st["as_secret"], "c-1", "dots:full", ttl=60)
    assert srv._jwt_verify(tok) is not None
    h, p, s = tok.split(".")
    bad = h + "." + p + "." + ("A" if s[0] != "A" else "B") + s[1:]  # guaranteed changed signature char
    assert srv._jwt_verify(bad) is None
    assert srv._jwt_verify("not.a.jwt") is None


def test_jwt_wrong_audience_rejected():
    st = srv._oauth_ensure()
    tok = srv._jwt_issue(st["as_secret"], "c-1", "dots:full", ttl=60)
    h, p, s = tok.split(".")
    claims = json.loads(base64.urlsafe_b64decode(p + "=="))
    claims["aud"] = "https://evil.example/mcp"
    import hmac as _hmac
    p2 = base64.urlsafe_b64encode(json.dumps(claims, separators=(",", ":")).encode()).rstrip(b"=").decode()
    s2 = base64.urlsafe_b64encode(_hmac.new(st["as_secret"].encode(), f"{h}.{p2}".encode(),
                                            hashlib.sha256).digest()).rstrip(b"=").decode()
    assert srv._jwt_verify(f"{h}.{p2}.{s2}") is None


def test_pkce_vector():
    # RFC 7636 Appendix B
    v = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    digest = srv._b64u(hashlib.sha256(v.encode()).digest())
    assert digest == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_valid_redirect():
    assert srv._valid_redirect("https://x.test/cb")
    assert srv._valid_redirect("http://localhost:9999/cb")
    assert not srv._valid_redirect("http://evil.example/cb")
    assert not srv._valid_redirect("ftp://x/cb")


def test_resource_id_has_no_secret():
    rid = srv._resource_id()
    assert "test-owner-secret" not in rid
    assert rid.endswith("/mcp")


# ---- regression: every tool function must exist (catches bad refactors) ----

EXPECTED_TOOLS = ["run_command", "read_output", "list_dir", "read_file",
    "write_file", "append_file", "edit_file", "remember", "recall", "forget",
    "set_goal", "add_update", "get_state", "add_task", "complete_task",
    "notify_user", "poll_inbox", "ack_inbox", "add_schedule",
    "list_schedules", "remove_schedule", "open_workspace",
    "inbox_push", "inbox_list", "inbox_ack"]


def test_all_tool_functions_exist():
    missing = [n for n in EXPECTED_TOOLS if not callable(getattr(srv, n, None))]
    assert not missing, f"missing functions: {missing}"


# ---- rate limiter ----

def test_rate_limiter_blocks_then_allows():
    srv._RATE.clear()
    assert all(srv._rate_ok("probe", "1.2.3.4", 3) for _ in range(3))
    assert not srv._rate_ok("probe", "1.2.3.4", 3)
    assert srv._rate_ok("probe", "5.6.7.8", 3)  # per-IP buckets

# ---- 0.2.2 audited regressions ----

def test_set_goal_preserves_update_history(monkeypatch, tmp_path):
    monkeypatch.setattr(srv, "STATE_FILE", tmp_path / "state.json")
    srv.add_update("before first goal")
    first = srv.set_goal("one")
    srv.add_update("progress")
    expected = srv.get_state()["updates"]
    assert len(first["updates"]) == 1
    assert srv.set_goal("two")["updates"] == expected
    assert srv.set_goal("two")["updates"] == expected


def test_absolute_file_paths_and_android_tmp_mapping(monkeypatch, tmp_path):
    from pathlib import Path
    home, temp = tmp_path / "home", tmp_path / "android-tmp"
    home.mkdir(); temp.mkdir()
    monkeypatch.setattr(srv, "HOME", home)
    monkeypatch.setattr(srv, "TMPDIR", temp)
    # Linux writable /tmp retains absolute location and is not placed under HOME.
    target = temp / "absolute.txt"
    assert srv.write_file(str(target), "hi")["path"] == str(target)
    assert srv.read_file(str(target))["text"] == "1: hi"
    with pytest.raises(ValueError, match="resolved to"):
        srv.resolve("/etc/passwd")
    # Exercise Android remapping using a virtual non-/tmp TMPDIR.
    monkeypatch.setattr(srv, "TMPDIR", Path("/data/local/tmp"))
    assert srv.resolve("/tmp/a.txt") == Path("/data/local/tmp/a.txt")
    monkeypatch.setattr(srv, "HOME", Path("/data/user/home"))
    assert srv.resolve("relative.txt") == Path("/data/user/home/relative.txt")


def test_list_dir_rejects_negative_and_allows_zero():
    with pytest.raises(ValueError, match="limit"):
        srv.list_dir("~", -1)
    assert srv.list_dir("~", 0)["items"] == []


def test_run_command_status_and_purge():
    result = srv.run_command("printf output; printf error >&2")
    sid = result["session_id"]
    try:
        status = srv.get_session_status(sid)
        assert status["done"] and status["state"] == "completed"
        assert status["exit_code"] == 0 and status["stdout_bytes"] == 6
        assert not status["durable"]
        srv.purge_session_output(sid)
        assert srv.read_output(sid)["text"] == ""
        assert srv.read_output(sid, "stderr")["text"] == ""
        assert srv.get_session_status(sid)["exit_code"] == 0
        srv.purge_session_output(sid)  # idempotent
    finally:
        srv.SESSIONS.pop(sid, None)


def test_legacy_active_purge_rejected_and_failure_status():
    sid = "release-active-probe"
    srv.SESSIONS[sid] = {"done": False, "stdout": b"keep", "stderr": b"", "code": None}
    try:
        assert srv.get_session_status(sid)["state"] == "running"
        with pytest.raises(ValueError, match="active"):
            srv.purge_session_output(sid)
        assert srv.SESSIONS[sid]["stdout"] == b"keep"
        srv.SESSIONS[sid].update(done=True, code=1)
        assert srv.get_session_status(sid)["state"] == "failed"
        srv.SESSIONS[sid]["err"] = "timeout"
        assert srv.get_session_status(sid)["state"] == "timed_out"
    finally:
        srv.SESSIONS.pop(sid, None)


def test_state_and_edit_schema_enums():
    import asyncio
    tools = {tool.name: tool for tool in asyncio.run(srv.srv.list_tools())}
    from dot_mcp.work import TASK_STATES
    schemas = {name: getattr(tool, "inputSchema", None) or tool.input_schema for name, tool in tools.items()}
    assert set(schemas["update_work_task"]["properties"]["state"]["enum"]) == TASK_STATES
    assert set(schemas["list_work_tasks"]["properties"]["state"]["enum"]) == TASK_STATES | {""}
    assert set(schemas["edit_file"]["properties"]["mode"]["enum"]) == {"replace_match", "insert_before", "insert_after"}
