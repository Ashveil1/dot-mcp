#!/bin/bash
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
echo "OK. run: ./run.sh"

# install the package itself (editable) so `python -m dot_mcp.server` works
.venv/bin/pip install -e . || echo "editable install failed — run.sh/expose.sh still set PYTHONPATH=src"
