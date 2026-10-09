#!/bin/bash
# usage: ./test.sh  (assumes server on 127.0.0.1:8088)
set -u
BASE="${1:-http://127.0.0.1:8088}"
echo "== health =="
curl -s --max-time 5 "$BASE/health"; echo
echo "== initialize =="
curl -s --max-time 10 -X POST "$BASE/mcp" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}'
echo
