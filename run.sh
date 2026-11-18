#!/usr/bin/env bash
# Runs Mike's backend in the terminal (for debugging). Normally Mike.app (Mac) or launch.py starts it for you.
# Don't open http://127.0.0.1:8777 in a browser while Mike.app is running: the page would take
# over Mike's microphone and speaker connection from the app.
cd "$(dirname "$0")"
[[ -x .venv/bin/python ]] || ./install.sh || exit 1
exec .venv/bin/python server.py
