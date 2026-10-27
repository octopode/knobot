#!/bin/bash
set -euo pipefail
launchctl bootout "gui/$(id -u)/local.keynote-render" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/local.keynote-render.plist"
rm -rf "$HOME/Library/Application Support/keynote-render"
echo "keynote-render removed. (teaching/_keynote_render with its renders is left in place.)"
