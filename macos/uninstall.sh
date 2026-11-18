#!/bin/bash
# logi-switch-sync uninstaller for macOS
set -u
LABEL="io.github.logi-switch-sync"
APP_DIR="$HOME/.logi-switch-sync"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
rm -f "$PLIST"
echo "Background agent removed."

read -r -p "Also delete $APP_DIR (config, log, Python environment)? [y/N]: " DEL
if [[ "$DEL" =~ ^[Yy] ]]; then
  rm -rf "$APP_DIR"
  echo "Deleted $APP_DIR."
fi
echo "Done."
