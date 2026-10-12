# Changelog

## Unreleased
- Browser control for AI: new `browser_hover`, `browser_select` (dropdown
  label/value with safe option listing), `browser_wait` (text/selector up to
  30s, returns a fresh snapshot), `browser_back`, `browser_reload`
- Snapshots now report `disabled`, `checked`/`selected`, ARIA `role`,
  input `value` and the current `selected_option` for dropdowns
- `/live` page: auto-reconnect with backoff (capability kept in
  sessionStorage, never stored credentials), current-domain display that
  updates on navigation, fullscreen view
- Live `ready` packet reports the current domain; viewers are notified on
  domain change
- Reject empty/oversize inputs consistently: `remember`, `notify_user`,
  `add_update`, legacy `add_task`, `add_schedule` now raise typed
  `bad_argument` errors instead of storing junk records
- `ack_work_result` on an unknown id now returns typed `not_found`
  (`result_id`) instead of silent `ok: false`

## 0.2.3
- Report typed, safe tool error causes consistently under MCP 1 and 2 without echoing raw exceptions or argument values

## 0.2.2
- Preserve add_update history when set_goal changes or repeats the goal
- Make file-tool allowed roots, Android /tmp mapping and effective paths explicit; keep root protections
- Support get_session_status and explicit finished-output purge for legacy run_command sessions
- Reject negative list_dir limits; expose work-task states and edit modes as schema enums
- Publish to PyPI only on a published GitHub release, after the full test matrix is green

## 0.2.1
- Fix browser OAuth consent failing with Origin: null by using a same-origin referrer policy; keep strict origin checks and suppress cross-origin Referer
- Accept bare loopback Host headers and the configured public HTTPS host with or without :443; keep DNS-rebinding protection enabled and reject unlisted hosts
- Synchronize the package __version__ with the release version

## 0.2.0

### Stable one-command startup and vault convenience
- Open the stable-key tunnl.gg tunnel first, discover its hostname and boot the server once with the correct MCP_PUBLIC_HOST (no manual export, no restart dance)
- Add `dots vault enable-auto`/`disable-auto`: one-time local Secret Service opt-in with typed ENABLE confirmation; no keyring means the vault stays locked, no plaintext fallback
- `start --vault` keeps the human getpass prompt and foreground server, now with its own tunnel and cleanup

### Browser dependency reporting and installed Chrome
- Prefer installed PATH Chrome/Chromium (explicit DOT_MCP_BROWSER_EXECUTABLE wins), with the same resolver in doctor; dedicated profile and sandbox unchanged
- browser_open returns a clear `not_installed` result naming missing Playwright/websockets/browser, so the AI asks before installing; nothing is installed silently

### Live view sharing and UI
- Add per-session opt-in password-free view links (`share_view`, `browser_share_view`) with token rotation on toggle/revocation; control always requires the owner secret plus the control capability
- Refresh the responsive live page with automatic view join, owner-auth fallback and clear viewing/control/privacy states

### Fixes
- Restore the omitted tunnl.gg tunnel helper and its mocked regressions
- Make the vault ciphertext tamper test deterministic (guaranteed byte flip)
- Make the JWT signature tamper test deterministic (guaranteed changed first character)

## 0.1.1
- README rewritten in English (install + usage, 22 tools, OAuth, security model)
- `dots start` output slimmed: Public URL / OAuth issuer / IDs / owner secret only
- OAuth consent strips pasted whitespace; SDK log spam silenced

## 0.1.0 — production hardening

**Distribution**
- Restructured to `src/dot_mcp` package (`pip install dot-mcp`)
- Console scripts: `dot-mcp` + `dots` (`start` / `status` / `stop`)
- `python -m dot_mcp.server` works installed and from checkout
- `ui/workspace.html` ships as package data
- Default port **33041** everywhere

**Security**
- `/mcp/` requires `Authorization: Bearer` (OAuth JWT or owner secret); 401 otherwise — no localhost bypass (tunnel traffic looks like loopback)
- Self-hosted OAuth 2.1: DCR (`POST /register`), PKCE-S256 consent (`/authorize`), JWT (1h) + rotating refresh (30d), manual Client ID/secret for Claude-style connectors
- Per-IP rate limits: 300/min on `/mcp*`, 30/min on auth endpoints (429 beyond)
- Append-only `audit.jsonl` (ts/ip/method/path/status/tool/ms)
- `Origin`/`Referer` check on consent POST (CSRF)
- Refresh tokens stored hashed; JWT aud never contains the secret URL

**Reliability**
- Cross-process file locking (`fcntl`) + atomic writes + `mutate_json()` read-modify-write — no lost updates, no torn files
- Scheduler floor (60s), session cap, output truncation + pagination guards
- Empty-ID guards on all prefix-match deletes (cannot wipe whole tables)
- 18-test pytest suite (`tests/test_server.py`)

**Docs**
- README: env table, file layout, loud security + limitations section
- `dots_tui.py` (unused TUI) and `.freebuff/` tracker removed

### Local vault (experimental)
- Add optional encrypted passphrase vault, human getpass CLI and locked-by-default server
- Add names-only vault_list, exact-origin vault_fill and vault_lock tools
- Pause live video and deny page snapshots/screenshots after fill until browser close
- Document same-user full-shell and authorized-site risks; do not claim secret isolation

### UI and credential output
- Redesign OAuth consent page with clear owner-access warning and mobile layout
- Add private-terminal credential reveal; hide startup credentials including JSON
- Improve status panel and /live focus states, controls and accessible labels
- Breaking: start --json no longer emits owner_secret/client_secret values

### Live stream transport
- Lower configurable JPEG quality/capture size while keeping browser viewport stable
- Add bounded client frame ACK window and slow-client timeout
- Show captured/received/displayed frame counters separately; preserve privacy-mode suppression

### Diagnostics and test isolation
- Add read-only doctor command for package/interpreter/live tool-count diagnostics
- Split optional browser/vault tests so passphrase vault tests work without Playwright
- Disable accidental Playwright debug env on the human-unlocked vault serve path
- Reuse the locally provisioned owner credential for vault serve when no explicit owner env is set

### Production vault leak checks
- Exercise unlocked vault through real MCP transport; assert no dummy secret in outputs/server/audit logs
- Fix Chromium Unix socket path overflow with a short private browser temp directory, cleaned on close

### OAuth browser redirect correction
- Permit only the validated callback HTTPS origin in consent form-action CSP
- Fix Chromium blocking an otherwise successful OAuth POST redirect
- Add actual Chromium form-submit/callback regression; retain owner, redirect and PKCE checks

### Stable tunnel helper and proxy checks
- Add dots tunnel for an already-running loopback server, dedicated stable SSH identity and URL-only stdout
- Keep provider output in private logs; terminate helper's SSH child on exit
- Check consent Origin/Referer scheme, host and port without trusting forwarded host headers
- Test proxy headers/warning cookies cannot bypass OAuth or /live authentication
- Document fish-friendly stable tunnel routes and /live fragment/interstitial handling
