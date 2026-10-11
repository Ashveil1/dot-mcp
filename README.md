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

Options: `--port` (default 33041), `--data-dir` (default `~/.dot-mcp`),
`--json` (script-friendly output).

## What it can do (22 tools)

- **Shell:** `run_command` (background sessions + timeout) + `read_output`
- **Files:** `list_dir`, `read_file` (paged + sha256), `write_file`
  (atomic + guards), `append_file`, `edit_file`
- **Memory:** `remember`, `recall`, `forget` — persists across chats
- **Goals/Tasks:** `set_goal`, `add_update`, `get_state`, `add_task`,
  `complete_task`
- **Inbox:** `notify_user`, `poll_inbox`, `ack_inbox` — messages across chats
- **Scheduler:** `add_schedule`, `list_schedules`, `remove_schedule` —
  background jobs that run between chats
- **UI:** `open_workspace` — embedded dashboard (tasks, memory, sessions)

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
