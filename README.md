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

File roots are guardrails, not a shell sandbox. A shell credential still grants
full access as the server's OS user. Use a separate OS user/container for
untrusted workloads.

Options: `--port` (default 33041), `--data-dir` (default `~/.dot-mcp`),
`--json` (script-friendly output).

## What it can do (33 tools in this development revision)

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
The original `run_command` remains the legacy synchronous API. Use the new
start/status tools for durable asynchronous work; `read_output` handles both.

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
