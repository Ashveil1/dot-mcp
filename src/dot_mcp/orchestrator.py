#!/usr/bin/env python3
"""dots_ui — terminal UI layer for `dots start`. UI ONLY.

Everything here is presentation: detection, symbols, panels, checklist,
live loop, keyboard, clipboard, log tee. Orchestration helpers (boot /
health / tunnel / verify) live here too so `dots` stays a thin dispatcher —
moved verbatim, no behavior change.

Layout (three phases, each painting its own final frame with alt-screen)
  1. checklist: hand-rolled paint — spinners → ✓ + elapsed
  2. staged reveal: connector panel → ready panel
  3. live mode: one frame per tick (header pill → connector → ready → footer)

Rich Live is NOT used: it reflowed poorly in some terminals, so each phase
paints the whole frame in place (alt-screen + cursor-home + clear).
"""
from __future__ import annotations
import collections
import json
import locale
import os
import re
import shutil
import signal as _sig
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

DOTS_VERSION = "0.3.0"
SECRET_MASK_KEEP = 4  # show first 4 + last 3 chars when masked

# ---------------------------------------------------------------- detection

def _is_tty() -> bool:
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


def _use_color() -> bool:
    if os.environ.get("NO_COLOR", "") != "":
        return False
    return _is_tty()


def _supports_unicode() -> bool:
    if os.environ.get("NO_UNICODE", "") != "":
        return False
    if os.environ.get("TERM", "") == "dumb":
        return False
    try:
        enc = (locale.getpreferredencoding(False) or "").lower()
        out = (getattr(sys.stdout, "encoding", "") or "").lower()
        return "utf" in enc and "utf" in out
    except Exception:
        return False


def _term_width(default: int = 100) -> int:
    try:
        return max(60, min(shutil.get_terminal_size().columns, default))
    except Exception:
        return 80


UNI = _supports_unicode()
SYM_OK = "✓" if UNI else "ok"
SYM_FAIL = "✗" if UNI else "x"
SYM_DOT = "●" if UNI else "*"
SYM_SPIN = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"] if UNI else ["-", "\\", "|", "/"]

GREEN, YELLOW, RED, CYAN = "green", "yellow", "red", "cyan"


def make_console():
    from rich.console import Console
    # width=None lets Rich re-read the real terminal size on every render.
    return Console(width=None, no_color=not _use_color(), force_terminal=_is_tty())


def _box():
    from rich import box
    return box.ROUNDED if UNI else box.ASCII


def link(url: str, text: str | None = None) -> str:
    """Clickable OSC 8 hyperlink on TTY, plain text otherwise."""
    label = text or url
    if not _is_tty() or os.environ.get("NO_HYPERLINK", "") != "":
        return label
    return f"\x1b]8;;{url}\x1b\\{label}\x1b]8;;\x1b\\"


def mask(secret: str) -> str:
    dots = "…" if UNI else "..."
    if len(secret) <= SECRET_MASK_KEEP + 3:
        return "•" * len(secret)
    return f"{secret[:SECRET_MASK_KEEP]}{dots}{secret[-3:]}"


# ---------------------------------------------------------------- clipboard

def clipboard_copy(text: str) -> bool:
    """Best-effort copy: native tools first, OSC 52 fallback. No new deps."""
    import shutil as _sh
    for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"],
                ["xsel", "--clipboard", "--input"],
                ["pbcopy"], ["termux-clipboard-set"]):
        if _sh.which(cmd[0]):
            try:
                subprocess.run(cmd, input=text.encode(), timeout=5, check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            except Exception:
                pass
    if _is_tty():
        try:
            import base64
            sys.stdout.write("\x1b]52;c;%s\x1b\\" % base64.b64encode(text.encode()).decode())
            sys.stdout.flush()
            return True
        except Exception:
            pass
    return False


# ---------------------------------------------------------------- data

@dataclass
class StartCtx:
    port: int
    data_dir: str
    secret: str
    py: str
    env: dict
    repo: Path


@dataclass
class Step:
    label: str
    state: str = "wait"  # wait | run | ok | fail
    elapsed: float = 0.0
    detail: str = ""


# ------------------------------------------------- orchestration (moved in)

def port_in_use(port: int) -> bool:
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except Exception:
        return False


def _wait_port_free(port: int, timeout: int = 20) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if not port_in_use(port):
            return True
        time.sleep(1)
    return not port_in_use(port)


def _kill_proc(proc) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=10)
        except Exception:
            pass


def _boot_server(py: str, env: dict, repo: Path, logfile: Path,
                 pipe_out: bool = False):
    """Boot the server module. pipe_out=True keeps stdout for the live log tee."""
    logfile.parent.mkdir(parents=True, exist_ok=True)
    if not pipe_out:
        fh = open(logfile, "wb")
        try:
            os.chmod(logfile, 0o600)
        except Exception:
            pass
        out = fh
    else:
        out = subprocess.PIPE
    return subprocess.Popen(
        [py, "-m", "dot_mcp.server"], cwd=str(repo), env=env,
        stdout=out, stderr=subprocess.STDOUT if not pipe_out else subprocess.STDOUT,
        stdin=subprocess.DEVNULL, start_new_session=True)


def _wait_health(port: int, timeout: int = 25) -> bool:
    import urllib.request
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as r:
                if '"tools"' in r.read().decode():
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def _ensure_tunnl_key(state_dir: Path):
    if not shutil.which("ssh"):
        raise RuntimeError("ssh not found — install OpenSSH first")
    key_dir = state_dir / "ssh"
    key_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(key_dir, 0o700)
    key = key_dir / "id_ed25519"
    if not (key.is_file() and (key_dir / "id_ed25519.pub").is_file()):
        if not shutil.which("ssh-keygen"):
            raise RuntimeError("ssh-keygen not found — install OpenSSH first")
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "",
                        "-f", str(key), "-C", "dot-mcp-tunnl"], check=True)
        os.chmod(key, 0o600)
    return key


_TUNNL_RE = re.compile(r"https://[a-z-]+-[a-f0-9]+\.tunnl\.gg")


def _start_tunnl(repo: Path, port: int, key: Path):
    pidfile = repo / "logs" / "tunnl.pid"
    try:
        old = int(pidfile.read_text().strip())
        try:
            os.kill(old, 0)
            os.kill(old, 9)
        except Exception:
            pass
    except Exception:
        pass
    log = repo / "tunnel.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "wb"):
        pass
    try:
        os.chmod(log, 0o600)
    except Exception:
        pass
    fh = open(log, "ab")
    proc = subprocess.Popen(
        ["ssh", "-tt", "-i", str(key), "-o", "IdentitiesOnly=yes",
         "-o", "StrictHostKeyChecking=accept-new", "-o", "ServerAliveInterval=20",
         "-o", "ServerAliveCountMax=3", "-R", f"80:127.0.0.1:{port}", "stable@proxy.tunnl.gg"],
        stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    pidfile.write_text(str(proc.pid))
    url = ""
    end = time.time() + 45
    while time.time() < end:
        if proc.poll() is not None:
            raise RuntimeError("tunnel died — see tunnel.log: " + log.read_text()[-500:])
        m = _TUNNL_RE.search(log.read_text(errors="replace"))
        if m:
            url = m.group(0)
            break
        time.sleep(1)
    if not url:
        proc.kill()
        raise RuntimeError("could not read URL from tunnel — see tunnel.log")
    return url


def _public_init_ok(mcp_url: str, secret: str, timeout: int = 20, tries: int = 3) -> bool:
    import urllib.request
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "dots-check", "version": "1"}}}).encode()
    for _ in range(max(1, tries)):
        try:
            # NOTE: Cloudflare edge (error 1010) blocks Python-urllib UA.
            req = urllib.request.Request(mcp_url, data=body, headers={
                "Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {secret}",
                "User-Agent": "dots/1 (health-check)"},
                method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                if r.status == 200 and '"jsonrpc"' in r.read().decode():
                    return True
        except Exception:
            pass
        time.sleep(3)
    return False


def _read_oauth_client(data_dir: str) -> dict:
    try:
        st = json.loads((Path(data_dir) / "oauth.json").read_text(encoding="utf-8"))
        sc = st.get("static_client")
        if isinstance(sc, dict) and sc.get("client_id"):
            return sc
    except Exception:
        pass
    return {}


_TUN_HIT_RE = re.compile(r"^\s*\d{1,2}:\d{2}(:\d{2})?\s+(GET|POST|DELETE)\s+\S+\s+\d{3}\b")


def tunnel_hits(repo: Path) -> int:
    """Best-effort request counter parsed from the tunnel dashboard log."""
    try:
        with open(repo / "tunnel.log", errors="replace") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 200_000))
            return sum(1 for line in fh if _TUN_HIT_RE.match(line))
    except Exception:
        return 0


# ---------------------------------------------------------------- panels

def header_render(console, uptime_s: float, tunnel_ok: bool, server_ok: bool,
                  hits: int) -> "object":
    from rich.panel import Panel
    from rich.text import Text
    pill = Text(f" {SYM_DOT} RUNNING " if server_ok else " ?", style=f"bold {GREEN}" if server_ok
                else f"bold {YELLOW}")
    head = Text.assemble(("dots ", "bold cyan"), (f"v{DOTS_VERSION} ", "dim"),
                         pill, (f"  up {int(uptime_s // 60)}m{int(uptime_s % 60):02d}s", "dim"),
                         (f"  tunnel {'ok' if tunnel_ok else 'down'}", GREEN if tunnel_ok else RED),
                         (f"  server {'ok' if server_ok else 'down'}", GREEN if server_ok else RED),
                         (f"  hits {hits}", "dim"))
    return Panel(head, box=_box(), border_style=CYAN, padding=(0, 2))


def _kv_row(label: str, value: str, secret_val: str = "", masked: bool = True):
    shown = mask(secret_val) if secret_val and masked else (secret_val or value)
    style = "bold" if not secret_val else f"bold {YELLOW}"
    return (label, shown, style)


def _clip(text: str, width: int) -> str:
    dots = "…" if UNI else "..."
    if len(text) <= width:
        return text
    keep = max(0, width - len(dots))
    return text[:keep] + dots


def connector_panel(public_url: str, issuer: str, client_id: str, client_secret: str,
                    owner_secret: str, masked: bool = True):
    from rich.panel import Panel
    from rich.table import Table
    rows = [
        ("Public URL", link(public_url, _clip(public_url, 56)), ""),
        ("Issuer", issuer, ""),
        ("Client ID", client_id, ""),
        ("Client secret", "", client_secret),
        ("Owner secret", "", owner_secret),
    ]
    t = Table(show_header=False, box=None, pad_edge=False, padding=(0, 2))
    t.add_column("k", style="dim", no_wrap=True)
    t.add_column("v", style="bold", overflow="fold")
    for label, val, sec in rows:
        t.add_row(label, mask(sec) if sec and masked else (sec or val))
    return Panel(t, title="Connector", box=_box(), border_style=CYAN, padding=(1, 2))


def ready_panel(secret: str, masked: bool = True):
    from rich.panel import Panel
    shown = mask(secret) if masked else secret
    body = (f"[bold]1.[/] Paste the public URL into your connector (auth = OAuth)\n"
            f"[bold]2.[/] Enter the client ID / secret when asked\n"
            f"[dim]export MCP_PATH_SECRET={shown}   (c = copy URL, s = reveal secrets)[/]")
    return Panel(body, title="Ready", box=_box(), border_style=GREEN, padding=(1, 2))


def footer_render(flash: str = ""):
    from rich.text import Text
    keys = Text.assemble(("q", "bold"), (" quit  ", "dim"), ("c", "bold"), (" copy URL  ", "dim"),
                         ("s", "bold"), (" show secrets  ", "dim"),
                         ("r", "bold"), (" restart", "dim"))
    if flash:
        keys.append(Text(f"   — {flash}", style=CYAN))
    return keys


def steps_render(steps: list) -> "object":
    from rich.table import Table
    from rich.text import Text
    t = Table(show_header=False, box=None, pad_edge=False, padding=(0, 1))
    for st in steps:
        if st.state == "ok":
            mark = Text(f"{SYM_OK} {st.label}  {st.elapsed:.1f}s", style=GREEN)
        elif st.state == "fail":
            mark = Text(f"{SYM_FAIL} {st.label}  {st.detail}", style=RED)
        elif st.state == "run":
            mark = Text(f"{SYM_SPIN[int(time.time() * 8) % len(SYM_SPIN)]} {st.label}", style=CYAN)
        else:
            mark = Text(f"  {st.label}", style="dim")
        t.add_row(mark)
    return t


# ---------------------------------------------------------------- keyboard

class KeyReader(threading.Thread):
    """Single-char stdin reader (TTY only). No new deps (termios/tty/select)."""

    def __init__(self):
        super().__init__(daemon=True)
        self.queue: "collections.deque[str]" = collections.deque()
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        import select
        import termios
        import tty
        try:
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
        except Exception:
            return
        try:
            tty.setraw(fd)
            while not self._stop.is_set():
                try:
                    r, _, _ = select.select([fd], [], [], 0.2)
                except Exception:
                    break
                if r:
                    try:
                        ch = os.read(fd, 1).decode(errors="replace")
                    except Exception:
                        break
                    if ch:
                        self.queue.append(ch.lower())
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
            except Exception:
                pass

    def get(self):
        try:
            return self.queue.popleft()
        except IndexError:
            return None


def clipboard_notice(ok: bool) -> str:
    return "URL copied" if ok else "copy failed (no clipboard tool)"


# ---------------------------------------------------------------- flows

@dataclass
class FlowResult:
    ok: bool
    public_url: str = ""
    host: str = ""
    reason: str = ""
    server_proc: object = None
    started_at: float = field(default_factory=time.monotonic)


def _phase(steps: list, idx: int, tick, fn, fail_reason: str):
    """Run one phase in a worker so `tick` can animate the spinner meanwhile."""
    import time as _t
    import threading as _th
    steps[idx].state = "run"
    t0 = _t.monotonic()
    stop_anim = _th.Event()

    def _animate():
        while not stop_anim.is_set():
            if tick:
                tick()
            time.sleep(0.08)

    anim = None
    if tick:
        anim = _th.Thread(target=_animate, daemon=True)
        anim.start()
    try:
        out = fn()
    except Exception as e:
        steps[idx].detail = str(e)[:100]
        stop_anim.set()
        if tick:
            tick()
        return None, False
    steps[idx].state = "ok"
    steps[idx].elapsed = _t.monotonic() - t0
    stop_anim.set()
    if tick:
        tick()
    return out, True


def run_flow(ctx: StartCtx, state_dir: Path, live=None) -> FlowResult:

    steps = [Step("Starting server"), Step("Opening tunnel"),
             Step("Restarting for public host"), Step("Verifying endpoint")]
    env = dict(ctx.env)
    srv = _phase(steps, 0, live, lambda: _boot_server(ctx.py, env, ctx.repo, ctx.repo / "mcp.log"),
                 "boot")[0]
    if srv is None:
        return FlowResult(False, reason="boot")
    if not _wait_health(ctx.port):
        _kill_proc(srv)
        steps[0].state = "fail"
        steps[0].detail = "no health — see mcp.log"
        if tick:
            tick()
        return FlowResult(False, reason="health")

    def _tun():
        key = _ensure_tunnl_key(state_dir)
        return _start_tunnl(ctx.repo, ctx.port, key)
    url, ok = _phase(steps, 1, live, _tun, "tunnel")
    if not ok:
        _kill_proc(srv)
        return FlowResult(False, reason="tunnel")
    host = url.split("://", 1)[1]
    public_mcp = f"{url}/mcp/"

    def _reboot():
        _kill_proc(srv)
        end = time.time() + 20
        while time.time() < end:
            if not port_in_use(ctx.port):
                break
            time.sleep(1)
        env["MCP_PUBLIC_HOST"] = host
        srv2 = _boot_server(ctx.py, env, ctx.repo, ctx.repo / "mcp.log")
        if not _wait_health(ctx.port):
            _kill_proc(srv2)
            raise RuntimeError("no health — see mcp.log")
        return srv2
    srv, ok = _phase(steps, 2, live, _reboot, "reboot")
    if not ok:
        return FlowResult(False, reason="reboot")

    ok_flag = [False]

    def _verify():
        if not _public_init_ok(public_mcp, ctx.secret):
            raise RuntimeError("public check failed — dots stop, then start again")
        ok_flag[0] = True
        return True
    _, ok = _phase(steps, 3, live, _verify, "verify")
    if not ok:
        _kill_proc(srv)
        return FlowResult(False, reason="verify")
    return FlowResult(True, public_url=public_mcp, host=host, server_proc=srv)


def plain_flow(ctx: StartCtx, state_dir: Path, console, leave_running: bool = False) -> FlowResult:
    """Non-TTY sequential output (same phases, no Live).

    Default: the verified server is stopped so the caller can exec it.
    leave_running=True (for --json): the verified server keeps running in the
    background; the proc is returned in the result.
    """
    console.print("Starting server…")
    env = dict(ctx.env)
    srv = _boot_server(ctx.py, env, ctx.repo, ctx.repo / "mcp.log")
    if not _wait_health(ctx.port):
        _kill_proc(srv)
        console.print("[red]server did not come up — see mcp.log[/]")
        return FlowResult(False, reason="health")
    console.print("Opening tunnel…")
    try:
        url = _start_tunnl(ctx.repo, ctx.port, _ensure_tunnl_key(state_dir))
    except RuntimeError as e:
        _kill_proc(srv)
        console.print(f"[red]{e}[/]")
        return FlowResult(False, reason="tunnel")
    host = url.split("://", 1)[1]
    console.print("Restarting server for the new public host…")
    _kill_proc(srv)
    if not _wait_port_free(ctx.port):
        console.print(f"[red]port {ctx.port} never freed — see mcp.log[/]")
        return FlowResult(False, reason="reboot")
    env["MCP_PUBLIC_HOST"] = host
    srv = _boot_server(ctx.py, env, ctx.repo, ctx.repo / "mcp.log")
    if not _wait_health(ctx.port):
        _kill_proc(srv)
        console.print("[red]server did not come up on retry — see mcp.log[/]")
        return FlowResult(False, reason="reboot")
    console.print("Verifying public endpoint…")
    if not _public_init_ok(f"{url}/mcp/", ctx.secret):
        _kill_proc(srv)
        console.print("[red]public check failed — run dots stop then start again[/]")
        return FlowResult(False, reason="verify")
    if leave_running:
        return FlowResult(True, public_url=f"{url}/mcp/", host=host,
                          server_proc=srv, started_at=time.monotonic())
    _kill_proc(srv)
    _wait_port_free(ctx.port)
    return FlowResult(True, public_url=f"{url}/mcp/", host=host)


def json_result(ok: bool, ctx: StartCtx, res: FlowResult, oauth: dict) -> str:
    return json.dumps({
        "ok": ok,
        "public_url": res.public_url if ok else "",
        "local": f"http://127.0.0.1:{ctx.port}/mcp/",
        "issuer": f"https://{res.host}" if ok else "",
        "client_id": oauth.get("client_id", ""),
        "client_secret": oauth.get("client_secret", ""),
        "owner_secret": ctx.secret,
        "port": ctx.port,
        "reason": res.reason,
    }, indent=2)


# ---------------------------------------------------------------- live mode

class LogTee(threading.Thread):
    """Pipe server stdout → mcp.log file (0600). No UI reads this."""

    def __init__(self, proc, logfile: Path):
        super().__init__(daemon=True)
        self.proc = proc
        self._logfile = logfile

    def run(self):
        try:
            fh = open(self._logfile, "ab")
            try:
                os.chmod(self._logfile, 0o600)
            except Exception:
                pass
        except Exception:
            fh = None
        try:
            for raw in iter(self.proc.stdout.readline, b""):
                if fh:
                    try:
                        fh.write(raw)
                        fh.flush()
                    except Exception:
                        pass
        except Exception:
            pass
        finally:
            try:
                if fh:
                    fh.close()
            except Exception:
                pass


def _boot_piped(py: str, env: dict, repo: Path):
    return subprocess.Popen([py, "-m", "dot_mcp.server"], cwd=str(repo), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, start_new_session=True)


def interactive(ctx: StartCtx, state_dir: Path) -> int:
    """Full TUI: checklist → staged panels → single-frame live render."""
    console = make_console()

    steps = [Step("Starting server"), Step("Opening tunnel"),
             Step("Restarting for public host"), Step("Verifying endpoint")]
    _screen_setup()
    try:
        last = [0.0]

        def draw():
            if time.monotonic() - last[0] < 0.08:
                return
            last[0] = time.monotonic()
            _paint(lambda w: steps_render(steps))
        res = run_flow(ctx, state_dir, draw)
    finally:
        _screen_teardown()
    if not res.ok:
        console.print(steps_render(steps))
        console.print(f"[red]Failed: {res.reason} — see mcp.log / tunnel.log[/]")
        return 1
    oauth = _read_oauth_client(ctx.data_dir)  # static client is created at boot

    # hand the verified server to the live loop: reboot one piped child
    _kill_proc(res.server_proc)
    end = time.time() + 20
    while time.time() < end and port_in_use(ctx.port):
        time.sleep(1)
    env = dict(ctx.env, MCP_PUBLIC_HOST=res.host)
    child = _boot_piped(ctx.py, env, ctx.repo)
    if not _wait_health(ctx.port):
        _kill_proc(child)
        console.print("[red]server did not come up — see mcp.log[/]")
        return 1
    tee = LogTee(child, ctx.repo / "mcp.log")
    tee.start()
    tee = LogTee(child, ctx.repo / "mcp.log")
    tee.start()
    action = _live_loop(console, ctx, child, res.public_url, f"https://{res.host}",
                        oauth.get("client_id", ""), oauth.get("client_secret", ""))
    if action == "restart":
        _kill_proc(child)
        try:
            old = int((ctx.repo / "logs" / "tunnl.pid").read_text().strip())
        except Exception:
            old = None
        return interactive_restart(ctx, state_dir, oauth, old)
    _kill_proc(child)
    _kill_tunnel(ctx.repo)
    console.print("[dim]Stopped.[/]")
    sys.stdout.flush()
    os._exit(0)  # child was SIGKILLed; exit now instead of joining the tee thread

def _tunnel_alive(repo: Path) -> bool:
    try:
        pid = int((repo / "logs" / "tunnl.pid").read_text().strip())
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _kill_tunnel(repo: Path) -> None:
    try:
        pid = int((repo / "logs" / "tunnl.pid").read_text().strip())
        try:
            os.kill(pid, _sig.SIGTERM)
        except Exception:
            pass
        try:
            (repo / "logs" / "tunnl.pid").unlink()
        except Exception:
            pass
    except Exception:
        pass


def _screen_setup() -> bool:
    try:
        if not _is_tty():
            return False
        sys.stdout.write("\x1b[?1049h\x1b[2J\x1b[H\x1b[?25l")
        sys.stdout.flush()
        return True
    except Exception:
        return False


def _screen_teardown() -> None:
    try:
        sys.stdout.write("\x1b[?25h\x1b[?1049l")
        sys.stdout.flush()
    except Exception:
        pass


def _paint(render_fn) -> None:
    """In-place frame: clear screen+scrollback, cursor home, then one render.

    Anti-stacking notes:
      - double clear (with/without scrollback) covers terminals that ignore one
      - a fresh Console per frame with today's width defeats stale-width caching
      - render_fn(width) gets the exact width, so nothing reflows mid-frame
    """
    import shutil as _sh
    from rich.console import Console
    width = 80
    try:
        width = _sh.get_terminal_size().columns or 80
    except Exception:
        pass
    if not _use_color():
        pass
    try:
        sys.stdout.write("\x1b[2J\x1b[3J\x1b[2J\x1b[H")
        sys.stdout.flush()
    except Exception:
        pass
    console = Console(width=width, no_color=not _use_color(),
                      force_terminal=_is_tty())
    console.print(render_fn(width))
    sys.stdout.flush()
def _live_loop(console, ctx: StartCtx, child, pub: str, issuer: str,
               cid: str, csec: str) -> str:
    """Hand-rolled live render: paint the whole frame in place each tick.

    Uses alt-screen + home/clear (no Live) so terminals that cannot fully
    reflow a Rich Live frame do not end up with stacked panels.
    """
    from rich.console import Group
    masked = [True]
    flash = [""]
    flash_until = [0.0]
    t0 = time.monotonic()
    keys = KeyReader()
    keys.start()
    tee = LogTee(child, ctx.repo / "mcp.log")
    tee.start()

    def render():
        up = time.monotonic() - t0
        srv_ok = port_in_use(ctx.port)
        tun_ok = _tunnel_alive(ctx.repo)
        parts = [header_render(console, up, tun_ok, srv_ok, tunnel_hits(ctx.repo)),
                 connector_panel(pub, issuer, cid, csec, ctx.secret, masked[0]),
                 ready_panel(ctx.secret, masked[0])]
        fmsg = flash[0] if time.time() < flash_until[0] else ""
        parts.append(footer_render(fmsg))
        return Group(*parts)

    screen = _screen_setup()
    action = "quit"
    stop = False
    try:
        while not stop:
            _paint(lambda w: render())
            for _ in range(3):  # poll keys over ~0.15s between frames
                k = keys.get()
                if k in ("q", "\x03"):
                    action, stop = "quit", True
                    break
                if k == "c" and pub:
                    flash[0] = clipboard_notice(clipboard_copy(pub))
                    flash_until[0] = time.time() + 3
                elif k == "s":
                    masked[0] = not masked[0]
                    flash[0] = "secrets revealed" if not masked[0] else "secrets masked"
                    flash_until[0] = time.time() + 3
                elif k == "r":
                    action, stop = "restart", True
                    break
                time.sleep(0.05)
            if not stop and child.poll() is not None:
                flash[0] = "server exited — r to restart, q to quit"
                flash_until[0] = time.time() + 5
    finally:
        keys.stop()
        if screen:
            _screen_teardown()
    return action


def interactive_restart(ctx: StartCtx, state_dir: Path, oauth: dict, tunnel_pid) -> int:
    """Restart only the server child (tunnel + URL stay). Single-frame render."""
    console = make_console()
    end = time.time() + 20
    while time.time() < end and port_in_use(ctx.port):
        time.sleep(1)
    env = dict(ctx.env)
    host = None
    try:
        m = _TUNNL_RE.search((ctx.repo / "tunnel.log").read_text(errors="replace"))
        host = m.group(0).split("://", 1)[1] if m else None
    except Exception:
        host = None
    if host:
        env["MCP_PUBLIC_HOST"] = host
    child = _boot_piped(ctx.py, env, ctx.repo)
    if not _wait_health(ctx.port):
        _kill_proc(child)
        console.print("[red]server did not come back — see mcp.log[/]")
        return 1
    pub = f"https://{host}/mcp/" if host else ""
    action = _live_loop(console, ctx, child, pub, f"https://{host or ''}",
                        oauth.get("client_id", ""), oauth.get("client_secret", ""))
    if action == "restart":
        _kill_proc(child)
        return interactive_restart(ctx, state_dir, oauth, None)
    _kill_proc(child)
    _kill_tunnel(ctx.repo)
    sys.stdout.flush()
    os._exit(0)  # child was SIGKILLed; exit now instead of joining the tee thread
