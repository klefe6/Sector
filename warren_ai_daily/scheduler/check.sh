#!/usr/bin/env bash
# check.sh — Check the status of the warren-ai-daily scheduler
# Usage: npm run check-schedule
set -e

TIMER="warren-ai-daily.timer"
SERVICE="warren-ai-daily.service"
TODAY=$(date +%Y-%m-%d)
OUTPUT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/output"
LOG_DIR="$OUTPUT_DIR/scheduler_logs"

echo ""
echo "══════════════════════════════════════════════════"
echo " Warren AI Daily Scheduler — Status Check"
echo "══════════════════════════════════════════════════"
echo ""

# Timer status
echo "[ Systemd Timer ]"
systemctl --user is-active "$TIMER" 2>/dev/null \
  && echo "  Timer: ACTIVE ✓" \
  || echo "  Timer: INACTIVE (not installed)"
echo ""

echo "[ Next fires ]"
systemctl --user list-timers "$TIMER" --no-pager 2>/dev/null \
  || echo "  (timer not installed)"
echo ""

# Today's run output
echo "[ Today's Run ($TODAY) ]"
SUMMARY="$OUTPUT_DIR/$TODAY/run_summary.json"
if [ -f "$SUMMARY" ]; then
  SUCCESS=$(python3 -c "import json,sys; d=json.load(open('$SUMMARY')); print('success='+str(d.get('success','?'))+' validationPassed='+str(d.get('validationPassed','?')))" 2>/dev/null || echo "parse error")
  echo "  run_summary.json: $SUCCESS"
else
  echo "  No run_summary.json — not completed today."
  STEP_COUNT=$(ls "$OUTPUT_DIR/$TODAY/step_"*.md 2>/dev/null | wc -l)
  if [ "$STEP_COUNT" -gt "0" ]; then
    echo "  Partial run: $STEP_COUNT/5 step files found (resume mode will continue)."
  else
    echo "  No step files found. Run not started."
  fi
fi
echo ""

# Scheduler log for today
echo "[ Scheduler Log ($TODAY) ]"
LOG_FILE="$LOG_DIR/$TODAY.log"
if [ -f "$LOG_FILE" ]; then
  echo "  Log file: $LOG_FILE"
  echo "  Last 10 lines:"
  tail -10 "$LOG_FILE" | sed 's/^/    /'
else
  echo "  No scheduler log for today."
fi
echo ""

# Journal output (last 20 lines)
echo "[ Journal (last 20 lines) ]"
journalctl --user -u warren-ai-daily --since today --no-pager 2>/dev/null | tail -20 | sed 's/^/  /' \
  || echo "  (no journal entries)"
echo ""

echo "══════════════════════════════════════════════════"
echo " Commands:"
echo "   npm run install-schedule    — install / update timer"
echo "   npm run uninstall-schedule  — remove timer"
echo "   npm run scheduled-morning:dry — dry-run check"
echo "══════════════════════════════════════════════════"
echo ""
