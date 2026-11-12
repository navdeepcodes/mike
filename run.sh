#!/bin/zsh
# Runs Mike's backend in the terminal (for debugging). Normally Mike.app starts it for you.
# Don't open http://127.0.0.1:8777 in a browser while Mike.app is running: the page would take
# over Mike's microphone and speaker connection from the app.
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Setting up Mike (first run)..."
  /opt/homebrew/bin/python3.12 -m venv .venv && .venv/bin/pip install -q -r requirements.txt || exit 1
fi
exec .venv/bin/python server.py
