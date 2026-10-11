# Changelog

## 0.1.0 — production hardening (unreleased, on `main` ahead of PyPI)

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
