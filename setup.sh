#!/bin/bash
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install --upgrade pip
.venv/bin/pip install "mcp>=1,<3" uvicorn
echo "OK. run: ./run.sh"
