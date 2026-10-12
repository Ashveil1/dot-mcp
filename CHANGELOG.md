# Changelog

## Unreleased
- Add optional dedicated Chromium profile with nine browser tools and masked PNG work artifacts
- Add /live watch and human control with separate expiring capabilities plus owner-token authentication on every visit
- Serialize human takeover against AI changes; revoke live capabilities on close
- Stream latest-frame CDP JPEGs with actual FPS counters and a lower-rate screenshot fallback
- Add durable SQLite tasks, checkpoints, command sessions, artifact references and results
- Add async command start/status/cancel with streamed bounded output and idempotency keys
- Add bootstrap_context for new-chat continuity and review-before-purge output retention
- Import legacy task records once with a private backup; preserve original JSON APIs
- Reconcile interrupted commands after process crashes; never automatically replay commands
- Add real two-client MCP coding demo and actual owner-crash/reopen tests
- Canonicalize nonexistent paths before root checks; preserve lock/body failures
- Serialize guarded writes, edits, and appends; reject missing SHA-guarded files
- Write JSON state with private permissions and clean atomic temporary files
- Track Linux child process identity and stop only owned processes; serialize CLI lifecycle
- Clean failed CLI startup resources and show failure diagnostics
- Align secret directories and shell scripts with packaged server lifecycle
- Fix OAuth authorization-code and refresh-token responses crashing during result unpacking
- Bind token requests to the presented client ID, including HTTP Basic credentials
- Enforce S256 PKCE on direct consent POSTs and preserve the method in the consent form
- Reject malformed redirects and loopback lookalikes; support exact IPv6 loopback
- Consume codes and refresh tokens only after successful request validation
- Advertise all supported client authentication methods; disable token-response caching
- Add endpoint-level OAuth regressions and Python/MCP compatibility test CI

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
