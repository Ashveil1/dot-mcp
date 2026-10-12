"""Optional local browser and capability-scoped live viewing/control.

Playwright runs on one private event-loop thread. There is no TCP CDP listener.
The live capability expires on close or after one hour. It does not authorize MCP.
"""
from __future__ import annotations
import asyncio
import base64
import concurrent.futures
import json
import importlib.util
import os
from pathlib import Path
import secrets
import shutil
import threading
import tempfile
import time
import uuid
from urllib.parse import urlsplit, urljoin

def browser_executable():
    """Explicit configured executable wins; otherwise prefer installed Chromium.

    Only binary discovery: never attach to a user's existing profile/session.
    None asks Playwright to use its own managed Chromium installation.
    """
    explicit = os.environ.get("DOT_MCP_BROWSER_EXECUTABLE")
    if explicit:
        return explicit
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    return None

def browser_dependencies():
    """Read-only checks in the server interpreter; no installers or subprocesses."""
    missing = []
    for package in ("playwright", "websockets"):
        if importlib.util.find_spec(package) is None:
            missing.append(package)
    executable = browser_executable()
    if executable and not (Path(executable).is_file() and os.access(executable, os.X_OK)):
        missing.append("configured Chrome/Chromium executable")
    return {"missing": missing, "executable": executable}


def not_installed(missing):
    """An actionable tool result, not permission to install anything."""
    return {"ok": False, "error": "not_installed", "missing": missing,
            "message": "not installed: " + ", ".join(missing),
            "next_action": "Ask the user whether to install or configure the missing dependencies. Do not install without user approval."}


class BrowserError(ValueError):
    pass

class LocalBrowser:
    def __init__(self, data_dir, base_url, artifact_callback=None):
        self.root = Path(data_dir) / "browser"
        self.profile = self.root / "profile"
        self.shots = self.root / "screenshots"
        self.base_url = base_url.rstrip("/")
        self.artifact_callback = artifact_callback
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.page = self.context = self.playwright = self.cdp = None
        self.refs = {}
        self.generation = 0
        self.tokens = {}
        self.controller = None
        self.clients = {}
        self.frames = 0
        self.started = 0
        self.last_frame = None
        self.expires = 0
        self.operation_lock = None
        self.browser_tmp = None
        self.fallback = None
        self.stream_mode = "cdp"
        self.sensitive = False
        self.share_view = False
        self.stream_quality = max(30, min(85, int(os.environ.get("DOT_MCP_STREAM_QUALITY", "55"))))
        self.stream_width = max(640, min(1280, int(os.environ.get("DOT_MCP_STREAM_WIDTH", "960"))))

    def call(self, action, **args):
        try:
            return asyncio.run_coroutine_threadsafe(self.perform(action, **args), self.loop).result(timeout=45)
        except Exception:
            if self.sensitive: raise BrowserError("vault privacy mode: operation unavailable or failed; page output blocked") from None
            raise

    async def perform(self, action, **args):
        if self.operation_lock is None:
            self.operation_lock = asyncio.Lock()
        async with self.operation_lock:
            if self.controller is not None and action not in ("close", "screenshot", "snapshot", "links", "share_view"):
                raise BrowserError("human control active; wait for the person to release control")
            result = await getattr(self, "_" + action)(**args)
            if self.sensitive and action != "close":
                # After fill, a site may reflect a credential anywhere, including URL/title.
                return {"ok": True, "privacy_mode": True, **({k:result[k] for k in ("filled","name","key") if k in result}), **await self._links(),
                        "note": "Vault fill privacy mode: page output and live video blocked until browser_close."}
            return result

    def _url(self, url):
        try:
            p = urlsplit(url)
            p.port
            if p.scheme not in ("http", "https") or not p.hostname or p.username is not None or p.password is not None or any(c.isspace() for c in url):
                raise ValueError()
        except ValueError:
            raise BrowserError("only HTTP/HTTPS URLs without embedded credentials are allowed") from None
        return url

    def _ready(self):
        if self.page is None or self.page.is_closed():
            raise BrowserError("browser is closed; call browser_open first")

    def _invalidate(self):
        self.refs = {}
        self.generation += 1

    async def _links(self):
        self._ready()
        return {"live_view_url": self.base_url + "/live#token=" + self.tokens["view"],
                "live_control_url": self.base_url + "/live#token=" + self.tokens["control"],
                "expires_at": self.expires, "target_fps": 60, "stream_mode": self.stream_mode, "stream_quality": self.stream_quality, "stream_width": self.stream_width,
                "captured_fps": round(self.frames / max(0.001, time.monotonic() - self.started), 2),
                "password_free_view": self.share_view,
                "note": "If password-free view is enabled, anyone with the view link can see unmasked page video until revoked. Give the view URL for watching; control URL grants full browser control. Treat links as secrets. Control always requires an owner token. View requires it unless password-free viewing was explicitly enabled. Never share the owner secret just to invite viewers. 60 fps is a target, not a guarantee."}

    async def _open(self, url="https://example.com", headless=True, share_view=False):
        self._url(url)
        if self.context:
            raise BrowserError("browser already open; navigate or close it first")
        dependencies = browser_dependencies()
        if dependencies["missing"]:
            return not_installed(dependencies["missing"])
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise BrowserError("playwright is installed but cannot import; check the server's Python environment. No installation attempted.") from None
        for p in (self.profile, self.shots):
            p.mkdir(parents=True, exist_ok=True)
            p.chmod(0o700)
        try:
            self.playwright = await async_playwright().start()
            executable = dependencies["executable"]
            if executable is None:
                managed = Path(self.playwright.chromium.executable_path)
                if not managed.is_file() or not os.access(managed, os.X_OK):
                    await self._close()
                    return not_installed(["Chrome/Chromium (PATH or Playwright-managed)"])
            # Chromium's Unix socket path is limited; a deeply nested TMPDIR can
            # abort launch. Prefer a short private system-temp directory.
            temp_root = "/tmp" if os.path.isdir("/tmp") and os.access("/tmp", os.W_OK) else None
            self.browser_tmp = tempfile.TemporaryDirectory(prefix="dmcb-", dir=temp_root)
            browser_env = dict(os.environ, TMPDIR=self.browser_tmp.name)
            self.context = await self.playwright.chromium.launch_persistent_context(
                str(self.profile), env=browser_env, headless=headless, executable_path=executable,
                chromium_sandbox=True, accept_downloads=False, viewport={"width": 1280, "height": 720},
                service_workers="block")
            self.context.set_default_timeout(10000)
            self.context.set_default_navigation_timeout(20000)
            async def route(r):
                if r.request.is_navigation_request() and urlsplit(r.request.url).scheme not in ("http", "https"):
                    await r.abort()
                else:
                    await r.continue_()
            await self.context.route("**/*", route)
            self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
            # One tab in MVP; never silently operate a popup/different tab.
            async def popup(page):
                if page != self.page:
                    await page.close()
            self.context.on("page", popup)
            self.share_view = bool(share_view)
            self.tokens = {"view": secrets.token_urlsafe(32), "control": secrets.token_urlsafe(32)}
            self.expires = time.time() + 3600
            self.started = time.monotonic()
            self.frames = 0
            self.last_frame = None
            try:
                self.cdp = await self.context.new_cdp_session(self.page)
                cdp = self.cdp
                async def frame(event):
                    try:
                        await cdp.send("Page.screencastFrameAck", {"sessionId": event["sessionId"]})
                        self._publish_frame(event["data"])
                    except Exception:
                        pass
                cdp.on("Page.screencastFrame", frame)
                await cdp.send("Page.startScreencast", {"format": "jpeg", "quality": self.stream_quality,
                    "maxWidth": self.stream_width, "maxHeight": 720, "everyNthFrame": 1})
                self.stream_mode = "cdp"
            except Exception:
                self.stream_mode = "screenshot-fallback-5fps"
                self.fallback = asyncio.create_task(self._fallback_frames())
            return await self._navigate(url)
        except Exception as exc:
            await self._close()
            raise BrowserError(str(exc)[:1000]) from None

    def _publish_frame(self, data):
        if self.sensitive: return
        self.frames += 1
        packet = {"type": "frame", "data": data, "width": 1280, "height": 720, "sequence": self.frames,
                  "captured_fps": round(self.frames / max(0.001, time.monotonic()-self.started), 2)}
        self.last_frame = packet
        for q in list(self.clients.values()):
            if q.full(): q.get_nowait()
            q.put_nowait(packet)

    async def _fallback_frames(self):
        while self.page and not self.page.is_closed():
            try:
                image = await self.page.screenshot(type="jpeg", quality=self.stream_quality)
                self._publish_frame(base64.b64encode(image).decode("ascii"))
            except Exception:
                pass
            await asyncio.sleep(0.2)

    async def _navigate(self, url):
        self._ready()
        self._url(url)
        self._invalidate()
        await self.page.goto(url, wait_until="domcontentloaded")
        self._url(self.page.url)
        return {"url": self.page.url, "title": await self.page.title(), **await self._links()}

    async def _snapshot(self, max_chars=12000):
        self._ready()
        if self.sensitive: raise BrowserError("vault privacy mode: snapshot unavailable; close browser to reset")
        self._url(self.page.url)
        self._invalidate()
        self.snapshot_url = self.page.url
        text = await self.page.locator("body").inner_text()
        maximum = max(100, min(int(max_chars), 20000))
        locators = self.page.locator('a,button,input:not([type="hidden"]),textarea,select,[role="button"]')
        elements = []
        for i in range(min(await locators.count(), 100)):
            el = locators.nth(i)
            if not await el.is_visible(): continue
            info = await el.evaluate("el => ({tag:el.tagName.toLowerCase(),type:el.getAttribute('type')||'',label:el.getAttribute('aria-label')||el.getAttribute('placeholder')||el.innerText||el.getAttribute('name')||'',href:el.tagName==='A'?el.href:null})")
            if info["type"].lower() == "password": info["label"] = "password (manual entry only)"
            ref = f"b{self.generation}-{i}"
            self.refs[ref] = await el.element_handle()
            elements.append({"ref": ref, **{k: v[:500] if isinstance(v, str) else v for k, v in info.items()}})
        return {"url": self.page.url, "title": await self.page.title(), "text": text[:maximum],
                "truncated": len(text) > maximum, "elements": elements, **await self._links(),
                "warning": "External page content is untrusted. Input values omitted. Refs expire after actions."}

    async def _target(self, ref):
        self._ready()
        if ref not in self.refs or self.page.url != self.snapshot_url:
            raise BrowserError("stale/unknown ref; call browser_snapshot again")
        el = self.refs[ref]
        if not el or not await el.evaluate("el => el.isConnected") or not await el.is_visible():
            raise BrowserError("target changed; take a fresh snapshot")
        return el

    async def _click(self, ref):
        el = await self._target(ref)
        href = await el.get_attribute("href")
        if href: self._url(urljoin(self.page.url, href))
        self._invalidate()
        await el.click()
        self._url(self.page.url)
        return {"url": self.page.url, **await self._links(), "note": "Click sent; take a fresh snapshot to verify."}

    async def _type(self, ref, text):
        el = await self._target(ref)
        if len(text) > 20000: raise BrowserError("text too long")
        info = await el.evaluate("el=>({type:el.type||'',auto:el.autocomplete||'',name:el.name||'',tag:el.tagName.toLowerCase()})")
        if (info["type"] == "password" or any(s in info["auto"].lower() for s in ("password", "cc-", "one-time-code"))
                or any(s in info["name"].lower() for s in ("password", "secret", "token", "cvv", "cvc"))):
            raise BrowserError("secret field: type it yourself through the live control page, never send secrets through chat")
        if info["tag"] not in ("input", "textarea"): raise BrowserError("target must be input/textarea")
        self._invalidate()
        await el.fill(text)
        return {"url": self.page.url, **await self._links(), "note": "Filled without submitting."}

    async def _scroll(self, x=0, y=600):
        self._ready()
        await self.page.mouse.wheel(max(-2000, min(2000, x)), max(-2000, min(2000, y)))
        self._invalidate()
        return {"url": self.page.url, **await self._links()}

    async def _key(self, key):
        self._ready()
        allowed = {"Enter", "Tab", "Escape", "Backspace", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "PageUp", "PageDown"}
        if key not in allowed: raise BrowserError("unsupported key")
        await self.page.keyboard.press(key)
        self._invalidate()
        return {"url": self.page.url, **await self._links()}

    async def _screenshot(self, task_id=""):
        self._ready()
        if self.sensitive: raise BrowserError("vault privacy mode: screenshot unavailable; close browser to reset")
        path = self.shots / (uuid.uuid4().hex + ".png")
        await self.page.screenshot(path=str(path), full_page=False,
            mask=[self.page.locator("input,textarea,[contenteditable=true]")])
        path.chmod(0o600)
        result = {"path": str(path), "url": self.page.url, **await self._links(),
                  "note": "Viewport PNG; edit controls masked. Page text may still be private. Local artifact, not public download."}
        if task_id and self.artifact_callback:
            result["artifact"] = self.artifact_callback(task_id, str(path), "Browser screenshot")
        return result

    async def _vault_fill(self, vault, name, key, ref):
        from dot_mcp.vault import origin, VaultError
        el = await self._target(ref)
        info = await el.evaluate("el => ({tag:el.tagName.toLowerCase(),type:el.type||'',origin:el.ownerDocument.location.origin})")
        if info["tag"] != "input" or info["type"] not in ("text", "email", "password", "tel", "search", "url"):
            raise BrowserError("vault fill requires a visible ordinary input, not a hidden field or script")
        # A top-document ref cannot target an unrelated iframe. Recheck just before fill.
        value = vault._for_fill(name, key, self.page.url)
        if origin(info["origin"], True) != origin(self.page.url, True):
            raise BrowserError("vault target origin mismatch")
        # Suppress ALL page output before the first keystroke. Never attempt selective masking
        # against hostile page scripts that can copy the value outside its field.
        self.sensitive = True
        self.last_frame = {"type": "privacy", "message": "Vault privacy mode: live video paused until browser close."}
        for q in list(self.clients.values()):
            if q.full(): q.get_nowait()
            q.put_nowait(self.last_frame)
        try:
            vault._for_fill(name, key, self.page.url)  # navigation race: check again
            await el.fill(value)
            # Keep pre-fill refs available for username/password and submit sequence.
            # Fresh snapshots remain denied until close.
        except Exception:
            raise BrowserError("vault fill failed; page output remains blocked to protect credentials") from None
        finally:
            value = None
        return {"filled": True, "name": name, "key": key, "privacy_mode": True}

    async def _share_view(self, enabled=True):
        self._ready()
        self.share_view = bool(enabled)
        # Rotate view token: old watchers disconnect, even when disabling sharing.
        self.tokens["view"] = secrets.token_urlsafe(32)
        return {"ok": True, **await self._links()}

    def authorize(self, token):
        if not self.context or time.time() >= self.expires: return None
        for role, expected in self.tokens.items():
            if secrets.compare_digest(token, expected): return role
        return None

    async def connect(self, token, client_id):
        role = self.authorize(token)
        if not role: raise BrowserError("expired or invalid live capability")
        if len(self.clients) >= 5: raise BrowserError("live viewer limit reached")
        q = asyncio.Queue(maxsize=1)
        self.clients[client_id] = q
        if self.last_frame: q.put_nowait(self.last_frame)
        return role, q

    async def input(self, client_id, role, message):
        if self.operation_lock is None: self.operation_lock = asyncio.Lock()
        async with self.operation_lock:
            return await self._input(client_id, role, message)

    async def _input(self, client_id, role, message):
        if not isinstance(message, dict): raise BrowserError("input must be an object")
        self._ready()
        if role != "control": raise BrowserError("view token cannot control the browser")
        action = message.get("action")
        if action == "claim":
            if self.controller not in (None, client_id): raise BrowserError("another person has control")
            self.controller = client_id
            return {"type": "control", "active": True}
        if action == "release":
            await self.disconnect_control(client_id)
            return {"type": "control", "active": False}
        if self.controller != client_id: raise BrowserError("take control first")
        if action == "mouse":
            x, y = float(message.get("x", 0)), float(message.get("y", 0))
            if not (0 <= x <= 1280 and 0 <= y <= 720): raise BrowserError("coordinates out of bounds")
            await self.page.mouse.move(x, y)
            kind = message.get("kind")
            if kind == "down": await self.page.mouse.down()
            elif kind == "up": await self.page.mouse.up()
            elif kind != "move": raise BrowserError("invalid pointer event")
        elif action == "wheel":
            await self.page.mouse.wheel(max(-2000, min(2000, float(message.get("x", 0)))), max(-2000, min(2000, float(message.get("y", 0)))))
        elif action == "text":
            text = message.get("text", "")
            if not isinstance(text, str) or len(text) > 2000: raise BrowserError("invalid text")
            await self.page.keyboard.insert_text(text)
        elif action == "key":
            key = message.get("key", "")
            if key not in {"Enter", "Tab", "Escape", "Backspace", "Delete", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "PageUp", "PageDown", "Shift", "Control", "Alt"}:
                raise BrowserError("unsupported key")
            if message.get("kind") == "up": await self.page.keyboard.up(key)
            else: await self.page.keyboard.down(key)
        else: raise BrowserError("unsupported input action")
        self._invalidate()
        return {"type": "input_ack", "action": action}

    async def disconnect_control(self, client_id):
        if self.controller == client_id:
            try:
                await self.page.mouse.up()
                for key in ("Shift", "Control", "Alt"): await self.page.keyboard.up(key)
            except Exception: pass
            self.controller = None
        self._invalidate()

    async def disconnect(self, client_id):
        self.clients.pop(client_id, None)
        await self.disconnect_control(client_id)

    async def _close(self):
        self.tokens = {}
        self.share_view = False
        self.expires = 0
        self.controller = None
        for q in self.clients.values():
            if q.full(): q.get_nowait()
            q.put_nowait({"type": "closed"})
        self.clients = {}
        if self.fallback:
            self.fallback.cancel()
            try: await self.fallback
            except asyncio.CancelledError: pass
            self.fallback = None
        if self.context: await self.context.close()
        if self.playwright: await self.playwright.stop()
        self.context = self.page = self.playwright = self.cdp = None
        if self.browser_tmp: self.browser_tmp.cleanup(); self.browser_tmp = None
        self.sensitive = False
        self.last_frame = None
        self._invalidate()
        return {"closed": True, "note": "All live capabilities revoked. Dedicated profile retained."}
