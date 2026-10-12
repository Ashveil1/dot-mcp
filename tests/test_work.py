"""Durable async execution including a coding-task/new-chat demonstration."""
import json
import os
from pathlib import Path
import time

import pytest
from dot_mcp.work import WorkStore


@pytest.fixture
def work(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    def resolve(path):
        file = home if path == "~" else Path(path)
        if not file.is_absolute(): file = home / file
        file = file.resolve()
        if not file.is_relative_to(home): raise ValueError("outside test home")
        return file
    store = WorkStore(tmp_path / "data", resolve, lambda: "/bin/sh")
    yield store, home
    for sid in list(store._processes):
        store.cancel(sid)
        wait(store, sid)
    store.close()


def wait(store, sid, seconds=6):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        status = store.status(sid)
        if status["done"]: return status
        time.sleep(0.02)
    raise AssertionError("command didn't finish")


def test_coding_task_file_then_new_chat(work):
    store, home = work
    task = store.create_task("Write and test greeting.py", "Produce a working file and verify its output")
    command = "printf 'print(\"hello\")\\n' > greeting.py; python3 greeting.py"
    started = store.start(command, task_id=task["id"], idempotency_key="demo-code-1")
    sid = started["session_id"]
    assert wait(store, sid)["state"] == "completed"
    assert store.read_output(sid)["text"] == "hello\n"
    assert store.get_task(task["id"])["state"] == "running"  # must verify result explicitly
    artifact = store.artifact(task["id"], str(home / "greeting.py"))
    store.update_task(task["id"], "done", result="greeting.py runs and prints hello")
    # A new chat has no in-memory conversation; read the persisted context/task.
    context = store.context()
    assert context["artifacts"][0]["id"] == artifact["artifact_id"]
    assert store.get_task(task["id"])["result"] == "greeting.py runs and prints hello"
    assert context["unread_count"] == 1


def test_output_visible_while_running(work):
    store, _ = work
    sid = store.start("printf first; sleep 1; printf second")["session_id"]
    for _ in range(60):
        out = store.read_output(sid)
        if out["text"]: break
        time.sleep(0.01)
    assert out["text"] == "first"
    assert not out["done"]
    assert wait(store, sid)["state"] == "completed"
    assert store.read_output(sid, offset=5)["text"] == "second"


def test_idempotency_same_request_no_second_launch(work):
    store, home = work
    first = store.start("echo line >> calls.txt", idempotency_key="once")
    second = store.start("echo line >> calls.txt", idempotency_key="once")
    assert first["session_id"] == second["session_id"]
    assert second["reused"]
    wait(store, first["session_id"])
    assert (home / "calls.txt").read_text() == "line\n"
    with pytest.raises(ValueError, match="different request"):
        store.start("echo other", idempotency_key="once")


def test_cancel_and_timeout(work):
    store, _ = work
    sid = store.start("sleep 20")["session_id"]
    store.cancel(sid)
    assert wait(store, sid)["state"] == "cancelled"
    sid = store.start("sleep 20", timeout=1)["session_id"]
    assert wait(store, sid)["state"] == "timed_out"


def test_output_budget_is_bounded(work):
    store, _ = work
    store.output_limit = 8192
    sid = store.start("yes x")["session_id"]
    status = wait(store, sid)
    assert status["state"] == "output_limit"
    assert status["stdout_bytes"] + status["stderr_bytes"] <= 8192


def test_concurrency_rejects_instead_of_pruning(work):
    store, _ = work
    store.max_sessions = 1
    sid = store.start("sleep 20")["session_id"]
    with pytest.raises(ValueError, match="concurrency"):
        store.start("echo no")
    assert store.status(sid)["state"] in ("starting", "running")
    store.cancel(sid)
    wait(store, sid)


def test_tasks_require_result_and_blocker(work):
    store, _ = work
    tid = store.create_task("task")["id"]
    with pytest.raises(ValueError): store.update_task(tid, "done")
    with pytest.raises(ValueError): store.update_task(tid, "blocked")
    store.update_task(tid, "blocked", blocker="Which file should I edit?")
    assert store.context()["tasks"][0]["blocker"] == "Which file should I edit?"


def test_cannot_finish_active_command(work):
    store, _ = work
    tid = store.create_task("task")["id"]
    sid = store.start("sleep 20", task_id=tid)["session_id"]
    with pytest.raises(ValueError, match="active command"):
        store.update_task(tid, "done", result="fake")
    store.cancel(sid)
    wait(store, sid)


def test_purge_needs_review_and_keeps_metadata(work):
    store, _ = work
    sid = store.start("echo output")["session_id"]
    wait(store, sid)
    with pytest.raises(ValueError, match="acknowledge"):
        store.purge_output(sid)
    result = store.context()["unread_results"][0]
    store.ack_result(result["id"])
    store.purge_output(sid)
    assert store.read_output(sid)["text"] == ""
    assert store.status(sid)["state"] == "completed"


def test_restart_marks_unverified_command_interrupted(tmp_path):
    data = tmp_path / "data"
    store = WorkStore(data, lambda p: tmp_path, lambda: "/bin/sh")
    task = store.create_task("restart test")
    # Durable record after a crash, no live process to adopt, no re-execution.
    with store.connection() as c:
        c.execute("INSERT INTO sessions(id,task_id,command,cwd,timeout,state,created,updated,request_hash) VALUES (?,?,?,?,?,'running',?,?,?)", (
            "lost", task["id"], "touch must-not-run", str(tmp_path), 10, time.time(), time.time(), "hash"))
        c.execute("UPDATE tasks SET state='running' WHERE id=?", (task["id"],))
    store.close()
    reopened = WorkStore(data, lambda p: tmp_path, lambda: "/bin/sh")
    try:
        assert reopened.status("lost")["state"] == "interrupted"
        assert reopened.get_task(task["id"])["state"] == "interrupted"
        assert not (tmp_path / "must-not-run").exists()
        assert reopened.context()["unread_count"] == 1
    finally: reopened.close()


def test_second_owner_cannot_recover_live_server(work):
    store, _ = work
    with pytest.raises(RuntimeError, match="already owned"):
        WorkStore(store.root, store.resolve, store.shell)


def test_legacy_import_is_once_with_backup(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    raw = json.dumps({"tasks": [{"id": "abc", "title": "old", "done": False}], "goal": "preserved"})
    (data / "state.json").write_text(raw)
    for _ in range(2):
        store = WorkStore(data, lambda p: tmp_path, lambda: "/bin/sh")
        assert store.list_tasks()["count"] == 1
        assert store.get_task("abc")["title"] == "old"
        store.close()
    assert (data / "state.pre-work-v1.json").read_text() == raw
    assert (data / "state.json").read_text() == raw


def test_invalid_legacy_is_not_silently_dropped(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "state.json").write_text("not-json")
    with pytest.raises(ValueError, match="invalid state"):
        WorkStore(data, lambda p: tmp_path, lambda: "/bin/sh")


def test_command_nonzero_is_not_task_success(work):
    store, _ = work
    tid = store.create_task("must fail")["id"]
    sid = store.start("exit 4", task_id=tid)["session_id"]
    assert wait(store, sid)["exit_code"] == 4
    assert store.get_task(tid)["state"] == "failed"


def test_detached_child_does_not_hang_capture(work):
    store, _ = work
    sid = store.start("sleep 20 &")["session_id"]
    assert wait(store, sid)["state"] == "failed"


def test_output_validation(work):
    store, _ = work
    sid = store.start("echo x")["session_id"]
    wait(store, sid)
    for args in ({"stream": "x"}, {"offset": -1}, {"length": 0}):
        with pytest.raises(ValueError): store.read_output(sid, **args)


def test_actual_server_owner_crash_and_reopen(tmp_path):
    import subprocess
    import sys
    from dot_mcp import lifecycle
    data = tmp_path / "data"
    code = '''
import json, sys, time
from pathlib import Path
from dot_mcp.work import WorkStore
root = Path(sys.argv[1])
store = WorkStore(root / 'data', lambda p: root, lambda: '/bin/sh')
task = store.create_task('crash test')
session = store.start('printf ready; sleep 30; touch must-not-run', task_id=task['id'])
while store.status(session['session_id'])['state'] == 'starting': time.sleep(0.02)
print(json.dumps({'task_id': task['id'], 'session_id': session['session_id']}), flush=True)
time.sleep(60)
'''
    child = subprocess.Popen([sys.executable, "-u", "-c", code, str(tmp_path)], stdout=subprocess.PIPE, text=True)
    store = None
    try:
        ids = json.loads(child.stdout.readline())
        child.kill()
        child.wait(timeout=3)
        store = WorkStore(data, lambda p: tmp_path, lambda: "/bin/sh")
        assert store.status(ids["session_id"])["state"] == "interrupted"
        assert store.get_task(ids["task_id"])["state"] == "interrupted"
        assert not (tmp_path / "must-not-run").exists()
        assert store.context()["unread_count"] == 1
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        child.stdout.close()
        if store: store.close()


def test_artifact_and_completed_task_survive_reopen(tmp_path):
    data = tmp_path / "data"
    file = tmp_path / "result.txt"
    file.write_text("result")
    store = WorkStore(data, lambda p: Path(p), lambda: "/bin/sh")
    task = store.create_task("produce file")
    aid = store.artifact(task["id"], str(file))["artifact_id"]
    store.update_task(task["id"], "done", result="Verified result.txt")
    store.close()
    reopened = WorkStore(data, lambda p: Path(p), lambda: "/bin/sh")
    try:
        assert reopened.get_task(task["id"])["state"] == "done"
        assert reopened.context()["artifacts"][0]["id"] == aid
    finally: reopened.close()


def test_mcp_tool_demo_across_two_client_sessions(tmp_path):
    import asyncio
    import socket
    import subprocess
    import sys
    from urllib.request import urlopen
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    import httpx
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, HOME=str(tmp_path), DOT_MCP_DATA=str(tmp_path / "data"),
               TMPDIR=str(tmp_path / "tmp"), MCP_PORT=str(port), MCP_HOST="127.0.0.1",
               MCP_PATH_SECRET="demo-secret", MCP_PUBLIC_HOST="",
               PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    with (tmp_path / "server.log").open("wb") as log:
        child = subprocess.Popen([sys.executable, "-m", "dot_mcp.server"], env=env, stdout=log, stderr=log)
    async def demo():
        url = f"http://127.0.0.1:{port}/mcp/"
        async def call(session, name, args):
            result = await session.call_tool(name, args)
            assert not getattr(result, "is_error", getattr(result, "isError", False)), result.content
            text = "\n".join(x.text for x in result.content if hasattr(x, "text"))
            return json.loads(text)
        async with httpx.AsyncClient(headers={"Authorization": "Bearer demo-secret"}) as client:
            async with streamable_http_client(url, http_client=client) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    defs = await session.list_tools()
                    assert len(defs.tools) == 46
                    task = await call(session, "create_work_task", {"title": "Write a greeting script"})
                    command = "printf 'print(\"hello\")\\n' > greeting.py; python3 greeting.py"
                    started = await call(session, "start_command", {"command": command, "task_id": task["id"], "idempotency_key": "mcp-demo"})
                    for _ in range(100):
                        status = await call(session, "get_session_status", {"session_id": started["session_id"]})
                        if status["done"]: break
                        await asyncio.sleep(0.02)
                    assert status["state"] == "completed"
                    output = await call(session, "read_output", {"session_id": started["session_id"]})
                    assert output["text"] == "hello\n"
                    await call(session, "register_artifact", {"task_id": task["id"], "path": "greeting.py"})
                    await call(session, "update_work_task", {"task_id": task["id"], "state": "done", "result": "Verified greeting.py prints hello"})
        # Different initialized MCP client, no shared conversation messages.
        async with httpx.AsyncClient(headers={"Authorization": "Bearer demo-secret"}) as client:
            async with streamable_http_client(url, http_client=client) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    context = await call(session, "bootstrap_context", {})
                    assert context["recent_tasks"][0]["result"] == "Verified greeting.py prints hello"
                    assert context["artifacts"][0]["name"] == "greeting.py"
                    assert context["unread_count"] == 1
    try:
        for _ in range(100):
            try:
                urlopen(f"http://127.0.0.1:{port}/health", timeout=0.2).close()
                break
            except OSError:
                if child.poll() is not None:
                    raise AssertionError((tmp_path / "server.log").read_text())
                time.sleep(0.05)
        else:
            raise AssertionError("server failed to start")
        asyncio.run(demo())
    finally:
        child.terminate()
        try: child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
