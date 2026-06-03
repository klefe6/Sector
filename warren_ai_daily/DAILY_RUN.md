# Daily Run Guide — Warren AI Swing Trading Runner

This is the exact procedure to complete the 5-prompt Warren AI workflow every morning.

**Portfolio monitoring (Prompts 6 & 7)** is documented separately: see **[PORTFOLIO_MONITOR.md](./PORTFOLIO_MONITOR.md)** (`npm run portfolio-monitor`, holdings export, outputs, website API).

---

## Why CDP mode is required

`investing.com` uses Cloudflare bot protection which blocks Playwright's automated Chromium.
**You must use your own Chrome installation with remote debugging enabled.**
The runner attaches to it via CDP — it never controls the browser invisibly.

---

## Every morning: 4 steps

**Note:** Automation only runs **Tuesday through Saturday**. Sunday and Monday are disabled to respect market hours (US markets are closed Sunday, and Monday pre-market data is often incomplete).

### Step 1 — Open Chrome with remote debugging

Open a **new terminal** (keep it open while the run is in progress):

```bash
google-chrome --remote-debugging-port=9222 \
  --remote-allow-origins='*' \
  --user-data-dir="$HOME/.warren-ai-chrome-profile"
```

A Chrome window opens using a dedicated profile stored at `~/.warren-ai-chrome-profile`.
This profile keeps your InvestingPro login between sessions.

### Step 2 — Navigate to Warren AI and confirm login

In the Chrome window that just opened:

1. Go to: `https://www.investing.com/warrenai`
2. If the login screen appears, log in with your InvestingPro credentials
3. Wait until you can see the **"Ask WarrenAI"** input box

> **First time only**: You will need to log in. After that, the profile remembers your session
> and future runs will open directly to the chat.

### Step 3 — Check readiness (optional but recommended)

In your project terminal:

```bash
cd "/home/klefe6/Coding Projects/rrg/warren_ai_daily"
npm run morning
```

This checks:
- Whether Chrome CDP is reachable
- Whether the Warren AI tab is open
- Whether you are logged in and the chat input is visible
- Whether today's run already completed

If everything is ready, it prints the exact command to run next.
If something is missing, it prints exactly what to fix.

### Step 4 — Run the daily workflow

```bash
cd "/home/klefe6/Coding Projects/rrg/warren_ai_daily"
npm run run-daily:cdp
```

The runner will:
1. Connect to your Chrome via CDP
2. Find the Warren AI tab
3. Submit all 5 prompts in sequence (takes ~4–10 minutes depending on response length)
4. Save all outputs to `output/YYYY-MM-DD/`
5. Validate the outputs automatically
6. Leave the browser open so you can review the chat

### Combined run: new ideas + portfolio monitor (`npm run morning-full`)

`npm run morning-full` runs the same **CDP** workflow as `run-daily:cdp`, then (when you have holdings) the portfolio monitor. **Chrome must already be running with remote debugging** before you start it — the script does not launch Chrome for you.

1. In a **separate terminal**, start Chrome with debugging (same command as Step 1):

   ```bash
   google-chrome --remote-debugging-port=9222 \
     --remote-allow-origins='*' \
     --user-data-dir="$HOME/.warren-ai-chrome-profile"
   ```

2. In that Chrome window, open Warren AI, **log in**, complete any **security / Cloudflare** steps, and confirm the **Ask WarrenAI** input is visible.

3. Then run:

   ```bash
   npm run morning-full
   ```

If CDP is not reachable, the command prints a boxed explanation, the **exact** launch line above, and whether the debugging **port looks closed** vs **open but not a CDP Chrome**. A short **“Last failure: Chrome CDP not reachable”** line may also appear under **Automation Health** on the Decision Cockpit until a full `morning-full` run completes successfully.

Use `npm run morning-full:dry` to preview what would run without sending prompts.

---

## If a run is interrupted

If the run fails partway (network drop, Chrome crash, etc.), do not start from scratch.
Use resume mode — it skips steps whose output files already exist:

```bash
npm run run-daily:resume
```

> Resume mode also uses CDP mode as set in `.env` (`WARREN_BROWSER_MODE=cdp`).

---

## Output files

Every successful run creates:

```
output/2026-05-04/
├── step_01.md              Step 1: Market Regime and Sector Readiness
├── step_02.md              Step 2: Top 4 Sectors and 16 Ticker Candidates
├── step_03.md              Step 3: Full Technical + Fundamental Table
├── step_04.md              Step 4: Execution Plan and Ranking
├── step_05.md              Step 5: Final Morning Decision Report
├── final_action_plan.md    Same as step_05.md — easy to find
├── run_summary.json        Metadata: timings, step status, validation result
└── validation_warnings.md  ONLY appears if validation found issues (not a normal file)
```

**Start with `final_action_plan.md`** — it has today's trade decisions.

---

## Validation

After every run, the runner automatically validates:

- All 5 step files exist and are non-empty
- `run_summary.json` reports `success: true`
- `final_action_plan.md` is ≥800 characters
- `final_action_plan.md` contains a trade/ticker section
- `final_action_plan.md` contains stop/risk management
- `final_action_plan.md` contains market regime context
- Step 2 contains ≥16 ticker symbols

If all checks pass: no `validation_warnings.md` is created.
If any check fails: `validation_warnings.md` is written with specific items to review.
**Outputs are never deleted by validation.**

---

## Confirmed working selectors (as of 2026-05-04)

| Element | Selector |
|---------|----------|
| Chat input | `[data-test="chat-input-field"]` |
| Send button | `[data-test="send-message-button"]` |
| AI response | `[data-test="chat-answers"]` |
| Generation done | `[data-test="answer-buttons-block"]` |

If Warren AI updates its UI and the run breaks, run `npm run inspect-page` to get a fresh DOM dump and update `src/warren_client.ts` `SELECTORS`.

---

## Configuration (`.env`)

```
WARREN_AI_URL=https://www.investing.com/warrenai
WARREN_BROWSER_MODE=cdp
CHROME_CDP_URL=http://127.0.0.1:9222
HEADLESS=false
KEEP_BROWSER_OPEN=true
RESUME=false
DRY_RUN=false
MAX_WAIT_MS=240000
STABLE_RESPONSE_MS=10000
POLL_MS=1500
MIN_RESPONSE_CHARS=500
LOGIN_CHECK_TIMEOUT_MS=300000
```

---

## Scheduled 7 AM Run (systemd user timer)

The scheduler automates the daily run at 7:00 AM on weekdays.

**Important manual requirement:** Chrome with CDP must already be running and you must
be logged into Warren AI before 7 AM. The scheduler only automates the prompts —
it cannot bypass Cloudflare or log in for you.

### What must be open overnight

Leave these running before you go to bed or before 7 AM:

1. **Chrome with CDP enabled:**
   ```bash
   google-chrome --remote-debugging-port=9222 \
     --remote-allow-origins='*' \
     --user-data-dir="$HOME/.warren-ai-chrome-profile"
   ```
2. **Warren AI tab open:** `https://www.investing.com/warrenai` — logged in and visible.

The scheduler will check both before doing anything. If either is missing, it writes a
failure log and exits without submitting any prompts.

### Install the schedule

```bash
cd "/home/klefe6/Coding Projects/rrg/warren_ai_daily"
npm run install-schedule
```

This copies `warren-ai-daily.service` and `warren-ai-daily.timer` into
`~/.config/systemd/user/`, enables the timer, and shows when it will next fire.

### Check schedule status

```bash
npm run check-schedule
```

Prints: timer active/inactive, next fire times, today's run result, last scheduler log,
and recent journal entries.

To check via systemd directly:
```bash
systemctl --user status warren-ai-daily.timer
systemctl --user list-timers warren-ai-daily.timer
journalctl --user -u warren-ai-daily --since today
```

### Disable / remove the schedule

```bash
npm run uninstall-schedule
```

### How the scheduler protects against duplicate runs

`src/scheduled_morning.ts` always checks before running:

| Situation | Scheduler action |
|-----------|-----------------|
| **Sunday or Monday** | Exits cleanly (automation disabled on weekends) |
| Today's `run_summary.json` says `success: true` | Exits cleanly, no prompts submitted |
| Partial run (some step files exist, no success) | Uses **resume mode** to continue |
| No run started today | Runs the full 5-step workflow |
| Force override needed | Set `WARREN_FORCE_RERUN=true` in `.env` |

**Trading days:** The automation only runs **Tuesday through Saturday** to align with US market hours and avoid incomplete Monday pre-market data.

### Dry-run test (non-destructive)

Run this any time to verify readiness without actually submitting prompts:

```bash
npm run scheduled-morning:dry
```

It prints all readiness check results and the action it *would* take, then exits.

### Scheduler logs

Every scheduled invocation writes a log to:

```
output/scheduler_logs/YYYY-MM-DD.log
```

The log includes: timestamp, readiness results, command attempted, success/failure, and
the output folder path.

### Full scheduler command reference

| Command | What it does |
|---------|--------------|
| `npm run scheduled-morning` | Run scheduler logic now (live) |
| `npm run scheduled-morning:dry` | Dry run — readiness checks only, no prompts |
| `npm run install-schedule` | Install systemd user timer for 7 AM weekdays |
| `npm run check-schedule` | Check timer status, today's run, last log |
| `npm run uninstall-schedule` | Remove systemd timer (does not delete outputs) |

---

## Combined morning workflow (recommended)

`npm run morning-full` runs **both** workflows in one command:

1. **Export portfolio** from `positions.db` → `config/current_portfolio.json`
2. **New ideas** (prompts 1–5) — skipped if already complete today
3. **Portfolio monitor** (prompts 6–7) — skipped if no holdings or already complete today

After both workflows, it writes:
- `output/YYYY-MM-DD/full_morning_summary.json`
- `output/YYYY-MM-DD/full_morning_summary.md`

### Dry-run first (safe):

```bash
cd /home/klefe6/Coding\ Projects/rrg/warren_ai_daily
npm run morning-full:dry
```

Prints exactly what would run/skip. **Sends nothing.**

### Live run:

```bash
npm run morning-full
```

### What gets skipped

| Condition | What happens |
|-----------|--------------|
| New ideas complete today | Step 2 skipped |
| No active holdings in DB | Portfolio monitor skipped |
| Portfolio monitor complete today | Step 3 skipped |
| CDP not reachable | Both blocked, summary written with warnings |

### Force flags

| Variable | Effect |
|----------|--------|
| `MORNING_FORCE_IDEAS=true` | Re-run new ideas even if complete |
| `MORNING_FORCE_PORTFOLIO=true` | Re-run portfolio monitor even if complete |

---

## Scheduler — current state

> **The 7 AM timer currently runs `scheduled-morning` (new ideas only, prompts 1–5).**
> It does **not** run the portfolio monitor or `morning-full`.

To switch to `morning-full` later, edit the service file:

```bash
nano ~/.config/systemd/user/warren-ai-daily.service
```

Change the `ExecStart` line from:
```
ExecStart=... npm run scheduled-morning
```
to:
```
ExecStart=... npm run morning-full
```

Then reload:
```bash
systemctl --user daemon-reload
systemctl --user restart warren-ai-daily.timer
```

⚠ Only switch when portfolio monitoring is fully tested and you want it to run unattended at 7 AM.

---

## All available commands

| Command | What it does |
|---------|--------------|
| `npm run morning` | Readiness check — CDP, tab, login, prior run status |
| `npm run morning-full` | Run new ideas + portfolio monitor (skip if complete) |
| `npm run morning-full:dry` | Preview what morning-full would do without sending anything |
| `npm run run-daily:cdp` | Full 5-step new ideas run using CDP mode |
| `npm run run-daily:resume` | Resume from the last incomplete step |
| `npm run export-portfolio` | Export active positions from DB to config JSON |
| `npm run portfolio-monitor` | Send prompts 6 & 7 for portfolio monitoring |
| `npm run portfolio-monitor:dry` | Preview portfolio prompts without sending |
| `npm run scheduled-morning` | Scheduled runner (duplicate-safe, logging, resume) |
| `npm run scheduled-morning:dry` | Dry-run readiness check for scheduled runner |
| `npm run install-schedule` | Install 7 AM systemd timer |
| `npm run check-schedule` | Status of timer + today's run + last log |
| `npm run uninstall-schedule` | Remove the timer |
| `npm run preflight` | Detailed CDP check + writes `preflight_latest.json` |
| `npm run inspect-page` | Dumps Warren AI DOM for debugging selectors |
| `npm run dry-run` | Print all 5 resolved prompts, no browser |
| `npm run build` | TypeScript compile check |

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `CANNOT CONNECT TO CHROME VIA CDP` | Launch Chrome with `--remote-debugging-port=9222` and `--remote-allow-origins='*'` (Chrome 111+), then fully quit and relaunch |
| Chat input not found | Navigate to Warren AI and log in in the Chrome window |
| `Assessing the Metrics…` captured as step output | Loading placeholder — update `STABLE_RESPONSE_MS=15000` |
| Step 2 has <16 tickers | Check `validation_warnings.md`; re-run that step via resume |
| Run fails at step 3 or 4 | Run `npm run run-daily:resume` — steps 1–2 will be skipped |
| `validation_warnings.md` created | Read it — it lists exactly what to review |
