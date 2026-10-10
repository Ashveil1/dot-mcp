#!/bin/bash
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "run ./setup.sh first"; exit 1
fi
.venv/bin/python dot_mcp.py --check
PORT="${MCP_PORT:-33041}"
echo "--- starting on $PORT ---"
MCP_PORT="$PORT" .venv/bin/python dot_mcp.py
