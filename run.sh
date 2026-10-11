#!/bin/bash
cd "$(dirname "$0")"
PY="${PYTHONPATH:-}"
export PYTHONPATH="$(pwd)/src:$PY"
if [ ! -x .venv/bin/python ]; then
  echo "run ./setup.sh first"; exit 1
fi
.venv/bin/python -m dot_mcp.server --check
echo "--- starting on 33041 ---"
MCP_PORT=33041 .venv/bin/python -m dot_mcp.server
