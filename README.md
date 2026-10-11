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
Owner secret          <shown once, stored on your machine>
OAuth Client ID       dots-xxxxxxxx
OAuth Client secret   <shown once, stored on your machine>
```

Add the **Public URL** as a custom MCP connector (auth = OAuth).
Approve once with the owner secret when asked. The tunnel URL stays the
same on restart; the free tunnel expires after 24h or 2h idle.

Options: `--port` (default 33041), `--data-dir` (default `~/.dot-mcp`),
`--json` (script-friendly output).

## Local-only mode (no tunnel)

```bash
export OPENAI_API_KEY=sk-...
python -m dot_mcp.local_agent            # chat loop
python -m dot_mcp.local_agent --list     # list tools only
```

## Develop

```bash
./setup.sh                      # venv + editable install
.venv/bin/python -m pytest tests/ -q   # test suite
python -m build                 # wheel in dist/
```

## License

MIT
