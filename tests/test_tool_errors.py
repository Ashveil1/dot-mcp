"""Error causes on the real authenticated transport, without input/secret leakage."""
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest
from dot_mcp.errors import safe_failure, safe_tool


def test_unknown_errors_never_echo_private_text():
    private = "SECRET-canary /outside/private https://user:password@example.com"
    for exception in (ValueError(private), RuntimeError(private), OSError(private)):
        error = json.loads(str(safe_failure(exception)))
        assert error["error"]["code"] == "tool_failed"
        assert private not in str(safe_failure(exception))
    error = safe_failure(FileNotFoundError(2, "missing", private))
    assert private not in str(error)
    assert json.loads(str(error))["error"]["code"] == "not_found"


def test_decorator_preserves_signature_and_safe_exception():
    import inspect
    @safe_tool
    def probe(path: str):
        raise ValueError("stale file private-canary")
    assert str(inspect.signature(probe)) == "(path: str)"
    with pytest.raises(ValueError) as caught:
        probe("private-canary")
    assert "private-canary" not in str(caught.value)
    assert json.loads(str(caught.value))["error"]["code"] == "precondition_failed"


def test_safe_errors_over_mcp_http(tmp_path):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    import httpx
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    env = dict(os.environ, HOME=str(tmp_path), DOT_MCP_DATA=str(tmp_path / "data"),
               TMPDIR=str(tmp_path / "tmp"), MCP_PORT=str(port), MCP_HOST="127.0.0.1",
               MCP_PATH_SECRET="error-test-owner", MCP_PUBLIC_HOST="",
               PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    logpath = tmp_path / "server.log"
    with logpath.open("wb") as log:
        child = subprocess.Popen([sys.executable, "-m", "dot_mcp.server"], env=env, stdout=log, stderr=log)
    async def check():
        async with httpx.AsyncClient(headers={"Authorization": "Bearer error-test-owner"}) as client:
            async with streamable_http_client(f"http://127.0.0.1:{port}/mcp/", http_client=client) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    async def call(name, args, code=None, field=None):
                        result = await session.call_tool(name, args)
                        text = "\n".join(c.text for c in result.content if hasattr(c, "text"))
                        assert "private-canary" not in text
                        assert "error-test-owner" not in text
                        assert "/outside/private" not in text
                        if code: assert str(tmp_path) not in text
                        body = json.loads(text)
                        if code:
                            assert getattr(result, "is_error", getattr(result, "isError", False)), text
                            assert body["error"]["code"] == code
                            if field: assert body["error"]["field"] == field
                        else:
                            assert not getattr(result, "is_error", getattr(result, "isError", False)), text
                        return body
                    await call("read_file", {"path": "missing-private-canary"}, "not_found", "path")
                    await call("read_file", {"path": "/outside/private-canary"}, "permission_denied", "path")
                    await call("read_file", {}, "bad_argument", "path")
                    await call("list_dir", {"limit": -1}, "bad_argument", "limit")
                    await call("list_dir", {"limit": "private-canary"}, "bad_argument", "limit")
                    await call("get_work_task", {"task_id": "private-canary"}, "not_found", "task_id")
                    await call("get_session_status", {"session_id": "private-canary"}, "not_found", "session_id")
                    invalid = await call("update_work_task", {"task_id": "private-canary", "state": "private-canary"}, "invalid_state", "state")
                    assert set(invalid["error"]["allowed"]) == {"pending", "running", "blocked", "done", "failed", "cancelled", "interrupted"}
                    await call("edit_file", {"path": "x", "match_text": "private-canary", "write_text": "private-canary", "mode": "private-canary"}, "bad_argument", "mode")
                    await call("write_file", {"path": "x", "content": "hello"})
                    await call("write_file", {"path": "x", "content": "private-canary", "create_only": True}, "precondition_failed", "path")
                    await call("write_file", {"path": "x", "content": "private-canary", "expected_sha256": "private-canary"}, "precondition_failed", "expected_sha256")
                    started = await call("start_command", {"command": "echo hello"})
                    for _ in range(100):
                        status = await call("get_session_status", {"session_id": started["session_id"]})
                        if status["done"]: break
                        await asyncio.sleep(.02)
                    assert status["done"]
                    await call("purge_session_output", {"session_id": started["session_id"]}, "precondition_failed")
                    context = await call("bootstrap_context", {})
                    await call("ack_work_result", {"result_id": context["unread_results"][0]["id"]})
                    await call("purge_session_output", {"session_id": started["session_id"]})
                    await call("vault_list", {}, "precondition_failed")
                    await call("unknown-private-canary", {}, "not_found", "tool")
                    await call("browser_snapshot", {}, "precondition_failed")
    try:
        for _ in range(100):
            try:
                urlopen(f"http://127.0.0.1:{port}/health", timeout=.2).close(); break
            except OSError:
                if child.poll() is not None: raise AssertionError(logpath.read_text())
                time.sleep(.05)
        else: raise AssertionError("server failed to start")
        asyncio.run(check())
    finally:
        child.terminate()
        try: child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.kill(); child.wait()
