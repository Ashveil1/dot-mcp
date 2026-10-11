"""Isolated Linux reliability regressions; no real tunnel or user files."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest
from dot_mcp import lifecycle, orchestrator as ui
from dot_mcp import server as srv


def test_nonexistent_parent_cannot_escape():
    with pytest.raises(ValueError):
        srv.resolve(str(srv.HOME / "missing" / ".." / ".." / "escape"))


def test_symlink_escape(tmp_path):
    link = srv.HOME / "outside-link"
    link.symlink_to(tmp_path, target_is_directory=True)
    try:
        with pytest.raises(ValueError):
            srv.resolve(str(link / "new-file"))
    finally:
        link.unlink()


def test_lock_preserves_body_exception(tmp_path):
    with pytest.raises(ValueError, match="original"):
        with srv._file_lock(tmp_path / "x", True):
            raise ValueError("original")


def test_lock_failure_does_not_run_body(tmp_path, monkeypatch):
    def fail(*args):
        raise OSError("lock failed")
    monkeypatch.setattr(srv.fcntl, "flock", fail)
    with pytest.raises(OSError, match="lock failed"):
        with srv._file_lock(tmp_path / "x", True):
            pytest.fail("must not bypass lock failure")


def test_missing_guarded_write_rejected():
    with pytest.raises(ValueError, match="missing"):
        srv.write_file("missing-sha.txt", "new", expected_sha256="abc")


def test_concurrent_guarded_writers_only_one_wins():
    p = srv.HOME / "guarded.txt"
    p.write_text("old")
    expected = hashlib.sha256(b"old").hexdigest()
    results = []
    def writer(n):
        try:
            srv.write_file(str(p), str(n), expected)
            results.append("ok")
        except ValueError:
            results.append("conflict")
    threads = [threading.Thread(target=writer, args=(n,)) for n in range(12)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert results.count("ok") == 1
    assert results.count("conflict") == 11


def test_concurrent_append_no_loss():
    p = srv.HOME / "append.txt"
    threads = [threading.Thread(target=srv.append_file, args=(str(p), f"{n}\n")) for n in range(20)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert sorted(map(int, p.read_text().splitlines())) == list(range(20))


def test_atomic_write_preserves_mode_and_cleans_temps():
    p = srv.HOME / "mode.txt"
    p.write_text("old")
    p.chmod(0o640)
    srv.write_file(str(p), "new")
    assert p.stat().st_mode & 0o777 == 0o640
    assert p.read_text() == "new"


def test_json_private(tmp_path):
    p = tmp_path / "private.json"
    srv.save_json(p, {"value": 1})
    assert p.stat().st_mode & 0o777 == 0o600


def test_state_dir_override_is_exact(tmp_path, monkeypatch):
    monkeypatch.setenv("DOT_MCP_STATE_DIR", str(tmp_path))
    assert lifecycle.state_dir() == tmp_path


def test_pid_reuse_or_forged_record_never_signalled(tmp_path):
    record = tmp_path / "process.json"
    lifecycle.record(record, os.getpid())
    info = json.loads(record.read_text())
    info["start_ticks"] = "invalid"
    record.write_text(json.dumps(info))
    assert lifecycle.owned(record) is None
    assert not lifecycle.stop(record)


def test_owned_child_can_be_stopped(tmp_path):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    record = tmp_path / "process.json"
    try:
        lifecycle.record(record, child.pid)
        assert lifecycle.owned(record)
        assert lifecycle.stop(record, timeout=0.1)
        child.wait(timeout=3)
        assert not record.exists()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_legacy_pid_file_is_not_trusted(tmp_path):
    record = tmp_path / "process.json"
    record.write_text(str(os.getpid()))
    assert not lifecycle.stop(record)


def test_instance_lock_rejects_parallel_operation(tmp_path):
    with lifecycle.instance_lock(tmp_path):
        with pytest.raises(RuntimeError, match="in progress"):
            with lifecycle.instance_lock(tmp_path):
                pass


def test_failed_start_cleans_up(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(ui, "_plain_flow", lambda *a: ui.FlowResult(False, reason="verify"))
    monkeypatch.setattr(lifecycle, "stop", lambda p: calls.append(p))
    ui.plain_flow(SimpleNamespace(repo=tmp_path), tmp_path, None)
    assert tmp_path / "logs" / "server.json" in calls
    assert tmp_path / "logs" / "tunnel.json" in calls


def test_unexpected_start_failure_cleans_up(tmp_path, monkeypatch):
    calls = []
    def fail(*a):
        raise OSError("spawn failed")
    monkeypatch.setattr(ui, "_plain_flow", fail)
    monkeypatch.setattr(lifecycle, "stop", lambda p: calls.append(p))
    with pytest.raises(OSError):
        ui.plain_flow(SimpleNamespace(repo=tmp_path), tmp_path, None)
    assert len(calls) == 2


def test_cli_json_start_checks_busy_port(tmp_path, monkeypatch, capsys):
    from dot_mcp import cli
    monkeypatch.setattr(cli, "REPO", tmp_path)
    monkeypatch.setattr(ui, "port_in_use", lambda p: True)
    assert cli.cmd_start(SimpleNamespace(port=0, json=True)) == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "port_in_use"


def test_cli_reports_start_failure(tmp_path, monkeypatch, capsys):
    from dot_mcp import cli
    monkeypatch.setattr(cli, "get_secret", lambda: "test")
    monkeypatch.setattr(ui, "port_in_use", lambda p: False)
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="mcp-ok"))
    monkeypatch.setattr(ui, "plain_flow", lambda *a, **k: ui.FlowResult(False, reason="verify"))
    assert cli._cmd_start(SimpleNamespace(port=0, data_dir=str(tmp_path), json=False)) == 1
    assert "Start failed: verify" in capsys.readouterr().err


def test_cross_process_create_only_has_one_winner():
    target = srv.HOME / "exclusive.txt"
    code = "from dot_mcp.server import write_file; import sys; write_file(sys.argv[1], sys.argv[2], create_only=True)"
    children = [subprocess.Popen([sys.executable, "-c", code, str(target), str(n)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                for n in range(4)]
    codes = [child.wait(timeout=10) for child in children]
    assert codes.count(0) == 1
    assert len(target.read_text()) == 1


def test_edit_serializes_and_checks_unique_match():
    target = srv.HOME / "edit.txt"
    target.write_text("one")
    srv.edit_file(str(target), "one", "two")
    with pytest.raises(ValueError, match="0x"):
        srv.edit_file(str(target), "one", "three")
    assert target.read_text() == "two"


def test_cli_stop_ignores_unrecorded_processes(tmp_path, monkeypatch):
    from dot_mcp import cli
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    monkeypatch.setattr(cli, "REPO", tmp_path)
    try:
        assert cli.cmd_stop(SimpleNamespace()) == 0
        assert child.poll() is None
    finally:
        child.terminate()
        child.wait(timeout=3)


def test_secret_created_private(tmp_path, monkeypatch):
    from dot_mcp import cli
    monkeypatch.setattr(cli, "STATE_DIR", tmp_path)
    monkeypatch.setattr(cli, "SECRET_FILE", tmp_path / "owner_secret")
    first = cli.get_secret()
    assert cli.get_secret() == first
    assert (tmp_path / "owner_secret").stat().st_mode & 0o777 == 0o600
