#!/usr/bin/env bash
# install.sh — Install the warren-ai-daily systemd user timer
# Usage: npm run install-schedule
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEMD_DIR="$HOME/.config/systemd/user"
SERVICE="warren-ai-daily.service"
TIMER="warren-ai-daily.timer"

echo ""
echo "Installing Warren AI Daily Scheduler..."
echo "Service file: $SCRIPT_DIR/$SERVICE"
echo "Timer file:   $SCRIPT_DIR/$TIMER"
echo "Target dir:   $SYSTEMD_DIR"
echo ""

# Create systemd user directory if needed
mkdir -p "$SYSTEMD_DIR"

# Copy unit files
cp "$SCRIPT_DIR/$SERVICE" "$SYSTEMD_DIR/$SERVICE"
cp "$SCRIPT_DIR/$TIMER"   "$SYSTEMD_DIR/$TIMER"

# Reload systemd user daemon
systemctl --user daemon-reload

# Enable and start the timer
systemctl --user enable "$TIMER"
systemctl --user start  "$TIMER"

echo ""
echo "✓ Timer installed and started."
echo ""
echo "Status:"
systemctl --user status "$TIMER" --no-pager 2>&1 | head -15

echo ""
echo "Next scheduled fires:"
systemctl --user list-timers "$TIMER" --no-pager 2>&1

echo ""
echo "To view logs:       journalctl --user -u warren-ai-daily --since today"
echo "To check status:    npm run check-schedule"
echo "To uninstall:       npm run uninstall-schedule"
echo ""
