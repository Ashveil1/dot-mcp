# dot-mcp

Personal MCP agent for ChatGPT, Claude, and Gemini: shell, files, memory,
goals, tasks, inbox, scheduler, and self-hosted OAuth — running on your
own machine.

> ⚠️ The server gives full shell access to whoever holds a credential.
> Expose it only while you use it, then stop it (`dot-mcp stop`).

## Install

```bash
pip install dot-mcp
```

Requires Python 3.10+ and OpenSSH (`ssh`, `ssh-keygen`) for the tunnel.

## Use

```bash
dot-mcp start    # server + public URL (runs in background)
dot-mcp status   # check if it is running
dot-mcp stop     # stop server + tunnel
```

`dot-mcp start` prints everything the connector needs:

```
Public URL            https://xxxx.tunnl.gg/mcp/
Owner secret          <stored on your machine>
OAuth Client ID       dots-xxxxxxxx
OAuth Client secret   <stored on your machine>
```

Add the **Public URL** as a custom MCP connector (auth = OAuth).
Approve once with the owner secret when asked. The tunnel URL stays the
same on restart; the free tunnel expires after 24h or 2h idle.

Linux/Termux is the supported runtime for process management. Stop only affects
processes started with the new ownership records. If upgrading from an older
version, stop its server/tunnel before starting this version; bare legacy PID
files are deliberately not trusted.

`DOT_MCP_STATE_DIR` overrides the complete credential/SSH-key directory
(default `~/.config/dot-mcp`), not its parent. This is separate from `--data-dir`.

File paths are relative to `$HOME` unless absolute. Absolute paths retain their
location and must fall within `$HOME`, `$TMPDIR`, or available shared storage;
other paths are rejected, not silently placed under HOME. On Android, when
`$TMPDIR` is outside `/tmp`, `/tmp` and `/tmp/...` map to `$TMPDIR`. File tools
return the effective `path`. Shell command text is never remapped, so use that
effective path in shell commands when this mapping applies.

File roots are guardrails, not a shell sandbox. A shell credential still grants
full access as the server's OS user. Use a separate OS user/container for
untrusted workloads.

Options: `--port` (default 33041), `--data-dir` (default `~/.dot-mcp`),
`--json` (script-friendly output).

### Development tunnel (testing only)

`dots start --dev` uses a Cloudflare quick tunnel instead of the default
stable-key tunnl.gg flow. It needs `cloudflared` on PATH, not OpenSSH. Missing
`cloudflared` is reported as `not installed: cloudflared`; nothing is installed.

DEV / UNSTABLE: quick-tunnel hostnames change on every restart and have no
uptime guarantee. Error 1033/outages can occur. Cloudflare quick tunnels do not
support SSE, so some MCP client/transport combinations can fail even if startup
initialize passes. Do not use this mode for production connectors.

Host/Origin protection stays enabled. The discovered trycloudflare.com host is
passed to the server before its single boot, with authenticated public initialize
verification. A failed check stops the owned server and tunnel. `dots stop`
stops either provider; `--json` includes mode, provider and unstable fields.
An explicit empty Cloudflare config prevents reusing a saved named tunnel.

This is a provider switch, NOT a security sandbox: use a separate container/OS
user with throwaway credentials. Dev mode does not change default data or
credential paths and can still use an existing local vault opt-in. In an isolated
sandbox, use separate `DOT_MCP_DATA` and `DOT_MCP_STATE_DIR` paths and a disposable
HOME; do not mount personal files. Optional browser/vault packages are not needed
for a basic MCP/OAuth smoke test. `--dev --vault` uses the same dev provider but
requires the normal local vault unlock.

Cloudflare docs: https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/

## What it can do (51 tools in this development revision)

- **Shell:** `run_command` (background sessions + timeout) + `read_output`
- **Files:** `list_dir`, `read_file` (paged + sha256), `write_file`
  (atomic + guards), `append_file`, `edit_file`
- **Memory:** `remember`, `recall`, `forget` — persists across chats
- **Goals/Tasks:** `set_goal`, `add_update`, `get_state`, `add_task`,
  `complete_task`
- **Inbox:** `notify_user`, `poll_inbox`, `ack_inbox` — messages across chats
- **Scheduler:** `add_schedule`, `list_schedules`, `remove_schedule` —
  background jobs that run between chats
- **Browser:** `browser_open/navigate/snapshot/click/type/scroll/key/screenshot/close`
  + `browser_hover/select/wait/back/reload` — snapshots report disabled,
  checked and selected-option state; `wait` handles dynamic pages
- **Live:** `/live` auto-reconnects (sessionStorage capability), shows the
  current domain, fullscreen view; control still requires the owner token
- **UI:** `open_workspace` — embedded dashboard (tasks, memory, sessions)

## Durable work (development revision)

Existing tool names/signatures remain available. New work tools:

- `create_work_task`, `get_work_task`, `list_work_tasks`, `update_work_task`
- `start_command`, `get_session_status`, `cancel_command`
- `register_artifact`, `bootstrap_context`, `ack_work_result`, `purge_session_output`

Suggested flow for ChatGPT/Claude:

1. Call `bootstrap_context` at the start of a new chat.
2. Create a work task, then `start_command` with its `task_id`.
3. Poll `read_output` / `get_session_status`. Output is readable while running.
4. Verify the result, register output files, and set the task to `done` with a
   result, or `blocked` with a question. Exit zero alone never finishes a task.
5. In a new chat, load context and inspect `get_work_task` to resume.

`start_command` returns immediately. A reused `idempotency_key` returns the
same session for an identical request; a changed request with that key is
rejected. This prevents duplicate launches, not exactly-once external effects.
`set_goal` changes the current goal without deleting `add_update` history.

The original `run_command` remains the legacy synchronous API. Use the new
start/status tools for durable asynchronous work; `read_output` and
`get_session_status` handle both. Legacy status is in-memory only and does not
survive restart. `purge_session_output` clears finished legacy output while
keeping its metadata; active output cannot be purged. Durable output still
requires `ack_work_result` before purge.

Default command limits: 4 active sessions, 1 hour maximum runtime, 4 MiB combined
output per session and 64 MiB saved output before new work is refused. Output
is not silently deleted: acknowledge a finished result and explicitly purge
its output. Task/events/session metadata stay in `work.sqlite3` (private file).

On first use, legacy tasks are copied from `state.json` once, with a private
backup. Old JSON tools still use their old files, and `bootstrap_context` shows
current legacy tasks/inbox separately. Memory/goals/schedules/OAuth are not
migrated or rewritten. Use new work task tools consistently; the legacy task
list and durable work list are separate after the initial import.

One server owns execution per data directory. After a server crash/restart,
unverified running commands are stopped if their saved process identity still
matches, and marked `interrupted`. They are never restarted automatically.
This is recovery of truthful records, not transparent survival of processes.

Artifacts are local file paths plus SHA-256, **not** hosted download links.
This revision has no browser, push notifications, or autonomous LLM worker,
and needs no new model API key for the active-chat workflow. A host may need
to refresh its tools after an update. Live ChatGPT/Claude integration remains
to be tested by the owner.

## Security model

- `/mcp/` requires `Authorization: Bearer` (OAuth JWT or owner secret) —
  anything else gets 401, even from localhost
- OAuth 2.1 is self-hosted: dynamic registration, PKCE, 1h JWT +
  rotating 30d refresh tokens, manual Client ID/secret for Claude-style
  connectors
- Per-IP rate limits (300/min API, 30/min auth), append-only `audit.jsonl`,
  cross-process file locks, atomic writes
- Known limits: the tunnel provider sees traffic (TLS ends at their edge);
  back up `~/.dot-mcp` yourself

## Local-only mode (no tunnel)

```bash
export OPENAI_API_KEY=sk-...
python -m dot_mcp.local_agent          # chat loop
python -m dot_mcp.local_agent --list   # list tools only
```

## Develop

```bash
./setup.sh                      # venv + editable install
.venv/bin/python -m pytest tests/ -q   # test suite
python -m build                 # wheel in dist/
```

See `CHANGELOG.md` for release notes.

## License

MIT

## Optional browser and /live

Install in the same environment as dot-mcp (no shell activation needed):

```sh
.venv/bin/python -m pip install '.[browser]'
.venv/bin/python -m playwright install chromium
```

On Arch, an installed Chrome/Chromium can be selected with
`DOT_MCP_BROWSER_EXECUTABLE=/usr/bin/chromium`. The Chromium sandbox stays
on. Do not point this at your everyday profile. This feature always uses
`$DOT_MCP_DATA/browser/profile` (the app's default data directory when unset).
Cookies remain there after close; deleting this dedicated profile signs it out.
Playwright and WebSocket dependencies are optional and loaded only for this
feature. Existing shell and file tools do not require a browser install.

Tools: `browser_open`, `browser_navigate`, `browser_snapshot`, `browser_click`,
`browser_type`, `browser_scroll`, `browser_key`, `browser_screenshot`, and
`browser_close`. Open defaults to headless Chromium; set `headless=false` to
show a window on a desktop with a display. Snapshots return rendered text and
short-lived element refs. Take a new snapshot after each action. Pages are
untrusted content, not instructions. Input values are omitted; recognized
secret fields reject AI typing. Enter secrets directly, never through AI chat.
One tab is supported; popups are closed, downloads disabled.

Every browser result (except close) includes `live_view_url` and
`live_control_url`. Give the view link to watch, and the control link only to
an intended controller. Both carry separate unguessable session capabilities
in URL fragments, not query strings or server access logs. They expire after
one hour or immediately on `browser_close`.

**Every new /live visit also requires an owner token.** The page asks for the
existing owner secret or a valid owner OAuth access token before streaming or
control. Enter it on the page itself, not in chat or a URL. It is sent over the
same-origin WebSocket, not stored in browser storage, and the authenticated
connection lasts at most 15 minutes. Reloading asks again. A leaked live link
alone is insufficient. This uses the existing owner-token validation, not a
new OAuth redirect flow. Do not distribute your long-lived owner secret to
invite viewers: this version does not issue guest credentials. A valid owner
OAuth token also authorizes owner MCP access, so it is not a view-only token.
Use HTTPS through the existing tunnel for remote access; plain HTTP is for
loopback testing only. WebSocket Origin must match the configured public
issuer exactly. MCP's owner-secret/OAuth checks are unchanged.

The /live page has Take control / Release buttons, click and hold, pointer
drag, direct typing, keyboard down/up, and wheel scrolling. Only one human
controls at a time. While control is claimed, AI changes are rejected until
release/disconnect; read-only snapshots and screenshots remain available.
Up to five viewers can join. Latest-frame queues drop old frames rather than
building latency. Live video is **not masked** and may show private page
content. Screenshot PNGs mask editable controls, but ordinary page text can
still be private. `browser_screenshot(task_id=...)` registers the local PNG
with existing work artifacts; it is not a public download link.

Streaming targets 60 fps using Chromium CDP screencast JPEGs. The page shows
received and captured rates. Static pages naturally emit fewer frames;
network, CPU, image decode and tunnel speed can reduce the rate. CDP setup
failure falls back to screenshot sampling up to 5 fps, reported in
`stream_mode`. No 60 fps guarantee. This is browser automation on your local
machine, not a network sandbox: HTTP/HTTPS sites may reach localhost or LAN
services. The separate profile does not make the existing full shell safe.

Local integration tests (optional, need installed Chromium):

```sh
TEST_CHROME=/usr/bin/chromium .venv/bin/python -m pytest tests/test_browser.py -q -s
.venv/bin/python -m pytest tests -q
```

The browser tests use only a loopback fixture, including authenticated /live
control, hold, text, scrolling, stale refs, capability expiry and fallback.

## Optional local fill-only vault (experimental)

Install `.[browser,vault]`. The vault uses AES-256-GCM authenticated encryption
with a random 16-byte salt and Scrypt-derived key (N=32768, r=8, p=1) from a
human passphrase. It is locked by default. Names, origins and values are all
inside the encrypted file; the encryption format/version is public. Files are
private (0600), with atomic replacement and a cooperating process lock. Keep
an offline backup: there is no passphrase recovery. Choose a long, unique
passphrase; 12 characters is a minimum, not a strength guarantee.

Human-only local terminal setup (no secrets in command arguments, chat or env):

```sh
.venv/bin/python -m dot_mcp.cli vault init
.venv/bin/python -m dot_mcp.cli vault set --name example-login --origin https://example.com --field username --field password
.venv/bin/python -m dot_mcp.cli vault list
```

Each command prompts with `getpass` and does not echo values. Names are not
secret by design. `--origin` is normalized to exact scheme, host and port;
paths do not limit credentials within an origin. No subdomain wildcards or
suffix matching. HTTPS is required. `--allow-loopback-http` explicitly allows
localhost/127.0.0.1/::1 HTTP development fixtures only, never LAN HTTP.

To run with an unlocked vault, stop any existing server holding the same data
directory, configure the existing MCP owner authentication/tunnel normally,
then start in the foreground:

```sh
.venv/bin/python -m dot_mcp.cli vault serve
```

This prompts for the passphrase locally and starts the HTTP server in that
same process. It does **not** create a tunnel or print owner/client secrets.
Do not pass a passphrase through environment variables or arguments. A normal
`dots start` starts locked and cannot unlock the vault via MCP. There is no
remote unlock/edit API or page. After 15 idle minutes, the next vault access
auto-locks; restart the foreground command to unlock again. `vault_lock`
locks it immediately. Python cannot promise forensic memory erasure of keys.

MCP tools added: `vault_list` returns names only; `vault_fill(name,key,ref)`
fills a saved username/password/token into a fresh visible input ref, with
exact allowed-origin verification and no submit; `vault_lock` drops unlocked
state. A fill into another origin is refused. Take one browser snapshot before
filling and use those refs for the username/password sequence and submit. The
pre-fill refs remain valid for consecutive fills, subject to DOM identity and
URL checks. Subsequent ordinary browser actions invalidate refs as before.

Before filling, the browser enters sticky privacy mode: it clears queued live
frames, pauses video and blocks all snapshots/screenshots/page URL/title
outputs until `browser_close`. This includes the screenshot fallback.
`browser_key('Enter')` can submit, but review the action first. A page can
reflect a password anywhere, so selective input masking is not a safe promise.
This conservative mode means the AI cannot visually verify a login after fill;
the human checks in the dedicated browser window (`headless=false`) and closes
it when done. No API restores video on an already-filled page.

### Threat model and limits

This is a fill-only **tool-output interface**, not a secret isolation boundary.
The browser's page JavaScript necessarily sees values filled into ordinary
HTML inputs (even type=password), and the authorized site receives them.
Origin binding prevents filling on a different origin; it does not protect
against a compromised authorized site, XSS, malicious extensions or phishing
on the approved origin. HTTP redirects are checked against the current page
origin before fill; no general page content can grant permission to use a key.

**Do not enable full shell access for an AI you do not trust.** The existing
`sh`/command tools run as the same OS user. They can inspect files/processes,
read key material from memory, automate a second browser or instrument code.
Encrypted-at-rest storage cannot stop this. Filesystem-root guards do not
sandbox shell. A real boundary needs a separate OS principal for the vault,
restricted IPC and a browser/input policy outside the AI's control, plus no
same-user unrestricted shell. That hardened architecture is not in this patch.
Likewise screenshots saved before vault use, terminal scrollback, page caches,
profile cookies and remote service logs have their own lifetimes. Secrets must
not be embedded in entry names or sent into chat. No payment-card or TOTP flow
is implemented yet. Use only throwaway credentials while evaluating this
experimental feature.

### Startup credentials and consent UI

Startup now keeps owner/client secrets out of normal terminal output and
`dots start --json`. The JSON result includes `credentials_hidden=true`
instead of `owner_secret` / `client_secret`. This is a deliberate breaking
change for scripts that read secrets from startup JSON. Client ID and public
URL remain available. Use `dots credentials` for file locations or
`dots credentials --show` in a private interactive terminal only: it requires
an explicit REVEAL confirmation and refuses redirected output. Terminal
recording/scrollback still captures an intentional reveal, so do not record
it. Do not paste secrets into AI chat. The OAuth page describes owner-level
shell/file access before approval, uses no remote assets, and sends no-cache,
no-referrer and anti-framing headers. OAuth parameters and verification rules
are unchanged.

### Live stream bandwidth and backpressure

The stream now defaults to JPEG quality 55 and max capture width 960 (the
controlled browser viewport remains 1280x720). Configure
`DOT_MCP_STREAM_QUALITY=30..85` and `DOT_MCP_STREAM_WIDTH=640..1280` before
starting for sharper text or lower bandwidth. Control coordinates still refer
to the 1280x720 viewport. This intentionally trades some image sharpness for
bandwidth; screenshots remain full viewport PNGs. It is configurable, not an
automatic bandwidth estimator.

The /live client acknowledges decoded frames. A bounded four-frame window plus
latest-frame queue avoids unbounded backlog while tolerating network roundtrip
time. A client that does not acknowledge frames is disconnected after five
seconds. The counters now distinguish captured, received and displayed frames;
'displayed' means image decoded and drawn to the canvas, not measured monitor
refresh. Static pages emit few frames naturally. No sustained 60-fps guarantee.

### Diagnostics and reconnects

`dots doctor` (or `dots doctor --json`) checks local package versions,
interpreter/source choice, optional dependency availability and the live
loopback `/health` tool count without launching a browser or revealing
credentials. It distinguishes the diagnostic Python from the selected server
Python; package versions are for the diagnostic interpreter. Installed
Chromium availability is a lead, not a successful launch test.

After applying patches, restart the running Python server. Editing files does
not reload the old process. Round 3 alone registers 42 tools; Round 4 vault adds
3 for 45; the browser-control round adds hover/select/wait/back/reload for 51.
Check `dots status` or live `/health`, then reconnect/refresh the
assistant's connector tool list. Importing server code in a separate shell
only checks files, not the server already running. Never work around missing
browser_* tools with a separate Python process: its browser/tokens are not
owned by the server /live handler. There is no `/shot/...` public screenshot
route in these patches; screenshot artifacts are private local files.

`vault serve` reuses the CLI's private local owner-secret file when
`MCP_PATH_SECRET` is unset, so it does not create an unreachable random owner
credential. It still prints no secret. Existing explicit owner env takes
precedence. The process receives that owner credential like normal `dots
start`; this is not a vault passphrase env variable.

Chromium uses a short private temporary directory for its process sockets when
system `/tmp` is writable, even if your configured app TMPDIR is deeply nested.
It is cleaned on browser close. Browser profile and screenshots still live in
the dedicated data directory. This prevents Linux Unix-socket path overflow;
it does not weaken the Chromium sandbox.

The OAuth consent CSP permits the validated callback HTTPS origin as well as
`self` for form submission. Chromium applies `form-action` to the redirect
following a POST; restricting it to `self` alone blocked successful OAuth
callbacks. This allowance is generated from the already-validated redirect,
not a wildcard, and does not replace owner-secret/redirect/PKCE verification.

## Stable public URLs and a tunnel-only helper

A running Python server does not learn a new public hostname just because a
tunnel was opened. Set `MCP_PUBLIC_HOST` to the canonical HTTPS hostname before
server boot. OAuth issuer/resource URLs, DNS-rebinding allowed hosts and /live
WebSocket Origin checks all use that configuration. Restart/reconnect is needed
when the hostname changes. Caller-controlled Host/X-Forwarded-Host headers do
not choose the issuer or authorize a new origin. Never disable these checks
just to make a proxy work. Consent Origin/Referer checks compare exact scheme,
host and port; proxy warning cookies do not authenticate the owner.

### tunnl.gg stable (no account)

`dots tunnel` runs a foreground stable-key SSH tunnel to an **already-running**
healthy loopback server. It does not start/restart the server or rewrite its
OAuth config. Stdout contains only the non-secret base URL and `/mcp/` URL;
provider banners are saved in a private tunnel log. Ctrl+C stops this helper's
SSH child, not the server. It does not reconnect endlessly or bypass provider
limits. OpenSSH is required. By default it creates a dedicated private key in
`~/.dot-mcp/tunnel/tunnl-key`; keep that same key to retain the stable URL.

Fish-friendly helper and equivalent manual command:

```fish
.venv/bin/python -m dot_mcp.cli tunnel --port 33041
# Existing dedicated SSH key:
.venv/bin/python -m dot_mcp.cli tunnel --port 33041 -i ~/.ssh/dot-mcp-tunnel
# Equivalent manual command (keep this terminal open):
ssh -t -i ~/.ssh/dot-mcp-tunnel -o IdentitiesOnly=yes -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes -R 80:localhost:33041 stable@proxy.tunnl.gg
```

First-time setup is two-phase because the provider chooses the hostname:
start a local server, run the helper to learn its stable URL, stop the server
using its normal lifecycle command, set the returned **hostname only**, then
boot the server with that setting. The tunnel may stay open while restarting
only the local server. Reusing the SSH key keeps the provider URL; it does not
make a stale running issuer update itself. A direct server boot example after
you have provisioned owner credentials using normal CLI setup:

```fish
set -gx MCP_PUBLIC_HOST your-returned-host.tunnl.gg
set -gx MCP_PORT 33041
# Human vault serve reuses the provisioned owner credential, starts foreground,
# and prompts locally for vault unlock (no secret in arguments):
.venv/bin/python -m dot_mcp.cli vault serve
```

If you do not want the vault unlocked, use normal `dots start` for the packaged
server+tunnel flow; the tunnel-only helper is for an independently managed
running server, not a replacement lifecycle manager. Do not paste owner/client
secrets into ChatGPT or include them in URL arguments. `dots credentials` gives
private credential-file locations; explicit reveal requires a private TTY.

The provider's free browser warning page appears once per day per site. It
redirects browser GETs before our app; use its normal Continue to site button,
then continue OAuth. Non-browser clients may skip it according to provider
policy. dot-mcp does not bypass or treat that warning/cookie as authentication.
For /live, pass the warning first at the public base URL, then reopen the
original live link containing its `#token=` fragment. A server cannot receive
URL fragments, so do not assume an interstitial redirect preserves a live
capability. /live still requires owner-token entry and exact configured Origin.
A proxy must support WebSocket upgrades. Our unit tests model proxy headers,
provider-cookie presence and auth checks; they do not prove any provider's
future warning implementation or uptime.

The provider currently documents 2 hours inactivity/24 hours maximum lifetime,
3 tunnels per IP, WebSocket limits and request rate limits. Reconnect with the
same private key to keep its stable name, within those limits. Changing keys
changes URLs. TLS ends at the provider; it sees transit traffic. See current
[tunnl.gg docs](https://tunnl.gg) and
[stable URL details](https://tunnl.gg/stable-urls.md).

### Tailscale Funnel (account/tailnet required)

With an installed, signed-in Tailscale client and Funnel enabled for your
tailnet, expose the loopback port:

```fish
tailscale funnel 33041
```

Use the printed HTTPS hostname as `MCP_PUBLIC_HOST` before booting dot-mcp,
and its `/mcp/` URL for the connector. Funnel has HTTPS/MagicDNS/tailnet-policy
requirements and bandwidth limits; it is not a no-account route. Enabling it
may change tailnet policy, so the account owner should do setup. Do not expose
a file directory in place of the authenticated MCP service. Official guide:
https://tailscale.com/kb/1223/funnel .

### Cloudflare named tunnel (account and domain required)

Use a dashboard-managed named tunnel and published route pointing to
`http://127.0.0.1:33041`, with your chosen fixed public hostname. Follow the
current dashboard's install/run steps or install it as a managed service. The
tunnel token is a credential: don't put it in chat or checked-in scripts.
Configure `MCP_PUBLIC_HOST` to the fixed hostname before server boot. Keep
owner-secret/OAuth checks enabled even when adding proxy access controls, and
ensure any Cloudflare Access policy is compatible with your connector.

Fish-friendly run for an already-configured locally managed named tunnel
whose credentials/config are kept private:

```fish
cloudflared tunnel run dot-mcp
```

Named and dashboard-managed setup differ; don't substitute a Quick Tunnel URL
for your configured named route. Official setup:
https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/get-started/create-remote-tunnel/ .

Quick/free tunnels are suitable for short experiments, not reliable connector
endpoints. In testing, Cloudflare Quick Tunnel intermittently returned Error
1033 while the process was alive; creating a new random URL forced a connector
change. Health working for a few minutes did not guarantee the next tool call.
Official Quick Tunnel docs promise no uptime SLA and note SSE limitations:
https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/ .

### Installed Chrome and convenient local vault startup

`browser_open` now selects your installed Chrome/Chromium from PATH when
`DOT_MCP_BROWSER_EXECUTABLE` is unset, in order: google-chrome,
google-chrome-stable, chromium, chromium-browser. This includes an executable in
`~/.local/bin` if that directory is on PATH. An explicit
`DOT_MCP_BROWSER_EXECUTABLE` still wins. If no installed binary is found,
Playwright uses its managed Chromium. `dots doctor` reports the same selection
logic, but discovery is not a successful launch test. Playwright and WebSocket
packages are still required. The binary is reused, **not** your main browser
profile or already-open tabs; dot-mcp retains its own profile and sandbox.

Vault tools are present by default; encryption still needs an initialized local
vault. `dots start` now opens the stable-key tunnl.gg tunnel first, discovers its
hostname, sets `MCP_PUBLIC_HOST` for the server and boots it once. No manually
exported hostname or second tunnel terminal is required. The dedicated key and
known-hosts file live in the local state directory. Keep that key to retain the
provider's stable identity. Tunnel availability and the warning page still
depend on tunnl.gg; "stable" does not promise uptime.

For a manual local passphrase prompt and foreground lifetime:

```fish
.venv/bin/python -m dot_mcp.cli start --vault
```

This starts its own tunnel. Keep the terminal open; Ctrl+C stops both. In
noninteractive or JSON mode there is no prompt. A prior keyring opt-in may still
unlock the detached server. Without opt-in/keyring, the vault remains locked.

### One-time local vault auto-unlock opt-in (Linux Secret Service)

After human `vault init`/`vault set`, install the optional auto-unlock dependency
only if you choose to use it. On Arch, an unlocked Secret Service provider
(e.g. the keyring used by your desktop) and its D-Bus session must be available
in the server environment. No provider or package is installed by this command.

```fish
.venv/bin/python -m pip install -e '.[vault-auto]'
.venv/bin/python -m dot_mcp.cli vault enable-auto
.venv/bin/python -m dot_mcp.cli start
```

`enable-auto` requires a local terminal, shows a warning, asks you to type
`ENABLE`, then prompts for the passphrase with getpass. It verifies the existing
vault and stores the passphrase only in OS Secret Service. A private opt-in
marker contains no secret. The server retrieves the secret directly on boot,
never via arguments, environment, chat or a parent-to-child key handoff. There
is no plaintext or alternate keyring backend fallback. Missing/locked keyring,
missing opt-in, wrong secret or failed unlock leaves the vault locked. Server
log reports only locked/unlocked status, not the passphrase.

**Warning:** any same-user process with access to the unlocked OS keyring can
retrieve this passphrase and unlock the vault. This is convenience on your own
trusted machine, not a boundary against your shell or other same-user software.
Idle auto-lock and `vault_lock` still lock; they do not automatically reopen on
a later tool call. Restarting the server retries auto-unlock.

```fish
.venv/bin/python -m dot_mcp.cli vault disable-auto
```

Disabling removes the opt-in first and deletes the keyring entry. If the OS
keyring is unavailable/locked, the command reports that credential deletion
failed; unlock it locally and rerun. Auto-unlock remains disabled meanwhile.
Stop/restart any already-unlocked server to drop its in-memory key. Exact-origin
fill and post-fill privacy mode are unchanged. `dots tunnel` remains available
for an already-running server; do not run a second tunnel for `dots start`.

### Missing browser dependencies

`browser_open` checks the running server's Python for Playwright and websockets,
then selects installed PATH Chrome/Chromium or Playwright-managed Chromium.
An explicit executable override is checked for an executable file. With neither
an installed browser nor a managed browser, it reports the browser as missing.

Missing dependencies return a tool result such as:

```json
{"ok": false, "error": "not_installed", "missing": ["playwright", "websockets"], "message": "not installed: playwright, websockets", "next_action": "Ask the user whether to install or configure the missing dependencies. Do not install without user approval."}
```

The tool does not run pip, a package manager or a browser installer. The AI must
ask before installing. These checks are rerun on each new `browser_open` attempt;
after approved installation, restart the server if its environment changed.
`dots doctor --json` includes `optional.browser_missing` for packages and an
explicit/PATH executable; it does not start Playwright to check its managed
browser. Installed-but-broken imports, missing system libraries, display or
sandbox failures are runtime failures, not proof that Chrome is absent. No
sandbox downgrade or automatic dependency fix is attempted. Vault startup is
unchanged.

### Password-free live viewing (explicit session opt-in)

`browser_open(..., share_view=true)` or `browser_share_view(enabled=true)`
enables password-free viewing for that browser session and returns a ready-to-open
`live_view_url`. Default sessions still require owner authentication for viewing.
The long random token is in the URL fragment, not server request URLs. `/live`
alone never exposes video. Tokens expire after one hour and close revokes them;
view connections last at most 15 minutes before reconnecting.

Anyone with an enabled view link can see **unmasked page video**, including
private information. Treat the link as a secret. The AI must get your approval
before enabling or sharing it. `browser_share_view(enabled=false)` rotates the
view token, disconnects old viewers and restores owner-authenticated viewing.
Control is separate: a control link plus your owner secret/OAuth token is always
required. A view token cannot become a control token, even with a password.
Existing exact-Origin checks, limits and post-vault-fill video blocking remain.

The live page tries tokenized view automatically. Private view/control links
show an owner-token form. It never stores that owner token.

### Safe tool errors

Tool failures return `isError` with JSON text containing `ok: false` and an
`error` object: `code`, safe `message`, optional `field`, and optional `allowed`
values. Codes include `not_found`, `bad_argument`, `invalid_state`,
`precondition_failed`, `permission_denied`, `resource_limit` and `tool_failed`.
Validation and anticipated failures behave the same under MCP 1 and 2. Raw
exception text, argument values, OS paths and browser-page text are not returned.
Unknown failures give a generic safe cause instead of guessing a diagnosis.
