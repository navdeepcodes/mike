#!/usr/bin/env bash
# Removes Mike from macOS/Linux: the app and his private Python environment.
# Your data (memory, keys, settings in data/) is kept; MIKE_REMOVE_DATA=1 deletes everything.
DIR="$(cd "$(dirname "$0")" && pwd)"
echo "-> Stopping Mike"
pkill -f "$DIR/server.py" 2>/dev/null
[[ "$(uname)" == "Darwin" ]] && osascript -e 'quit app "Mike"' 2>/dev/null
echo "-> Removing the app"
rm -rf "$HOME/Applications/Mike.app" "$HOME/.local/share/applications/mike.desktop"
echo "-> Removing Mike's Python environment"
rm -rf "$DIR/.venv"
if [[ "${MIKE_REMOVE_DATA:-}" == "1" ]]; then
  rm -rf "$DIR"; echo "Mike and all his data are removed."
else
  echo "Mike is uninstalled. Your memory, keys and settings are still in $DIR/data."
fi
