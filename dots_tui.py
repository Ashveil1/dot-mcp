#!/usr/bin/env python3
"""dots_tui — Textual TUI for `dots start`. Blue ANSI/ASCII theme.

Phases
  0. boot   : ASCII logo types itself in, rule line blinks, tagline appears
  1. startup: checklist — spinner → ✓ + elapsed per phase
  2. live   : header (● RUNNING pulse, uptime, tunnel, hits) + connector
              + ready panels slide in, footer legend

Keys: q quit · c copy URL · s show secrets · r restart · l logs.
No rich.Live, no stacked panels: Textual owns the layout.
"""
from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

VERSION = "0.4.0"
SPIN = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

LOGO = [
    " ██████╗  ██████╗ ████████╗███████╗",
    " ██╔══██╗██╔═══██╗╚══██╔══╝██╔════╝",
    " ██║  ██║██║   ██║   ██║   ███████╗",
    " ██║  ██║██║   ██║   ██║   ╚════██║",
    " ██████╔╝╚██████╔╝   ██║   ███████║",
    " ╚═════╝  ╚═════╝    ╚═╝   ╚═════╝",
]
RULE = "─" * 44
TAGLINE = "personal MCP agent · capability URL + OAuth"


def mask(secret: str) -> str:
    if len(secret) <= 9:
        return "•" * len(secret)
    return f"{secret[:4]}…{secret[-3:]}"


# ------------------------------------------------------- orchestration

def port_in_use(port: int) -> bool:
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except Exception:
        return False


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


def _wait_port_free(port: int, timeout: int = 20) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if not port_in_use(port):
            return True
        time.sleep(1)
    return not port_in_use(port)


def _boot(py: str, env: dict, repo: Path, piped: bool = False):
    if piped:
        return subprocess.Popen([py, "dot_mcp.py"], cwd=str(repo), env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
    log = repo / "mcp.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    fh = open(log, "wb")
    try:
        os.chmod(log, 0o600)
    except Exception:
        pass
    return subprocess.Popen([py, "dot_mcp.py"], cwd=str(repo), env=env,
                            stdout=fh, stderr=subprocess.STDOUT,
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
_HIT_RE = re.compile(r"^\s*\d{1,2}:\d{2}(:\d{2})?\s+(GET|POST|DELETE)\s+\S+\s+\d{3}\b")


def _start_tunnl(repo: Path, port: int, key: Path) -> str:
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
    (repo / "logs" / "tunnl.pid").parent.mkdir(parents=True, exist_ok=True)
    (repo / "logs" / "tunnl.pid").write_text(str(proc.pid))
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


def _tunnel_alive(repo: Path) -> bool:
    try:
        os.kill(int((repo / "logs" / "tunnl.pid").read_text().strip()), 0)
        return True
    except Exception:
        return False


def _kill_tunnel(repo: Path) -> None:
    try:
        os.kill(int((repo / "logs" / "tunnl.pid").read_text().strip()), 15)
    except Exception:
        pass
    try:
        (repo / "logs" / "tunnl.pid").unlink()
    except Exception:
        pass


def tunnel_hits(repo: Path) -> int:
    try:
        with open(repo / "tunnel.log", errors="replace") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 200_000))
            return sum(1 for line in fh if _HIT_RE.match(line))
    except Exception:
        return 0


def read_oauth_client(data_dir: str) -> dict:
    try:
        st = json.loads((Path(data_dir) / "oauth.json").read_text(encoding="utf-8"))
        sc = st.get("static_client")
        if isinstance(sc, dict) and sc.get("client_id"):
            return sc
    except Exception:
        pass
    return {}


# ------------------------------------------------------- clipboard

def clipboard_copy(text: str) -> bool:
    """Try native tools first (wayland/x11/mac/android), then OSC 52 terminal escape."""
    import shutil as _sh
    # native clipboard tools
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
    # OSC 52 — works in most modern terminals (kitty, alacritty, wezterm, etc.)
    try:
        import base64
        encoded = base64.b64encode(text.encode()).decode()
        sys.stdout.write(f"\x1b]52;c;{encoded}\x1b\\")
        sys.stdout.flush()
        return True
    except Exception:
        pass
    return False


# ------------------------------------------------------- data

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


class FlowFailure(RuntimeError):
    pass


def run_startup(ctx: StartCtx, state_dir: Path, tick) -> dict:
    """Boot → tunnel → reboot for public host → verify. Returns live child+URL."""
    import threading
    env = dict(ctx.env)
    steps = [Step("Starting server"), Step("Opening tunnel"),
             Step("Restarting for public host"), Step("Verifying endpoint")]

    def phase(i, fn):
        st = steps[i]
        st.state = "run"
        tick(steps)  # show spinner immediately
        stop = threading.Event()
        t0 = time.monotonic()

        def anim():
            while not stop.is_set():
                tick(steps)
                time.sleep(0.09)

        th = threading.Thread(target=anim, daemon=True)
        th.start()
        try:
            out = fn()
        except Exception as e:
            stop.set()
            raise FlowFailure(f"{steps[i].label}: {e}") from e
        stop.set()
        st.state = "ok"
        st.elapsed = time.monotonic() - t0
        tick(steps)
        time.sleep(1.5)  # hold each ✓ so steps complete visibly one by one
        return out

    def _boot0():
        srv = _boot(ctx.py, env, ctx.repo)
        if not _wait_health(ctx.port):
            _kill_proc(srv)
            raise RuntimeError("no health — see mcp.log")
        return srv

    srv = phase(0, _boot0)

    def _tun():
        key = _ensure_tunnl_key(state_dir)
        return _start_tunnl(ctx.repo, ctx.port, key)

    url = phase(1, _tun)
    host = url.split("://", 1)[1]

    def _reboot():
        _kill_proc(srv)
        end = time.time() + 20
        while time.time() < end and port_in_use(ctx.port):
            time.sleep(1)
        env["MCP_PUBLIC_HOST"] = host
        srv2 = _boot(ctx.py, env, ctx.repo)
        if not _wait_health(ctx.port):
            _kill_proc(srv2)
            raise RuntimeError("no health — see mcp.log")
        return srv2

    srv = phase(2, _reboot)

    def _verify():
        pub = f"{url}/mcp/"
        if not _public_init_ok(pub, ctx.secret):
            _kill_proc(srv)
            raise RuntimeError("public check failed — dots stop, then start again")
        return pub

    pub = phase(3, _verify)

    # hand over to the live loop as a piped child (mcp.log keeps the output)
    _kill_proc(srv)
    end = time.time() + 20
    while time.time() < end and port_in_use(ctx.port):
        time.sleep(1)
    child = _boot(ctx.py, dict(env, MCP_PUBLIC_HOST=host), ctx.repo, piped=True)
    if not _wait_health(ctx.port):
        _kill_proc(child)
        raise FlowFailure("server did not come up — see mcp.log")
    return {"child": child, "pub": pub, "host": host}


# ------------------------------------------------------- blue palette

BG = "#0b0f1a"        # deep blue-black
PANEL = "#111827"     # panel dark blue-grey
LINE = "#2563eb"      # blue border
CYAN = "#60a5fa"      # light blue accent
BLUE = "#3b82f6"      # primary blue
STEEL = "#94a3b8"     # label blue-grey
DIM = "#475569"       # muted slate
TEXT = "#f1f5f9"      # near white
GOOD = "#e2e8f0"      # white (success)
BAD = "#ff4444"       # red (error)


def build_app(ctx: StartCtx, state_dir: Path):
    from textual.app import App, ComposeResult
    from textual.containers import Vertical
    from textual.widgets import Static
    from textual import events

    class DotsApp(App):
        TITLE = "dots"
        CSS = f"""
        Screen {{ background: {BG}; align: center middle; }}
        #body {{ width: 100%; max-width: 104; padding: 0 1; height: auto; }}
        #boot {{ height: auto; }}
        #logo {{ color: {CYAN}; text-style: bold; }}
        #rule {{ color: {LINE}; }}
        #tagline {{ color: {DIM}; padding: 0 0 1 0; }}
        #steps {{ height: auto; padding: 0 0 1 0; }}
        #header {{ color: {CYAN}; text-style: bold; padding: 0 0 1 0; }}
        #connector {{
            border: round {LINE}; background: {PANEL};
            padding: 0 1; margin: 0 0 1 0; height: auto;
            border-title-align: center; border-title-color: {CYAN};
        }}
        #ready {{
            border: round {BLUE}; background: {PANEL};
            padding: 0 1; margin: 0 0 1 0; height: auto;
            border-title-align: center; border-title-color: {BLUE};
        }}
        #logs {{ color: {DIM}; height: auto; padding: 0 0 1 0; }}
        #footer {{ color: {DIM}; }}
        .fail {{ color: {BAD}; }}
        """

        def __init__(self):
            super().__init__()
            self.masked = False  # show secrets by default; press s to mask
            self.show_logs = False
            self.flash = ""
            self.flash_until = 0.0
            self.steps = [Step("Starting server"), Step("Opening tunnel"),
                          Step("Restarting for public host"), Step("Verifying endpoint")]
            self.t0 = time.monotonic()
            self.pub = ""
            self.host = ""
            self.child = None
            self.oauth = read_oauth_client(ctx.data_dir)
            self._log_lines: list[str] = []
            self.exit_mode = "quit"
            self.failed = ""
            self._pulse = True
            self._hits_last = -1
            # boot animation state
            self._boot_stage = 0       # 0=dot 1=logo 2=flash 3=rule 4=tag 5=checks 6=done
            self._stage_tick = 0
            self._logo_lines = 0
            self._tag_chars = 0
            self._check_idx = 0
            self._flash_done = False
            self._steps_done = False  # once true, checklist never re-renders

        # ---------- compose
        def compose(self) -> ComposeResult:
            with Vertical(id="body"):
                yield Static("", id="logo", markup=True)
                yield Static("", id="rule", markup=True)
                yield Static("", id="tagline", markup=True)
                yield Static("", id="steps", markup=True)
                yield Static("", id="header", markup=True)
                yield Static("", id="connector", markup=True)
                yield Static("", id="ready", markup=True)
                yield Static("", id="logs", markup=True)
                yield Static("", id="footer", markup=True)

        # ---------- boot animation
        def on_mount(self):
            self.query_one("#steps").display = False
            self._steps_done = True  # checklist never shown; guard all updates
            for wid in ("#header", "#connector", "#ready", "#footer"):
                self.query_one(wid).display = False
            self.set_interval(0.05, self._boot_tick)      # type logo + blink rule
            self.set_interval(30, self._tick_live)        # slow header refresh (uptime ticks every 30s)
            self.query_one("#connector").border_title = "Connector"
            self.query_one("#ready").border_title = "Ready"

        def _boot_tick(self):
            self._stage_tick += 1
            if self._boot_stage == 0:
                # pulsing center dot — 3 pulses over ~0.6s
                dot = self.query_one("#logo")
                if self._stage_tick <= 12:
                    on = (self._stage_tick // 3) % 2 == 0
                    dot.update(f"[{CYAN if on else DIM}]●[/]")
                else:
                    dot.update("")
                    self._boot_stage = 1
                    self._stage_tick = 0
                    self._logo_lines = 0
            elif self._boot_stage == 1:
                # type logo line by line (~0.1s per line)
                if self._stage_tick % 2 == 0 and self._logo_lines < len(LOGO):
                    self._logo_lines += 1
                    self.query_one("#logo").update(
                        "\n".join(LOGO[: self._logo_lines]))
                if self._logo_lines >= len(LOGO) and self._stage_tick > len(LOGO) * 2 + 6:
                    self._boot_stage = 2
                    self._stage_tick = 0
            elif self._boot_stage == 2:
                # bright flash sweep across the logo
                if not self._flash_done:
                    self._flash_done = True
                    self.query_one("#logo").update(
                        "\n".join(f"[bright_white]{line}[/]" for line in LOGO))
                    self._de("timer: flash_off")
                    self.set_timer(0.15, self._flash_off)
            elif self._boot_stage == 3:
                # draw rule left→right with a bright head
                w = min(len(RULE), self._stage_tick * 2)
                head = f"[bright_white]{RULE[:max(0, w-4)]}[/]" if w > 4 else ""
                tail = f"[white]{RULE[max(0, w-4):w]}[/]"
                self.query_one("#rule").update(head + tail)
                if w >= len(RULE):
                    self._boot_stage = 4
                    self._stage_tick = 0
                    self._tag_chars = 0
            elif self._boot_stage == 4:
                # type tagline char by char
                if self._stage_tick % 2 == 0 and self._tag_chars < len(TAGLINE):
                    self._tag_chars += 1
                tag = TAGLINE[: self._tag_chars]
                self.query_one("#tagline").update(
                    f"[{DIM}]{tag}[/]  [{STEEL}]v{VERSION}[/]")
                if self._tag_chars >= len(TAGLINE) and self._stage_tick > len(TAGLINE) * 2 + 8:
                    self._boot_stage = 5
                    self._stage_tick = 0
                    self._check_idx = 0
            elif self._boot_stage == 5:
                # boot checks appear one by one
                checks = ["checking environment", "loading configuration",
                          "preparing tunnel"]
                if self._stage_tick % 6 == 0 and self._check_idx < len(checks):
                    self._check_idx += 1
                lines = [f"[{GOOD}]  ✓ {c}[/]" for c in checks[: self._check_idx]]
                self.query_one("#tagline").update(
                    f"[{DIM}]{TAGLINE}[/]  [{STEEL}]v{VERSION}[/]\n" + "\n".join(lines))
                if self._check_idx >= len(checks) and self._stage_tick > len(checks) * 6 + 10:
                    self._boot_stage = 6
                    self._stage_tick = 0
                    self._de("timer: begin_steps")
                    self.set_timer(0.4, self._begin_steps)

        def _flash_off(self):
            self.query_one("#logo").update("\n".join(LOGO))
            self._boot_stage = 3
            self._stage_tick = 0

        def _begin_steps(self):
            self._de("begin_steps called")
            self.query_one("#rule").display = False
            self.query_one("#tagline").update(
                f"[{DIM}]{TAGLINE}[/]  [{STEEL}]v{VERSION}[/]")
            self.query_one("#steps").display = False  # checklist never shown
            self.run_worker(self._do_startup, thread=True, exclusive=True)

        def _on_all_steps_done(self):
            self._de("on_all_steps_done called")
            self._steps_done = True  # stop all further checklist re-renders
            try:
                self.query_one("#steps").display = False
            except Exception:
                pass
            # No checklist — brief "ready" beat, then live frame
            self.query_one("#tagline").update(
                f"[{DIM}]{TAGLINE}[/]  [{GOOD}]ready[/]")
            self._de("timer: show_live")
            self.set_timer(0.4, self._show_live)

        # ---------- failure debug
        def _de(self, msg: str) -> None:
            try:
                with open(ctx.repo / "dots-debug.log", "a", encoding="utf-8") as fh:
                    fh.write(time.strftime("%H:%M:%S ") + msg + "\n")
            except Exception:
                pass

        # ---------- startup worker
        def _do_startup(self):
            self._de("worker start")
            try:
                res = run_startup(ctx, state_dir, self._tick_steps)
            except BaseException as e:  # visible failure is the point
                import traceback
                self.failed = f"{type(e).__name__}: {e}"
                self._de("failed: " + self.failed + "\n" + traceback.format_exc())
                self.call_from_thread(self._show_failure)
                return
            self.pub, self.host, self.child = res["pub"], res["host"], res["child"]
            self._de(f"live: pub={self.pub} host={self.host}")
            self.call_from_thread(self._on_all_steps_done)

        def _tick_steps(self, steps):
            if not self._steps_done:
                self.call_from_thread(self._update_steps)

        def _update_steps(self):
            if self._steps_done:
                self._de("update_steps BLOCKED (already done)")
                return
            try:
                self.query_one("#steps").update(self._steps_text())
            except Exception:
                self._de("steps error\n" + __import__("traceback").format_exc())

        def _steps_text(self):
            out = []
            for st in self.steps:
                if st.state == "ok":
                    out.append(f"[{GOOD}]✓[/] [{TEXT}]{st.label}[/] "
                               f"[{DIM}]{st.elapsed:.1f}s[/]")
                elif st.state == "fail":
                    out.append(f"[{BAD}]✗ {st.label}  {st.detail}[/]")
                elif st.state == "run":
                    ch = SPIN[int(time.time() * 8) % len(SPIN)]
                    out.append(f"[{CYAN}]{ch}[/] [{TEXT}]{st.label}...[/]")
                else:
                    out.append(f"[{DIM}]  {st.label}[/]")
            return "\n".join(out)

        def _show_failure(self):
            if self._steps_done:
                return
            self.query_one("#steps").update(self._steps_text())
            footer = self.query_one("#footer")
            footer.update(f"[{BAD}]startup failed: {self.failed}[/]  [{DIM}](press q)[/]")
            footer.display = True

        def _show_live(self):
            self._de(f"show_live called (steps_done={self._steps_done}, timers={len(self._timers)})")
            try:
                self._de("show_live")
                self._steps_done = True
                # cancel EVERY timer so no boot/steps callback can re-render
                for timer in list(self._timers):
                    try:
                        timer.stop()
                    except Exception:
                        pass
                self.query_one("#logo").display = False
                self.query_one("#rule").display = False
                self.query_one("#tagline").display = False
                self.query_one("#steps").display = False
                # purge any residual animation frames from the screen
                try:
                    self.screen.styles.layer = "background"
                except Exception:
                    pass
                self.refresh(layout=True)
                for wid in ("#header", "#connector", "#ready", "#logs", "#footer"):
                    self.query_one(wid).display = True
                # staged slide-in for the two panels
                for wid, delay in (("#connector", 0.05), ("#ready", 0.18)):
                    w = self.query_one(wid)
                    try:
                        w.styles.offset_x = 6
                        w.styles.opacity = 0.0
                        self.set_timer(delay, lambda wref=w: self._slide_in(wref))
                    except Exception:
                        pass
                # tee server output → mcp.log (debug only)
                self.run_worker(self._tee, thread=True, exclusive=False)
                self._refresh()
                self._de("show_live done")
            except Exception:
                self._de("show_live error\n" + __import__("traceback").format_exc())
                raise

        def _slide_in(self, widget=None):
            try:
                widget.styles.opacity = 1.0
                widget.animate("offset_x", value=0, duration=0.35,
                               easing="out_cubic")
            except Exception:
                pass

        # ---------- live rendering
        def _header_text(self):
            up = time.monotonic() - self.t0
            srv_ok = port_in_use(ctx.port)
            tun_ok = _tunnel_alive(ctx.repo)
            hits = tunnel_hits(ctx.repo)
            dot = f"[{GOOD}]●[/]" if (srv_ok and self._pulse) else f"[{GOOD}]○[/]"
            status = (f"[{GOOD}]RUNNING[/]" if srv_ok else f"[{BAD}]DOWN[/]")
            changed = "" if hits == self._hits_last else f"[{CYAN}]▲[/]"
            self._hits_last = hits
            return (f"[{CYAN}]dots[/] [{STEEL}]v{VERSION}[/]  {dot} {status}   "
                    f"[{STEEL}]up[/] [{TEXT}]{int(up // 60)}m{int(up % 60):02d}s[/]   "
                    f"[{STEEL}]tunnel[/] [{GOOD if tun_ok else BAD}]"
                    f"{'ok' if tun_ok else 'down'}[/]   "
                    f"[{STEEL}]hits[/] [{TEXT}]{hits}[/]{changed}")

        def _fmt_connector(self):
            sec = mask if self.masked else (lambda s: s)
            url = self.pub or "—"
            return "\n".join([
                f"[{STEEL}]Public URL[/]     [{BLUE}]{url}[/]",
                f"[{STEEL}]Owner secret[/]   [{TEXT}]{sec(ctx.secret)}[/]",
                f"[{STEEL}]OAuth Client ID[/]     [{TEXT}]{self.oauth.get('client_id', '')}[/]",
                f"[{STEEL}]OAuth Client secret[/] [{TEXT}]{sec(self.oauth.get('client_secret', ''))}[/]",
            ])

        def _fmt_ready(self):
            shown = mask(ctx.secret) if self.masked else ctx.secret
            return "\n".join([
                f"[{BLUE}]1.[/] [{TEXT}]Paste the public URL into your connector "
                f"(auth = OAuth)[/]",
                f"[{BLUE}]2.[/] [{TEXT}]Enter the client ID / secret when asked[/]",
                f"[{DIM}]export MCP_PATH_SECRET={shown}[/]",
            ])

        def _fmt_footer(self):
            keys = (f"[{CYAN}]q[/] quit  [{CYAN}]s[/] secrets  [{CYAN}]r[/] restart  "
                    f"[{CYAN}]c[/] copy URL  [{CYAN}]y[/] copy secret")
            if self.flash and time.time() < self.flash_until:
                keys += f"   [{DIM}]— {self.flash}[/]"
            return keys

        def _refresh(self):
            try:
                self.query_one("#header").update(self._header_text())
                self.query_one("#connector").update(self._fmt_connector())
                self.query_one("#ready").update(self._fmt_ready())
                self.query_one("#footer").update(self._fmt_footer())
                if self.show_logs:
                    self.query_one("#logs").update("\n".join(self._log_lines[-8:]))
            except Exception:
                self._de("refresh error\n" + __import__("traceback").format_exc())

        def _tick_live(self):
            if not self.child:
                return
            self._pulse = not self._pulse  # status-dot breathe
            self._refresh()
            # realtime upstream: stream server output every tick
            if self.show_logs:
                try:
                    self.query_one("#logs").update(
                        "\n".join(self._log_lines[-10:]))
                except Exception:
                    pass
            if self.child.poll() is not None:
                self.flash = "server exited — r to restart, q to quit"
                self.flash_until = time.time() + 5

        # ---------- server output tee (→ mcp.log only)
        def _tee(self):
            try:
                fh = open(ctx.repo / "mcp.log", "ab")
                try:
                    os.chmod(ctx.repo / "mcp.log", 0o600)
                except Exception:
                    pass
            except Exception:
                fh = None
            proc = self.child
            try:
                for raw in iter(proc.stdout.readline, b""):
                    if fh:
                        try:
                            fh.write(raw)
                            fh.flush()
                        except Exception:
                            pass
                    try:
                        self._log_lines.append(raw.decode(errors="replace").rstrip()[:200])
                    except Exception:
                        pass
            except Exception:
                pass
            finally:
                for pipe in (proc.stdout, proc.stderr):
                    try:
                        if pipe is not None:
                            pipe.close()
                    except Exception:
                        pass

        def _close_child(self):
            proc = self.child
            if proc is None:
                return
            # kill the whole process group (ssh tunnels, child procs)
            try:
                import os
                os.killpg(os.getpgid(proc.pid), 15)
            except Exception:
                try:
                    proc.terminate()
                    proc.wait(timeout=8)
                except Exception:
                    try:
                        proc.kill()
                        proc.wait(timeout=5)
                    except Exception:
                        pass
            for pipe in (proc.stdout, proc.stderr):
                try:
                    if pipe is not None:
                        pipe.close()
                except Exception:
                    pass

        # ---------- keys (single handler — no BINDINGS double-fire)
        def on_key(self, event: events.Key) -> None:
            k = event.key
            self._de(f"key={k!r}")
            if k == "q":
                self.exit_mode = "quit"
                self.exit()
                return
            if k == "r":
                self.exit_mode = "restart"
                self.exit()
                return
            if k == "c":
                # copy URL directly — no mouse selection needed
                ok = clipboard_copy(self.pub) if self.pub else False
                self.flash = "URL copied" if ok else "copy failed"
                self.flash_until = time.time() + 4
                self._de(f"copied_url={ok}")
                self._refresh()
                return
            if k == "y":
                # copy owner secret directly
                ok = clipboard_copy(ctx.secret)
                self.flash = "secret copied" if ok else "copy failed"
                self.flash_until = time.time() + 4
                self._de(f"copied_secret={ok}")
                self._refresh()
                return
            elif k == "s":
                self.masked = not self.masked
                self.flash = "secrets revealed" if not self.masked else "secrets masked"
                self.flash_until = time.time() + 5
                self._de(f"masked={self.masked}")
            elif k == "l":
                self.show_logs = not self.show_logs
                self.query_one("#logs").display = self.show_logs
                # fast tick while logs visible, slow 30s when hidden
                for t in self._timers:
                    try:
                        t.stop()
                    except Exception:
                        pass
                if self.show_logs:
                    self.set_interval(0.5, self._tick_live)
                else:
                    self.set_interval(30, self._tick_live)
                if self.show_logs:
                    self.query_one("#logs").update("\n".join(self._log_lines[-10:]))
            self._refresh()

        # ---------- native copy: mouse-drag selection ----------
        def on_mouse_up(self, event) -> None:
            """Textual 8 has no auto-copy — grab selection and copy on release."""
            try:
                text = self.get_selected_text()
                if text and text.strip():
                    self.copy_to_clipboard(text)
                    self.flash = "copied to clipboard"
                    self.flash_until = time.time() + 3
            except Exception:
                pass

        def action_copy_text(self):
            """Fallback: copy whatever is currently selected."""
            try:
                text = self.get_selected_text()
                if text and text.strip():
                    self.copy_to_clipboard(text)
                    self.flash = "copied to clipboard"
                    self.flash_until = time.time() + 3
            except Exception:
                pass

        def action_copy_selected(self):
            """Copy the currently selected text (after mouse-drag)."""
            try:
                text = self.get_selected_text()
                if text and text.strip():
                    self.copy_to_clipboard(text)
                    self.flash = "copied to clipboard"
                    self.flash_until = time.time() + 3
                    self._de(f"copied={text[:40]!r}")
                else:
                    self.flash = "no selection to copy"
                    self.flash_until = time.time() + 2
            except Exception as e:
                self._de(f"copy error: {e}")

        def action_yank_selected(self):
            """Show selected text in the footer for manual copy."""
            try:
                text = self.get_selected_text()
                if text and text.strip():
                    self.flash = f"YANK: {text[:200]}"
                    self.flash_until = time.time() + 8
                    self._de("yank shown")
                else:
                    self.flash = "no selection to yank"
                    self.flash_until = time.time() + 2
            except Exception as e:
                self._de(f"yank error: {e}")
                # fast tick while logs visible, slow 30s when hidden
                for t in self._timers:
                    try:
                        t.stop()
                    except Exception:
                        pass
                if self.show_logs:
                    self.set_interval(0.5, self._tick_live)
                else:
                    self.set_interval(30, self._tick_live)
                if self.show_logs:
                    self.query_one("#logs").update("\n".join(self._log_lines[-10:]))
            self._refresh()

    return DotsApp


def run_interactive(ctx: StartCtx, state_dir: Path) -> int:
    """Run the TUI; 0 = quit, 42 = restart requested (caller loops)."""
    while True:
        app = build_app(ctx, state_dir)()
        app.run()
        app._close_child()
        if app.exit_mode != "restart":
            # sweep any orphaned server for this project, then drop the tunnel
            try:
                out = subprocess.run(["pgrep", "-f", "dot_mcp\\.py"],
                                     capture_output=True, text=True).stdout.split()
                import signal as _sig
                for pid in out:
                    try:
                        os.kill(int(pid), _sig.SIGTERM)
                    except Exception:
                        pass
            except Exception:
                pass
            _wait_port_free(ctx.port)
            _kill_tunnel(ctx.repo)
            return 0
        try:
            out = subprocess.run(["pgrep", "-f", "dot_mcp\\.py"],
                                 capture_output=True, text=True).stdout.split()
            for pid in out:
                try:
                    os.kill(int(pid), 9)
                except Exception:
                    pass
        except Exception:
            pass
        _wait_port_free(ctx.port)
