#!/usr/bin/env bash
# Warren AI readiness check — safe to run anytime (no browser automation).
# Usage: bash scripts/check_warren_readiness.sh [--json]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RRG_ROOT="$(cd "$PROJECT_DIR/.." && pwd)"
OUTPUT_DIR="${OUTPUT_DIR:-$PROJECT_DIR/output}"
JSON_MODE=false
[[ "${1:-}" == "--json" ]] && JSON_MODE=true

pass=0
warn=0
fail=0
lines=()

note() {
  local level="$1" msg="$2"
  lines+=("[$level] $msg")
  case "$level" in
    OK)   pass=$((pass + 1)) ;;
    WARN) warn=$((warn + 1)) ;;
    FAIL) fail=$((fail + 1)) ;;
  esac
}

# ── Timer ────────────────────────────────────────────────────────────────────
timer_enabled="unknown"
timer_next="unknown"
if systemctl --user is-enabled warren-ai-daily.timer &>/dev/null; then
  timer_enabled="enabled"
  note OK "warren-ai-daily.timer is enabled"
else
  timer_enabled="disabled"
  note FAIL "warren-ai-daily.timer is NOT enabled"
fi

timer_line="$(systemctl --user list-timers warren-ai-daily.timer --no-pager 2>/dev/null | awk 'NR==3 {print $1, $2, $3, $4, $5, $6}')"
if [[ -n "$timer_line" ]]; then
  timer_next="$timer_line"
  note OK "Next timer run: $timer_next"
else
  note WARN "Could not read warren-ai-daily.timer schedule"
fi

# ── Last service result ──────────────────────────────────────────────────────
svc_result="$(systemctl --user show warren-ai-daily.service -p Result --value 2>/dev/null || echo unknown)"
svc_active="$(systemctl --user show warren-ai-daily.service -p ActiveState --value 2>/dev/null || echo unknown)"
if [[ "$svc_result" == "success" || "$svc_active" == "inactive" ]]; then
  note OK "Last service result: ${svc_result:-inactive} (${svc_active})"
else
  note WARN "Last service result: ${svc_result} (${svc_active})"
fi

today_log="$OUTPUT_DIR/scheduler_logs/$(date +%F).log"
if [[ -f "$today_log" ]]; then
  if grep -qi "automation is DISABLED" "$today_log" 2>/dev/null; then
    note OK "Today's scheduler log: skipped (scheduled off-day or duplicate)"
  elif grep -qi "Run SUCCEEDED" "$today_log" 2>/dev/null; then
    note OK "Today's scheduler log: run succeeded"
  elif grep -qiE "failed|error|CDP not reachable" "$today_log" 2>/dev/null; then
    note FAIL "Today's scheduler log shows errors — see $today_log"
  else
    note WARN "Today's scheduler log exists but outcome unclear: $today_log"
  fi
else
  note WARN "No scheduler log for today: $today_log"
fi

# ── Day-of-week schedule ─────────────────────────────────────────────────────
dow="$(date +%u)"  # 1=Mon .. 7=Sun
if [[ "$dow" -eq 1 || "$dow" -eq 7 ]]; then
  note OK "Today is a scheduled off-day for Warren (Mon/Sun — runs Tue–Sat)"
else
  note OK "Today is a Warren run day (Tue–Sat)"
fi

# ── Chrome CDP ───────────────────────────────────────────────────────────────
CDP_URL="${CHROME_CDP_URL:-http://127.0.0.1:9222}"
if curl -sf --connect-timeout 3 "$CDP_URL/json/version" >/dev/null 2>&1; then
  note OK "Chrome CDP reachable at $CDP_URL"
  warren_tab="$(curl -sf --connect-timeout 3 "$CDP_URL/json/list" 2>/dev/null \
    | python3 -c "import sys,json; tabs=json.load(sys.stdin); print(next((t.get('url','') for t in tabs if 'warren' in t.get('url','').lower()), ''))" 2>/dev/null || true)"
  if [[ -n "$warren_tab" ]]; then
    note OK "Warren AI tab open: $warren_tab"
  else
    note WARN "CDP up but no Warren AI tab found in open tabs"
  fi
else
  note FAIL "Chrome CDP NOT reachable at $CDP_URL"
fi

# ── Output freshness ─────────────────────────────────────────────────────────
latest_dir=""
if [[ -d "$OUTPUT_DIR" ]]; then
  latest_dir="$(find "$OUTPUT_DIR" -maxdepth 1 -type d -name '20??-??-??' 2>/dev/null | sort | tail -1)"
fi
if [[ -n "$latest_dir" && -f "$latest_dir/run_summary.json" ]]; then
  latest_date="$(basename "$latest_dir")"
  note OK "Latest successful output: $latest_date ($latest_dir/run_summary.json)"
else
  note FAIL "No successful Warren output directory with run_summary.json found"
fi

# ── Stocks website health ────────────────────────────────────────────────────
STOCKS_HEALTH="${STOCKS_HEALTH_URL:-http://127.0.0.1:8061/healthz}"
stocks_code="$(curl -s -o /tmp/warren_readiness_stocks.json -w '%{http_code}' --connect-timeout 5 "$STOCKS_HEALTH" 2>/dev/null || echo 000)"
if [[ "$stocks_code" == "200" ]] && grep -q '"ok"' /tmp/warren_readiness_stocks.json 2>/dev/null; then
  note OK "Stocks website health OK ($STOCKS_HEALTH)"
elif [[ "$stocks_code" == "200" ]]; then
  note WARN "Stocks website HTTP 200 but /healthz body is not JSON ok (legacy Dash fallback?)"
else
  note FAIL "Stocks website down ($STOCKS_HEALTH → HTTP $stocks_code)"
fi

# ── Portfolio export ─────────────────────────────────────────────────────────
portfolio_json="$RRG_ROOT/config/current_portfolio.json"
portfolio_summary="$OUTPUT_DIR/portfolio_summary.json"
if [[ -f "$portfolio_json" ]]; then
  note OK "Portfolio export present: config/current_portfolio.json"
elif [[ -f "$portfolio_summary" ]]; then
  note OK "Portfolio summary present: output/portfolio_summary.json"
else
  note WARN "No portfolio export found (config/current_portfolio.json or output/portfolio_summary.json)"
fi

# ── Required env vars ────────────────────────────────────────────────────────
env_file="$PROJECT_DIR/.env"
if [[ -f "$env_file" ]]; then
  # shellcheck disable=SC1090
  set -a; source "$env_file"; set +a
fi
for var in CHROME_CDP_URL OUTPUT_DIR; do
  if [[ -n "${!var:-}" ]]; then
    note OK "Env $var is set"
  else
    note WARN "Env $var is not set (using defaults)"
  fi
done

# ── Summary ──────────────────────────────────────────────────────────────────
overall="READY"
if (( fail > 0 )); then overall="NOT_READY"
elif (( warn > 0 )); then overall="READY_WITH_WARNINGS"
fi

if $JSON_MODE; then
  python3 - <<PY
import json
print(json.dumps({
  "overall": "$overall",
  "pass": $pass,
  "warn": $warn,
  "fail": $fail,
  "timer_enabled": "$timer_enabled",
  "timer_next": "$timer_next",
  "latest_output": "$(basename "${latest_dir:-}")",
  "checks": $(printf '%s\n' "${lines[@]}" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read().strip().split(chr(10))))"),
}, indent=2))
PY
else
  echo "Warren AI Readiness — $overall"
  echo "  OK: $pass  WARN: $warn  FAIL: $fail"
  echo
  for line in "${lines[@]}"; do echo "  $line"; done
  echo
  echo "Dry-run scheduler (no prompts):"
  echo "  cd \"$PROJECT_DIR\" && WARREN_SCHEDULE_DRY_RUN=true npm run scheduled-morning"
fi

exit $(( fail > 0 ? 1 : 0 ))
