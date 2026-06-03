#!/usr/bin/env bash
#
# bootstrap_warren_chrome.sh
#
# Ensures Chrome is running with CDP on port 9222 and that the Warren AI
# tab is open. Called automatically by the systemd-scheduled runner before
# any CDP operations are attempted.
#
# Exit 0 — Chrome is up and Warren tab is open (or now opening).
# Exit 1 — Chrome could not start or CDP never became reachable.
#
# Does NOT attempt to log in, bypass Cloudflare, or interact with the page.
# Login failures are detected later by the Playwright readiness check.
#
# Environment (with defaults):
#   CHROME_CDP_URL       http://127.0.0.1:9222
#   WARREN_AI_URL        https://www.investing.com/warrenai
#   CHROME_PROFILE_DIR   $HOME/.warren-ai-chrome-profile
#   CDP_WAIT_SEC         25
#   DISPLAY              :1

set -uo pipefail

# ── Config ────────────────────────────────────────────────────────────────
CDP_BASE="${CHROME_CDP_URL:-http://127.0.0.1:9222}"
WARREN_URL="${WARREN_AI_URL:-https://www.investing.com/warrenai}"
CHROME_PROFILE="${CHROME_PROFILE_DIR:-$HOME/.warren-ai-chrome-profile}"
CDP_WAIT_SEC="${CDP_WAIT_SEC:-25}"
export DISPLAY="${DISPLAY:-:1}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)/output"
STATUS_FILE="${OUTPUT_DIR}/bootstrap_status.json"
CHROME_LOG="/tmp/warren-chrome-bootstrap.log"

mkdir -p "${OUTPUT_DIR}"

# ── State variables (updated as checks run) ───────────────────────────────
_ts()  { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
_log() { echo "[bootstrap $(_ts)] $*"; }

STARTED_AT="$(_ts)"
CDP_REACHABLE=false
CHROME_STARTED=false
WARREN_TAB_PRESENT=false

# ── Status writer ─────────────────────────────────────────────────────────
# All values are passed as positional args — the heredoc is quoted ('PYEOF')
# so no bash expansion happens inside the Python source.  Python booleans
# are produced from the "true"/"false" string args with == comparison.
_write_status() {
    local _status="$1"
    local _err="${2:-}"
    python3 - \
        "${_status}" \
        "${CDP_REACHABLE}" \
        "${CHROME_STARTED}" \
        "${WARREN_TAB_PRESENT}" \
        "${_err}" \
        "${WARREN_URL}" \
        "${STARTED_AT}" \
        "$(_ts)" \
        "${STATUS_FILE}" \
        <<'PYEOF'
import json, sys

(status, cdp_r, chr_s, tab_p,
 err_msg, warren_url, started_at, finished_at, out_path) = sys.argv[1:10]

d = {
    "status":             status,
    "cdp_reachable":      cdp_r == "true",
    "chrome_started":     chr_s == "true",
    "warren_tab_present": tab_p == "true",
    "warren_url":         warren_url,
    "started_at":         started_at,
    "finished_at":        finished_at,
    "error_message":      err_msg,
}
with open(out_path, "w") as f:
    json.dump(d, f, indent=2)
print(f"[bootstrap] Status written → {out_path}")
PYEOF
}

# ── Helper: find Warren tab URL in CDP /json response ─────────────────────
_tab_url() {
    echo "$1" | python3 -c "
import json, sys
try:
    tabs = json.loads(sys.stdin.read())
    matches = [t for t in tabs if 'warrenai' in t.get('url', '')]
    print(matches[0]['url'] if matches else '')
except Exception:
    print('')
"
}

# ── 1. Check if CDP is already reachable ─────────────────────────────────
if curl -sf --max-time 3 "${CDP_BASE}/json/version" > /dev/null 2>&1; then
    _log "Chrome CDP already reachable at ${CDP_BASE}."
    CDP_REACHABLE=true
else
    _log "Chrome CDP not reachable — launching Chrome with profile: ${CHROME_PROFILE}"
    CHROME_STARTED=true

    nohup google-chrome \
        --remote-debugging-port=9222 \
        --remote-allow-origins='*' \
        --user-data-dir="${CHROME_PROFILE}" \
        --no-first-run \
        --no-default-browser-check \
        --disable-session-crashed-bubble \
        "${WARREN_URL}" \
        > "${CHROME_LOG}" 2>&1 &

    CHROME_PID=$!
    _log "Chrome launched (PID ${CHROME_PID}). Waiting up to ${CDP_WAIT_SEC}s for CDP..."

    READY=false
    for i in $(seq 1 "${CDP_WAIT_SEC}"); do
        sleep 1
        if curl -sf --max-time 2 "${CDP_BASE}/json/version" > /dev/null 2>&1; then
            _log "CDP reachable after ${i}s ✓"
            READY=true
            CDP_REACHABLE=true
            break
        fi
    done

    if ! ${READY}; then
        _log "ERROR: CDP never became reachable after ${CDP_WAIT_SEC}s."
        _log "Check Chrome launch log: ${CHROME_LOG}"
        _write_status "FAIL" "Chrome launched but CDP unreachable after ${CDP_WAIT_SEC}s. See ${CHROME_LOG}"
        exit 1
    fi
fi

# ── 2. Check for Warren AI tab ────────────────────────────────────────────
TABS_JSON=$(curl -sf --max-time 5 "${CDP_BASE}/json" 2>/dev/null || echo "[]")
WARREN_FOUND=$(_tab_url "${TABS_JSON}")

if [ -n "${WARREN_FOUND}" ]; then
    _log "Warren AI tab already open: ${WARREN_FOUND}"
    WARREN_TAB_PRESENT=true
else
    _log "Warren AI tab not found — opening ${WARREN_URL} via CDP..."

    ENCODED_URL=$(python3 -c \
        "import urllib.parse, sys; print(urllib.parse.quote('${WARREN_URL}', safe=':/?=&@'))")

    curl -sf --max-time 5 "${CDP_BASE}/json/new?${ENCODED_URL}" > /dev/null 2>&1 || true

    # Brief wait for Chrome to register the new tab
    sleep 3

    # Verify
    TABS_JSON=$(curl -sf --max-time 5 "${CDP_BASE}/json" 2>/dev/null || echo "[]")
    WARREN_FOUND=$(_tab_url "${TABS_JSON}")

    if [ -n "${WARREN_FOUND}" ]; then
        _log "Warren AI tab now open: ${WARREN_FOUND} ✓"
        WARREN_TAB_PRESENT=true
    else
        _log "WARN: Tab URL still loading — will be verified by Playwright CDP check next."
    fi
fi

# ── 3. Write status and exit ──────────────────────────────────────────────
_write_status "OK" ""
_log "Bootstrap complete — exit 0"
exit 0
