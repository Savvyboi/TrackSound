#!/bin/sh
# TrackSound installer for macOS and Linux. Run:
#
#   curl -LsSf https://raw.githubusercontent.com/Savvyboi/TrackSound/HEAD/install.sh | sh
#
# It installs uv (Astral's Python installer) if needed, then TrackSound with its own Python and
# ffmpeg, adds a desktop shortcut, and starts it. Nothing needs admin rights; everything goes
# into your home folder. Uninstall: `uv tool uninstall tracksound`.
#
# Options (environment variables): TRACKSOUND_REF=<branch or tag> installs that version from
# GitHub instead of the latest release; TRACKSOUND_NO_START=1 skips starting it.
set -eu

REPO="${TRACKSOUND_REPO:-Savvyboi/TrackSound}"
say() { printf '%s\n' "$*"; }

say "==> Installing TrackSound"

if ! command -v uv >/dev/null 2>&1; then
    say "==> Installing uv (https://docs.astral.sh/uv/)"
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:${XDG_BIN_HOME:-$HOME/.local/bin}:$PATH"
if ! command -v uv >/dev/null 2>&1; then
    say "uv was installed but is not on PATH yet. Open a new terminal and run this installer again."
    exit 1
fi

SRC=""
if [ -z "${TRACKSOUND_REF:-}" ]; then
    # newest release wheel, if a release exists
    SRC=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null \
        | grep -o '"browser_download_url": *"[^"]*\.whl"' | head -n 1 | sed 's/.*"\(https[^"]*\)"$/\1/') || true
fi
if [ -z "$SRC" ]; then
    REF="${TRACKSOUND_REF:-HEAD}"
    SRC="tracksound @ https://github.com/$REPO/archive/$REF.zip#subdirectory=backend"
fi

say "==> Installing from $SRC"
uv tool install --force --python 3.12 "$SRC"
uv tool update-shell >/dev/null 2>&1 || true

TS="$(command -v tracksound || echo "$HOME/.local/bin/tracksound")"
"$TS" shortcut || true

say ""
say "TrackSound is installed. Start it any time with the desktop shortcut or by running: tracksound"
if [ -z "${TRACKSOUND_NO_START:-}" ]; then
    say "Starting it now (it opens in your browser; close this window or press Ctrl+C to stop)..."
    exec "$TS"
fi
