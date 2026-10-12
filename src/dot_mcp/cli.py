#!/usr/bin/env python3
"""dot-mcp CLI: `dot-mcp start`, `dot-mcp status`, `dot-mcp stop`.

Works both from a source checkout (src/ layout) and as an installed package
(`pip install dot-mcp`). Defaults: port 33041, data in ~/.dot-mcp.
"""
from __future__ import annotations
import argparse, json, os, secrets, subprocess, sys
from pathlib import Path

from dot_mcp import orchestrator as ui
from dot_mcp import lifecycle


def _repo_root() -> Path:
    """Source checkout root if present (for .venv + logs), else the data dir."""
    here = Path(__file__).resolve()          # <repo>/src/dot_mcp/cli.py
    repo = here.parent.parent.parent         # <repo> in a source checkout
    if (repo / "pyproject.toml").exists() or (repo / "dot_mcp.py").exists() or (repo / "src").is_dir():
        return repo
    data = Path(os.environ.get("DOT_MCP_DATA", str(Path.home() / ".dot-mcp")))
    data.mkdir(parents=True, exist_ok=True)
    return data


REPO = _repo_root()
STATE_DIR = lifecycle.state_dir()
SECRET_FILE = STATE_DIR / "owner_secret"
DEFAULT_PORT = 33041
DEFAULT_DATA = str(Path.home() / ".dot-mcp")

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich import box
    console = Console()
    HAS_RICH = True
except Exception:  # plain fallback
    HAS_RICH = False
    class _C:
        def print(self, *a, **k):
            print(*[str(x).replace("[", "").replace("]", "") for x in a])
    console = _C()  # type: ignore
    Panel = Table = None  # type: ignore

ACCENT = "cyan"
WARN = "yellow"
ERR = "red"
OK = "green"


def banner(sub: str = "") -> None:
    title = "[bold cyan]dot-mcp[/] [dim]personal MCP agent[/]"
    if sub:
        title += f"  [bold white]{sub}[/]"
    if HAS_RICH:
        console.print(Panel(title, box=box.ROUNDED, border_style=ACCENT))
    else:
        console.print(f"== dot-mcp {sub} ==")


def get_secret() -> str:
    """Per-machine owner secret: read from disk, else generate + save (0600)."""
    try:
        s = SECRET_FILE.read_text(encoding="utf-8").strip()
        if s:
            return s
    except Exception:
        pass
    s = secrets.token_urlsafe(32)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=STATE_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(s + "\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, SECRET_FILE)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return s


def server_python() -> tuple[str, str | None]:
    """(python, src_dir). Source checkout: .venv if present + src on path.
    Installed package: this interpreter (dot_mcp is importable)."""
    src = REPO / "src"
    if (src / "dot_mcp" / "server.py").exists():
        venv = REPO / ".venv" / "bin" / "python"
        py = str(venv) if (venv.is_file() and os.access(venv, os.X_OK)) else sys.executable
        return py, str(src)
    return sys.executable, None


def _start_oauth_client(data_dir: str) -> dict:
    """Read the pre-provisioned OAuth client the server writes at boot."""
    try:
        st = json.loads((Path(data_dir) / "oauth.json").read_text(encoding="utf-8"))
        sc = st.get("static_client")
        if isinstance(sc, dict) and sc.get("client_id"):
            return sc
    except Exception:
        pass
    return {}


def cmd_start(args: argparse.Namespace) -> int:
    try:
        with lifecycle.instance_lock(REPO):
            if ui.port_in_use(args.port or DEFAULT_PORT):
                if args.json:
                    print(json.dumps({"ok": False, "reason": "port_in_use"}))
                else:
                    print("Port already in use; check dot-mcp status before starting.", file=sys.stderr)
                return 1
            return _cmd_start(args)
    except (RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _cmd_start(args: argparse.Namespace) -> int:
    port = args.port or DEFAULT_PORT
    data_dir = args.data_dir or DEFAULT_DATA
    if getattr(args, "vault", False):
        if args.json or not sys.stdin.isatty():
            print("Vault remains locked in noninteractive/JSON startup; use a private local terminal to unlock.", file=sys.stderr)
        else:
            from dot_mcp.vault import main as vault_main
            console.print("Starting foreground with a local vault prompt. No tunnel is started; use dots tunnel in another terminal. Ctrl+C stops this server.")
            os.environ["MCP_PORT"] = str(port)
            os.environ["MCP_HOST"] = "127.0.0.1"
            # Public issuer must be configured by the human before boot. The
            # passphrase stays in this foreground process, never child env/args.
            return vault_main(["serve", "--data-dir", str(Path(data_dir).resolve())])
    # Owner secret: stable per machine (env wins, else file, else generate+save).
    secret = (os.environ.get("MCP_PATH_SECRET", "").strip() or get_secret())
    py, src_dir = server_python()
    env = dict(os.environ, MCP_PORT=str(port), MCP_HOST="127.0.0.1",
               DOT_MCP_DATA=data_dir, MCP_PATH_SECRET=secret)
    if src_dir:  # source checkout: make `dot_mcp` importable for -m
        env["PYTHONPATH"] = src_dir + os.pathsep + env.get("PYTHONPATH", "")
    try:
        v = subprocess.run([py, "-c", "import mcp; print('mcp-ok')"],
                           capture_output=True, text=True, timeout=30, env=env).stdout.strip()
        assert "mcp-ok" in v
    except Exception:
        print(f"no mcp package in {py} — run ./setup.sh first", file=sys.stderr)
        return 1
    ctx = ui.StartCtx(port=port, data_dir=data_dir, secret=secret, py=py,
                      env=env, repo=REPO)

    if args.json:
        # script mode: full flow, JSON ONLY to stdout, server left running
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            res = ui.plain_flow(ctx, STATE_DIR, console, leave_running=True)
        oauth = _start_oauth_client(data_dir)
        sys.stdout.write(ui.json_result(res.ok, ctx, res, oauth) + "\n")
        return 0 if res.ok else 1

    banner("start")
    if ui.port_in_use(port):
        console.print(f"[{ERR}]port {port} is already in use — run dot-mcp status or dot-mcp stop first[/]")
        return 1

    # Run the full flow quietly (no spinner, no live output) — then show results once.
    # leave_running=True: the verified server stays up as a detached background
    # process, so we can return to the shell instead of blocking it.
    import contextlib, io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        res = ui.plain_flow(ctx, STATE_DIR, console, leave_running=True)
    if not res.ok:
        print(f"Start failed: {res.reason}. Check mcp.log and tunnel.log.", file=sys.stderr)
        return 1

    # Show all info in one shot
    host = res.host
    public_mcp = res.public_url
    oauth = _start_oauth_client(data_dir)
    t = Table("", "", box=box.SIMPLE, show_header=False)
    t.add_row("Public URL", public_mcp)
    t.add_row("Browser + vault", "optional installs; vault locked by default")
    t.add_row("Owner secret", "hidden (use dots credentials --show locally)")
    t.add_row("OAuth Client ID", oauth.get("client_id", "—"))
    t.add_row("OAuth Client secret", "hidden")
    console.print(t)
    console.print(Panel(
        f"[bold green]Running.[/] Paste the public URL into your connector (auth = OAuth).",
        title="ready", border_style=OK))
    console.print("[dim]Server running in the background — dots status / dots stop[/]\n")
    return 0


def cmd_doctor(args):
    """Local diagnostics without opening browser, revealing secrets or changing state."""
    import importlib.util
    import importlib.metadata
    import urllib.request
    py, src = server_python()
    report = {"interpreter": py, "source": src, "packages": {}, "optional": {}}
    for name in ("mcp", "uvicorn", "playwright", "cryptography", "websockets"):
        try: report["packages"][name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError: report["packages"][name] = "not installed"
    from dot_mcp.browser import browser_executable, browser_dependencies
    report["optional"]["browser_executable"] = browser_executable() or "Playwright-managed (not verified)"
    report["optional"]["browser_missing"] = browser_dependencies()["missing"]
    report["vault"] = "locked by default; human start --vault or vault serve required"
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{args.port or DEFAULT_PORT}/health", timeout=3) as r:
            health = json.load(r)
        report["running"] = {"ok": health.get("ok"), "tools": health.get("tools")}
    except Exception:
        report["running"] = {"ok": False, "tools": None}
    # Report current interpreter packages, never claim it proves a different
    # server interpreter has the same packages.
    report["package_interpreter"] = sys.executable
    if args.json: print(json.dumps(report, indent=2))
    elif HAS_RICH:
        table = Table("Check", "Result", box=box.SIMPLE)
        table.add_row("Selected server Python", py)
        table.add_row("Current diagnostic Python", sys.executable)
        table.add_row("Source", src or "installed package")
        for name, value in report["packages"].items(): table.add_row(name, value)
        table.add_row("Browser executable", str(report["optional"]["browser_executable"]))
        table.add_row("Missing browser packages/executable", ", ".join(report["optional"]["browser_missing"]) or "none found; managed browser/launch not verified")
        table.add_row("Running tool count", str(report["running"]["tools"] or "server unavailable"))
        table.add_row("Vault", report["vault"])
        console.print(Panel(table, title="dot-mcp doctor", border_style=ACCENT))
        console.print("If ChatGPT sees old tools: restart the patched server, verify live tool count, refresh the connector. Never substitute separate-process browser scripts for server browser_* tools.")
    else: print(json.dumps(report, indent=2))
    return 0


def cmd_credentials(args):
    data = args.data_dir or DEFAULT_DATA
    oauth = _start_oauth_client(data)
    if not args.show:
        console.print("Credentials are hidden by default.")
        console.print(f"Owner credential file: {SECRET_FILE}")
        console.print(f"OAuth credential file: {Path(data) / 'oauth.json'}")
        console.print("Use dots credentials --show only in a private local terminal. Never paste into AI chat.")
        return 0
    if not sys.stdout.isatty() or not sys.stdin.isatty():
        print("Credential reveal requires a private interactive terminal; redirection refused.", file=sys.stderr)
        return 1
    console.print("These secrets grant owner access. Do not record this terminal or paste into chat.")
    if input("Type REVEAL to continue: ") != "REVEAL":return 1
    secret = os.environ.get("MCP_PATH_SECRET") or (SECRET_FILE.read_text().strip() if SECRET_FILE.exists() else "not provisioned")
    # Literal output prevents rich markup in user-owned configuration values.
    print("Owner secret:", secret)
    print("OAuth Client ID:", oauth.get("client_id", "not provisioned"))
    print("OAuth Client secret:", oauth.get("client_secret", "not provisioned"))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    port = args.port or DEFAULT_PORT
    try:
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as r:
            info = json.loads(r.read().decode())
        if HAS_RICH:
            table = Table("Service", "State", box=box.SIMPLE, show_header=False)
            table.add_row("Server", f"running on 127.0.0.1:{port}")
            table.add_row("Registered tools", str(info.get("tools", "unknown")))
            table.add_row("Home", str(info.get("home", "unknown")))
            console.print(Panel(table, title="dot-mcp status", border_style=OK))
        else:
            console.print(f"running port={port} tools={info.get('tools')}")
        return 0
    except Exception:
        console.print(f"[{ERR}]○ stopped[/] port {port} (no server)")
        return 1


def cmd_stop(_: argparse.Namespace) -> int:
    try:
        with lifecycle.instance_lock(REPO):
            server = lifecycle.stop(REPO / "logs" / "server.json")
            tunnel = lifecycle.stop(REPO / "logs" / "tunnel.json")
            (REPO / "logs" / "tunnl.pid").unlink(missing_ok=True)
        console.print(f"Stopped owned processes: server={server}, tunnel={tunnel}.")
        return 0
    except (RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    import sys
    route = list(sys.argv[1:] if argv is None else argv)
    if route and route[0] == "tunnel":
        from dot_mcp.tunnel import main as tunnel_main
        return tunnel_main(route[1:])
    if route and route[0] == "vault":
        from dot_mcp.vault import main as vault_main
        return vault_main(route[1:])
    ap = argparse.ArgumentParser(prog="dot-mcp", description="dot-mcp personal agent CLI")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("start", help="run server + tunnel in background; --vault unlocks a foreground server")
    p.add_argument("--port", type=int, default=0, help="override port (default 33041)")
    p.add_argument("--data-dir", default="", help="override data dir (default ~/.dot-mcp)")
    p.add_argument("--json", action="store_true", help="script mode: JSON result, server left running")
    p.add_argument("--vault", action="store_true", help="human local unlock + foreground server (no automatic tunnel); noninteractive stays locked")
    q = sub.add_parser("status", help="check if the server is running")
    q.add_argument("--port", type=int, default=0, help="override port (default 33041)")
    sub.add_parser("stop", help="stop server + tunnel")
    doctor = sub.add_parser("doctor", help="read-only runtime/package/tool-count diagnostics")
    doctor.add_argument("--port", type=int, default=0)
    doctor.add_argument("--json", action="store_true")
    creds = sub.add_parser("credentials", help="inspect credential locations; --show reveals only in a private terminal")
    creds.add_argument("--data-dir", default="")
    creds.add_argument("--show", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "start":
        return cmd_start(args)
    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "stop":
        return cmd_stop(args)
    if args.cmd == "doctor":
        return cmd_doctor(args)
    if args.cmd == "credentials":
        return cmd_credentials(args)
    banner()
    console.print("Commands: [bold]start[/] · [bold]status[/] · [bold]stop[/]\nExample: [cyan]dot-mcp start[/]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
