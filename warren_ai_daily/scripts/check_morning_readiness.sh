#!/usr/bin/env bash
#
# check_morning_readiness.sh
#
# Runs the safe dry-run path of scheduled_morning.ts (no prompts sent to
# Warren), then prints a clean human-readable summary from the status JSON.
#
# Usage:
#   npm run scheduled-morning:check
#   bash scripts/check_morning_readiness.sh

set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATUS_FILE="${PROJECT_DIR}/output/scheduled_morning_status.json"
ENV_FILE="${PROJECT_DIR}/.env"

SEP="══════════════════════════════════════════════════════════"

echo ""
echo "${SEP}"
echo "  Warren AI — Morning Readiness Check"
echo "  $(date '+%A, %b %-d %Y  %I:%M %p')"
echo "${SEP}"
echo ""
echo "  Running dry-run checks (no prompts will be sent)…"
echo ""

# Run the dry-run; capture exit code without aborting the script
cd "${PROJECT_DIR}"
WARREN_SCHEDULE_DRY_RUN=true npm run scheduled-morning 2>&1
DRY_EXIT=$?

echo ""
echo "${SEP}"
echo "  SUMMARY"
echo "${SEP}"
echo ""

if [ ! -f "${STATUS_FILE}" ]; then
    echo "  ✗ Status file not found — scheduler failed before writing status."
    echo ""
    echo "  VERDICT: ✗ UNKNOWN — see output above"
    echo ""
    exit 1
fi

# Parse status JSON with Python (available on all supported systems)
python3 - "${STATUS_FILE}" "${ENV_FILE}" <<'PYEOF'
import json, sys, os

status_file = sys.argv[1]
env_file    = sys.argv[2]

with open(status_file) as f:
    s = json.load(f)

# Check Telegram config
telegram = False
if os.path.exists(env_file):
    for line in open(env_file):
        if line.startswith('TELEGRAM_BOT_TOKEN='):
            val = line.split('=', 1)[1].strip()
            if val:
                telegram = True

bs  = s.get('bootstrap_status',  'skipped')
cdp = s.get('cdp_check_status',  'skipped')
cap = s.get('capture_status',    'skipped')
err = s.get('error_message', '').lower()

# Derive individual check results from the combined cdp_check_status + error text
if cdp == 'ok':
    cdp_line  = '✓ pass'
    tab_line  = '✓ pass'
    chat_line = '✓ pass'
elif cdp == 'failed':
    if 'chat input' in err or 'login' in err or 'cloudflare' in err:
        cdp_line  = '✓ pass'
        tab_line  = '✓ pass'
        chat_line = '✗ fail  ← login or Cloudflare check required'
    elif 'tab not found' in err:
        cdp_line  = '✓ pass'
        tab_line  = '✗ fail  ← Warren tab missing (bootstrap could not open it)'
        chat_line = '- not checked'
    else:
        cdp_line  = '✗ fail  ← Chrome CDP unreachable'
        tab_line  = '- not checked'
        chat_line = '- not checked'
else:
    cdp_line  = '- skipped'
    tab_line  = '- skipped'
    chat_line = '- skipped'

if cap == 'dry_run':
    fresh_line = '- skipped (dry run — only real runs write output)'
elif s.get('output_fresh'):
    fresh_line = '✓ pass  (run_summary.json written within last 12 h)'
else:
    fresh_line = '✗ stale (run_summary.json missing or older than 12 h)'

tg_line = '✓ configured' if telegram else '- not configured (optional — add to .env)'

print(f"  Bootstrap Chrome   : {'✓ pass' if bs == 'ok' else ('✗ fail' if bs == 'failed' else '- skipped')}")
print(f"  CDP reachable      : {cdp_line}")
print(f"  Warren tab open    : {tab_line}")
print(f"  Warren chat visible: {chat_line}")
print(f"  Output freshness   : {fresh_line}")
print(f"  Telegram alerts    : {tg_line}")
print()

# Final verdict
if bs == 'ok' and cdp == 'ok':
    print("  VERDICT: ✓ READY")
    print("           Scheduler will run at 7 AM without manual intervention.")
elif cdp == 'failed' and ('chat input' in err or 'login' in err or 'cloudflare' in err):
    print("  VERDICT: ✗ NEEDS MANUAL LOGIN")
    print("           Open Chrome → https://www.investing.com/warrenai → log in.")
    print("           Then run this check again to confirm.")
elif cdp == 'failed' and 'tab not found' in err:
    print("  VERDICT: ✗ NEEDS MANUAL SETUP")
    print("           Warren tab could not be opened. Check Chrome is working.")
elif bs == 'failed':
    print("  VERDICT: ✗ CHROME FAILED TO START")
    print("           Check: /tmp/warren-chrome-bootstrap.log")
elif bs == 'skipped' and cdp == 'skipped':
    print("  VERDICT: - NOT RUN (today is Sun/Mon or already completed today)")
else:
    raw_err = s.get('error_message', 'see logs above')
    print(f"  VERDICT: ✗ CHECK FAILED — {raw_err}")

print()
PYEOF

echo "${SEP}"
echo ""
