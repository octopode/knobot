#!/bin/bash
# Installs the keynote-render queue handler OUTSIDE the Claude-writable folder:
# Claude Science can only drop render requests and read results, never change what runs.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
APP="$HOME/Library/Application Support/keynote-render"
PLIST="$HOME/Library/LaunchAgents/local.keynote-render.plist"
ROOT="/Users/impactus/thermofiles/teaching/_keynote_render"
PY="/Users/impactus/.claude-science/conda/envs/keynote/bin/python3.12"
if [ ! -x "$PY" ]; then
  echo "ERROR: interpreter $PY not found." >&2
  echo "The render service needs a non-Apple python (Apple's /usr/bin/python3 is denied" >&2
  echo "access to ~/thermofiles by macOS). Recreate the Claude Science 'keynote' env, or" >&2
  echo "edit PY in this script and ProgramArguments in the plist to another python3." >&2
  exit 1
fi
mkdir -p "$APP" "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
mkdir -p "$ROOT"/{requests,renders,status,work,rejected}
install -m 0644 "$SRC/render_queue.py" "$APP/render_queue.py"
install -m 0644 "$SRC/export_slides.applescript" "$APP/export_slides.applescript"
install -m 0644 "$SRC/slide_ops.applescript" "$APP/slide_ops.applescript"
rm -f "$APP/export_slides.js"
install -m 0644 "$SRC/local.keynote-render.plist" "$PLIST"
plutil -lint "$PLIST"
launchctl bootout "gui/$(id -u)/local.keynote-render" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed. Render requests dropped in $ROOT/requests are exported by Keynote."
