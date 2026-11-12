#!/bin/bash
# Turns the daily deal check on or off (macOS, uses the built-in launchd scheduler; free).
#
#   bash scripts/schedule_daily_check.sh on       # check every day at 9:00 (default)
#   bash scripts/schedule_daily_check.sh on 18:30 # check every day at 18:30
#   bash scripts/schedule_daily_check.sh off      # stop the daily check
#   bash scripts/schedule_daily_check.sh status
#
# If your Mac is asleep at that time, the check runs when it wakes up.
# Output goes to data/daily_check.log. The check only saves alerts; it never buys anything.

set -euo pipefail
LABEL="com.vinylagent.dailycheck"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"

case "${1:-status}" in
  on)
    TIME="${2:-9:00}"
    HOUR=$((10#${TIME%%:*})); MIN=$((10#${TIME##*:}))
    [ -x "$PY" ] || { echo "Can't find $PY. Set up the app first (see README)."; exit 1; }
    mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/data"
    cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$PY</string><string>$ROOT/scripts/check_deals.py</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MIN</integer></dict>
  <key>StandardOutPath</key><string>$ROOT/data/daily_check.log</string>
  <key>StandardErrorPath</key><string>$ROOT/data/daily_check.log</string>
</dict>
</plist>
PLISTEOF
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    printf "Daily deal check is ON: every day at %d:%02d. Log: data/daily_check.log\n" "$HOUR" "$MIN"
    ;;
  off)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Daily deal check is OFF."
    ;;
  status)
    if [ -f "$PLIST" ]; then echo "Daily deal check is ON ($PLIST)."; else echo "Daily deal check is OFF."; fi
    ;;
  *)
    echo "Usage: bash scripts/schedule_daily_check.sh on [HH:MM] | off | status"; exit 1;;
esac
