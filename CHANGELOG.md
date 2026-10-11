# Changelog

## Unreleased
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
