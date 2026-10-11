"""Linux process ownership records. Never signal a bare, potentially reused PID."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import signal
import tempfile
import time


def state_dir() -> Path:
    return Path(os.environ.get("DOT_MCP_STATE_DIR") or
                str(Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "dot-mcp"))


def identity(pid: int) -> dict | None:
    try:
        proc = Path("/proc") / str(pid)
        stat = (proc / "stat").read_text().rsplit(")", 1)[1].split()
        return {"pid": pid, "start_ticks": stat[19], "uid": proc.stat().st_uid,
                "cmdline": (proc / "cmdline").read_bytes().hex()}
    except (OSError, ValueError, IndexError):
        return None


def record(path: Path, pid: int) -> None:
    info = identity(pid)
    if not info or info["uid"] != os.getuid():
        raise RuntimeError("cannot verify child process ownership")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(info, f)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def owned(path: Path) -> dict | None:
    try:
        info = json.loads(path.read_text())
        current = identity(int(info["pid"]))
        if current == info and info["uid"] == os.getuid():
            return info
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def stop(path: Path, timeout: float = 5) -> bool:
    info = owned(path)
    if not info:
        path.unlink(missing_ok=True)
        return False
    try:
        os.kill(info["pid"], signal.SIGTERM)
        end = time.monotonic() + timeout
        while owned(path) and time.monotonic() < end:
            try:
                status = (Path("/proc") / str(info["pid"]) / "stat").read_text().rsplit(")", 1)[1].split()[0]
                if status == "Z":
                    break
            except OSError:
                break
            time.sleep(0.05)
        if owned(path):
            os.kill(info["pid"], signal.SIGKILL)
    except ProcessLookupError:
        pass
    path.unlink(missing_ok=True)
    return True


@contextmanager
def instance_lock(repo: Path):
    path = repo / "logs" / "instance.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("another start/stop operation is in progress") from None
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
