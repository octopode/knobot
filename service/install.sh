#!/bin/bash
# Installs the keynote-render queue handler OUTSIDE the Claude-writable folder:
# Claude Science can only drop render requests and read results, never change what runs.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
APP="$HOME/Library/Application Support/keynote-render"
PLIST="$HOME/Library/LaunchAgents/local.keynote-render.plist"
LOG="$HOME/Library/Logs/keynote-render.launchd.log"

# ---- configuration: export any of these before running to override --------
#   DECK_ROOT  the ONLY tree the service reads decks from and writes results to;
#              requests naming a path outside it are rejected
#   KR_ROOT    the queue folder the agent drops requests in (must be inside DECK_ROOT
#              so the agent can write to it)
#   KR_PYTHON  a NON-Apple python 3.10+: Apple's /usr/bin/python3 is denied access to
#              non-boot volumes under launchd, which breaks every render
: "${DECK_ROOT:=$HOME/thermofiles/teaching}"
: "${KR_ROOT:=$DECK_ROOT/_keynote_render}"
: "${KR_PYTHON:=$HOME/.claude-science/conda/envs/keynote/bin/python3.12}"
ROOT="$KR_ROOT"
PY="$KR_PYTHON"
if [ ! -x "$PY" ]; then
  echo "ERROR: interpreter $PY not found." >&2
  echo "The render service needs a non-Apple python (Apple's /usr/bin/python3 is denied" >&2
  echo "access to ~/thermofiles by macOS). Recreate the Claude Science 'keynote' env, or" >&2
  echo "or export KR_PYTHON=/path/to/python3 before running this script." >&2
  exit 1
fi
# Compile-check the AppleScripts BEFORE installing them. A single syntax error makes
# osascript fail at RUN time for every op in the file (renders use a separate script and
# keep working), which is an expensive way to discover a typo. osacompile resolves
# Keynote's terminology only outside a sandbox, so this check belongs here.
for S in export_slides.applescript slide_ops.applescript; do
  if ! osacompile -o /tmp/kr-syntax-check.scpt "$SRC/$S" 2>/tmp/kr-syntax-check.err; then
    echo "ERROR: $S does not compile; nothing was installed." >&2
    cat /tmp/kr-syntax-check.err >&2
    rm -f /tmp/kr-syntax-check.scpt /tmp/kr-syntax-check.err
    exit 1
  fi
done
rm -f /tmp/kr-syntax-check.scpt /tmp/kr-syntax-check.err

mkdir -p "$APP" "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
mkdir -p "$ROOT"/{requests,renders,status,work,rejected}
install -m 0644 "$SRC/render_queue.py" "$APP/render_queue.py"
install -m 0644 "$SRC/export_slides.applescript" "$APP/export_slides.applescript"
install -m 0644 "$SRC/slide_ops.applescript" "$APP/slide_ops.applescript"
rm -f "$APP/export_slides.js"
sed -e "s|@@PYTHON@@|$PY|g" \
    -e "s|@@APP@@|$APP|g" \
    -e "s|@@DECK_ROOT@@|$DECK_ROOT|g" \
    -e "s|@@KR_ROOT@@|$ROOT|g" \
    -e "s|@@LOG@@|$LOG|g" \
    "$SRC/local.keynote-render.plist.in" > "$PLIST"
chmod 0644 "$PLIST"
plutil -lint "$PLIST"
launchctl bootout "gui/$(id -u)/local.keynote-render" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed."
echo "  deck root : $DECK_ROOT"
echo "  queue     : $ROOT/requests"
echo "  python    : $PY"
