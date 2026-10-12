"""Production module HTTP smoke in an isolated child, loopback only."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def test_production_app_auth_and_mcp_initialize(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, HOME=str(tmp_path), DOT_MCP_DATA=str(tmp_path / "data"),
               TMPDIR=str(tmp_path / "tmp"), MCP_HOST="127.0.0.1", MCP_PORT=str(port),
               MCP_PATH_SECRET="isolated-test-secret", MCP_PUBLIC_HOST="fixture.tunnl.gg")
    log = tmp_path / "server.log"
    with log.open("wb") as output:
        child = subprocess.Popen([sys.executable, "-m", "dot_mcp.server"], env=env,
                                 stdout=output, stderr=output)
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                with urlopen(base + "/health", timeout=0.2) as r:
                    assert json.load(r)["tools"] == 46
                break
            except OSError:
                if child.poll() is not None:
                    raise AssertionError(log.read_text())
                time.sleep(0.05)
        else:
            raise AssertionError("server did not become healthy")
        try:
            urlopen(base + "/mcp/", timeout=2)
        except HTTPError as error:
            assert error.code == 401
        else:
            raise AssertionError("missing bearer header was accepted")
        with urlopen(base + "/live", timeout=2) as live:
            assert b'Owner token for control or private view' in live.read()
            assert live.headers['Cache-Control'] == 'no-store'
        request = Request(base + "/mcp/", method="POST", headers={
            "Authorization": "Bearer isolated-test-secret",
            "Content-Type": "application/json", "Accept": "application/json, text/event-stream",
        }, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "isolated-test", "version": "1"}}}).encode())
        for host in (f"127.0.0.1:{port}", "localhost", "127.0.0.1", "[::1]",
                     "fixture.tunnl.gg", "fixture.tunnl.gg:443"):
            request.add_header("Host", host)
            with urlopen(request, timeout=3) as response:
                assert response.status == 200
                assert b'"jsonrpc"' in response.read()
        for host in ("evil.test", "stale.tunnl.gg", "fixture.tunnl.gg:444"):
            request.add_header("Host", host)
            try:
                urlopen(request, timeout=3)
            except HTTPError as error:
                assert error.code == 421
                assert error.read() == b"Invalid Host header"
            else:
                raise AssertionError("unlisted host was accepted")
    finally:
        child.terminate()
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
