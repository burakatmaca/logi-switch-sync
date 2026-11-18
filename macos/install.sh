#!/bin/bash
# logi-switch-sync installer for macOS
# Installs into ~/.logi-switch-sync and runs in the background via a launchd user agent.
set -euo pipefail

LABEL="io.github.logi-switch-sync"
APP_DIR="$HOME/.logi-switch-sync"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG="$APP_DIR/config.json"
LOG="$APP_DIR/follow.log"

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

command -v python3 >/dev/null 2>&1 || die "python3 not found. Install it with 'brew install python' or 'xcode-select --install'."

say "Installing to $APP_DIR"
mkdir -p "$APP_DIR"
cp "$SRC_DIR/logi_switch.py" "$APP_DIR/"
if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
  python3 -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip >/dev/null 2>&1 || true
"$APP_DIR/.venv/bin/pip" install -q -r "$SRC_DIR/requirements.txt"
PY="$APP_DIR/.venv/bin/python"

# Stop a running instance so the receiver is free for the device scan
launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true

say "Devices on the receiver (keyboard and mouse should be on THIS computer now):"
"$PY" "$APP_DIR/logi_switch.py" list || warn "Device scan failed - see Troubleshooting in README.md"
echo

KEEP="n"
if [ -f "$CONFIG" ]; then
  read -r -p "An existing configuration was found. Keep it? [Y/n]: " KEEP
  KEEP=${KEEP:-y}
fi

if [[ ! "$KEEP" =~ ^[Yy] ]]; then
  echo "When you press an Easy-Switch key to LEAVE this computer, which channel"
  read -r -p "should the mouse follow to? (the other computer's channel, 1-3): " TO
  [[ "$TO" =~ ^[123]$ ]] || die "Channel must be 1, 2 or 3."

  PEER=""; SECRET=""
  read -r -p "Enable clipboard text sync with the other computer? [y/N]: " CLIP
  if [[ "$CLIP" =~ ^[Yy] ]]; then
    read -r -p "IP address of the other computer: " PEER
    [ -n "$PEER" ] || die "IP address is required for clipboard sync."
    read -r -p "Shared secret (leave empty to generate a new one): " SECRET
    if [ -z "$SECRET" ]; then
      SECRET=$(openssl rand -hex 16)
    fi
  fi

  TO="$TO" PEER="$PEER" SECRET="$SECRET" "$PY" - "$CONFIG" <<'EOF'
import json, os, sys
cfg = {"to": int(os.environ["TO"])}
if os.environ["PEER"]:
    cfg.update(peer=os.environ["PEER"], secret=os.environ["SECRET"], port=47821)
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2)
EOF
  chmod 600 "$CONFIG"
fi

mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>-u</string>
    <string>$APP_DIR/logi_switch.py</string>
    <string>follow</string>
    <string>--config</string><string>$CONFIG</string>
    <string>--forever</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF

: > "$LOG"
launchctl bootstrap "gui/$(id -u)" "$PLIST"
sleep 4

echo
say "Installed. Recent log ($LOG):"
tail -n 5 "$LOG" || true
echo
IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "unknown")
echo "  This Mac's IP : $IP"
if grep -q '"secret"' "$CONFIG"; then
  echo "  Shared secret : $("$PY" -c 'import json,sys;print(json.load(open(sys.argv[1]))["secret"])' "$CONFIG")"
  echo "  -> Use this IP and secret when installing on the other computer."
  warn "If macOS asks whether Python may accept incoming connections, click Allow."
fi
echo
echo "  Follow the log : tail -f $LOG"
echo "  Uninstall      : $SRC_DIR/macos/uninstall.sh"
warn "If the log says the receiver cannot be opened, add Python to System Settings > Privacy & Security > Input Monitoring."
