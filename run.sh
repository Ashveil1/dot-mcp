#!/bin/bash
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "run ./setup.sh first"; exit 1
fi
.venv/bin/python dot_mcp.py --check
echo "--- starting on 8088 ---"
MCP_PORT=8088 .venv/bin/python dot_mcp.py
