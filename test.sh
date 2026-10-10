#!/bin/bash
# usage: ./test.sh [mcp_endpoint]  (assumes server on 127.0.0.1:33041)
#   endpoint defaults to http://127.0.0.1:33041/mcp/
#   auth: $MCP_PATH_SECRET or ~/.config/dot-mcp/path_secret as Bearer
set -u
ENDPOINT="${1:-http://127.0.0.1:33041/mcp/}"
BASE="${ENDPOINT%%/mcp*}"
SECRET="${MCP_PATH_SECRET:-$(cat "${XDG_CONFIG_HOME:-$HOME/.config}/dot-mcp/path_secret" 2>/dev/null || true)}"
echo "== health =="
curl -s --max-time 5 "$BASE/health"; echo
echo "== initialize =="
curl -s --max-time 10 -X POST "$ENDPOINT" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Authorization: Bearer $SECRET" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}'
echo
