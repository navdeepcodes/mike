#!/usr/bin/env bash
# Mike installer for macOS (and Linux).
#
# One line, in Terminal:
#   curl -fsSL https://raw.githubusercontent.com/navdeepcodes/mike/main/install.sh | bash
# or, from a copy of the repo:
#   ./install.sh
#
# What it does: finds Python 3.10-3.12 (offers Homebrew's 3.12 on a Mac), clones Mike to ~/mike
# (unless run from a copy), installs his packages in a private .venv, downloads the voice models
# (~150 MB), and on a Mac builds Mike.app into ~/Applications.
# Options: MIKE_DIR=folder, MIKE_NO_MODELS=1, MIKE_NO_APP=1.
set -euo pipefail
REPO="https://github.com/navdeepcodes/mike.git"
say() { printf "\033[36m-> %s\033[0m\n" "$*"; }
fail() { printf "\033[31mx %s\033[0m\n" "$*"; exit 1; }

# ---- where Mike lives ---------------------------------------------------------------------
HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo "")"
if [[ -n "$HERE" && -f "$HERE/server.py" ]]; then
  DIR="$HERE"
else
  DIR="${MIKE_DIR:-$HOME/mike}"
  if [[ ! -f "$DIR/server.py" ]]; then
    command -v git >/dev/null || fail "git is needed (Mac: xcode-select --install)."
    say "Downloading Mike to $DIR"
    git clone --depth 1 "$REPO" "$DIR"
  fi
fi
cd "$DIR"

# ---- Python ---------------------------------------------------------------------------------
find_python() {
  for c in python3.12 python3.11 python3.10 python3 /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,12) else 1)' 2>/dev/null; then
      command -v "$c"; return 0
    fi
  done
  return 1
}
PY="$(find_python || true)"
if [[ -z "$PY" && "$(uname)" == "Darwin" ]] && command -v brew >/dev/null; then
  read -r -p "Mike needs Python 3.12. Install it with Homebrew? [Y/n] " a </dev/tty || a=""
  if [[ -z "$a" || "$a" =~ ^[Yy] ]]; then brew install python@3.12; PY="$(find_python || true)"; fi
fi
[[ -n "$PY" ]] || fail "Install Python 3.12 (Mac: brew install python@3.12, or python.org; Linux: your package manager), then run this again."
say "Using Python: $PY"

# ---- packages -------------------------------------------------------------------------------
if [[ ! -x .venv/bin/python ]]; then
  say "Creating Mike's private Python environment"
  "$PY" -m venv .venv
fi
say "Installing Mike's packages (a few minutes the first time)"
.venv/bin/python -m pip install --upgrade pip -q
.venv/bin/python -m pip install -r requirements.txt -q || fail "Package install failed. Scroll up for the error."

if [[ "${MIKE_NO_MODELS:-}" != "1" ]]; then
  say "Downloading the voice models (~150 MB, once)"
  .venv/bin/python models.py || echo "  (Mike will retry the download when he starts.)"
fi

# ---- the app --------------------------------------------------------------------------------
if [[ "$(uname)" == "Darwin" ]]; then
  if [[ "${MIKE_NO_APP:-}" == "1" ]]; then
    echo "Skipped building Mike.app (MIKE_NO_APP=1). Run ./app/build.sh to build it."
  elif xcrun --find swiftc >/dev/null 2>&1; then
    say "Building Mike.app"
    ./app/build.sh
    echo
    printf "\033[32mMike is installed.\033[0m Open Mike from ~/Applications (or Spotlight), allow the microphone, and say \"Hey Mike\".\n"
    echo "macOS asks once for Accessibility and Screen Recording when Mike first controls an app or looks at the screen."
  else
    echo "To build the Mike app, install Apple's command line tools (xcode-select --install) and run ./install.sh again."
    echo "Meanwhile: .venv/bin/python launch.py opens Mike in your browser."
  fi
else
  APPS="$HOME/.local/share/applications"
  mkdir -p "$APPS"
  cat > "$APPS/mike.desktop" <<DESK
[Desktop Entry]
Name=Mike
Comment=Your voice assistant
Exec=$DIR/.venv/bin/python $DIR/launch.py
Icon=$DIR/app/icon-1024.png
Terminal=false
Type=Application
Categories=Utility;
DESK
  echo
  printf "\033[32mMike is installed.\033[0m Start \"Mike\" from your app menu (or: .venv/bin/python launch.py) and allow the microphone.\n"
fi
echo "Add a free model key in Settings (Groq, Gemini, OpenRouter or NVIDIA), or install Claude Code to use your Claude plan."
