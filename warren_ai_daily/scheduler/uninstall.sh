#!/usr/bin/env bash
# uninstall.sh — Remove the warren-ai-daily systemd user timer
# Usage: npm run uninstall-schedule
set -e

SYSTEMD_DIR="$HOME/.config/systemd/user"
TIMER="warren-ai-daily.timer"
SERVICE="warren-ai-daily.service"

echo ""
echo "Removing Warren AI Daily Scheduler..."
echo ""

# Stop and disable if running
systemctl --user stop    "$TIMER"   2>/dev/null || true
systemctl --user disable "$TIMER"   2>/dev/null || true
systemctl --user stop    "$SERVICE" 2>/dev/null || true

# Remove unit files from systemd
rm -f "$SYSTEMD_DIR/$TIMER"
rm -f "$SYSTEMD_DIR/$SERVICE"

# Reload daemon
systemctl --user daemon-reload

echo "✓ Timer and service removed."
echo ""
echo "The scheduler will no longer run at 7 AM."
echo "Source files remain in scheduler/ — re-run npm run install-schedule to reinstall."
echo ""
