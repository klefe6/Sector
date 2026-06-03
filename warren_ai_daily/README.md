# Warren AI Daily Runner

Playwright automation that runs 5 sequential swing trading prompts through Warren AI each morning, captures each response, feeds prior answers into later prompts automatically, and saves a final action plan report.

---

## What It Does

1. **Step 1 — Market Regime**: Assesses SPY trend, breadth, VIX, and yield curve.
2. **Step 2 — Sector Tickers**: Picks top 4 sectors and 4 candidates each → 16 tickers.
3. **Step 3 — Research Table**: Full technical + fundamental + valuation table for all 16.
4. **Step 4 — Execution Plan**: Filters and ranks. Entry, stop, target, and R:R for top 8.
5. **Step 5 — Final Report**: Synthesizes everything into a morning action plan.

Each response is saved as `output/YYYY-MM-DD/step_0N.md`. The final report is also saved as `final_action_plan.md`.

---

## Prerequisites

- **Node.js 18+** — https://nodejs.org
- **npm 9+** (included with Node.js)

---

## First-Time Setup

### 1. Install dependencies

```bash
cd warren_ai_daily
npm install
npm run install-browsers
```

### 2. Create your `.env` file

```bash
cp .env.example .env
```

Open `.env` and set at minimum:

```
WARREN_AI_URL=https://warren.ai   # The exact URL of the Warren AI chat page
```

### 3. Run preflight to verify your setup

```bash
npm run preflight
```

This opens the browser, navigates to Warren AI, checks if the chat input is visible, writes `output/preflight_latest.json`, and tells you exactly what to do next.

### 4. Log in manually (one time only)

If the preflight reports "Login required", run:

```bash
npm run run-daily:visible
```

When the browser opens, log in to Warren AI. After logging in, the runner will proceed automatically. The session is saved in `./browser_profile/` and reused on all future runs.

---

## Daily Use

### Full 5-step run (default)

```bash
npm run run-daily
```

### Run with the browser window visible (useful for debugging)

```bash
npm run run-daily:visible
```

Runs headless=false and leaves the browser open so you can inspect the final state.

### Resume a partially completed run

If a run failed partway through, resume from where it left off:

```bash
npm run run-daily:resume
```

Steps that already have a `step_0N.md` for today are skipped. Prior outputs are loaded and used as placeholders in later prompts.

### Connect to your own Chrome (CDP mode)

CDP mode attaches to a running Chrome instance. Useful if you already have Warren AI open and logged in.

**Step 1 — Launch Chrome with remote debugging enabled:**

Linux:
```bash
google-chrome --remote-debugging-port=9222 --remote-allow-origins='*' --user-data-dir="$HOME/.warren-ai-chrome-profile"
```

Windows PowerShell:
```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --remote-allow-origins='*' --user-data-dir="$env:USERPROFILE\.warren-ai-chrome-profile"
```

**Step 2 — Run the daily script in CDP mode:**

```bash
npm run run-daily:cdp
```

This connects to `http://127.0.0.1:9222` by default (configurable via `CHROME_CDP_URL`).

### Test without submitting (dry-run)

```bash
npm run dry-run
```

Prints all 5 fully-resolved prompts to the terminal. No browser required.

---

## NPM Scripts Reference

| Command | What it does |
|---------|--------------|
| `npm run preflight` | Check if chat input is reachable; write `preflight_latest.json` |
| `npm run dry-run` | Print all prompts, no browser |
| `npm run run-daily` | Full 5-step run, persistent browser mode |
| `npm run run-daily:resume` | Skip already-completed steps for today |
| `npm run run-daily:cdp` | Connect to existing Chrome via CDP |
| `npm run run-daily:visible` | Headless=false, keep browser open after run |
| `npm run build` | TypeScript compile check (no output files) |
| `npm run install-browsers` | Install Playwright's Chromium |

---

## Output Structure

```
output/
├── 2026-05-04/
│   ├── step_01.md              step 1 response
│   ├── step_02.md              step 2 response
│   ├── step_03.md              step 3 response
│   ├── step_04.md              step 4 response
│   ├── step_05.md              step 5 response
│   ├── final_action_plan.md    copy of step 5 for easy access
│   ├── run_summary.json        metadata, timings, step status
│   └── validation_warnings.md  (only if validation found issues)
└── preflight_latest.json       most recent preflight result
```

If a run fails:
```
output/2026-05-04/
├── error_step3.png     screenshot at time of failure
└── error_step3.html    full HTML snapshot at time of failure
```

---

## Resume Mode

If your run fails at step 3 and steps 1–2 already completed, do not start from scratch:

```bash
npm run run-daily:resume
```

The runner:
- Looks for `step_01.md`, `step_02.md` etc. in today's output folder
- Skips any step whose file already exists
- Loads the saved text and uses it for placeholder substitution in later prompts
- Runs only the missing steps

---

## Validation

After step 5 completes, the runner automatically validates:

- **Step 2** contains at least 16 ticker symbols
- **Final report** contains expected sections (best trade, backup, sector, caution reason)

If validation finds issues, `validation_warnings.md` is written. **Outputs are never deleted** — validation is informational only.

---

## Response Completion Detection

The runner uses a text-stability window to decide when Warren AI has finished generating:

1. Submit prompt
2. Wait for a new response to appear (different from the previous one)
3. Poll every `POLL_MS` milliseconds
4. When the text has not changed for `STABLE_RESPONSE_MS` milliseconds AND the stop button is gone → response is complete
5. If `MAX_WAIT_MS` is reached, returns whatever partial text exists with a warning

Each suspicious response (shorter than `MIN_RESPONSE_CHARS`) is flagged in `run_summary.json` but does not stop the run.

---

## Editing Prompts

All prompt templates live in `prompts/`. Edit them freely — they are plain markdown.

| File | Purpose |
|------|---------|
| `01_market_regime.md` | Prompt 1 — regime and sector assessment |
| `02_sector_tickers.md` | Prompt 2 — 4 sectors × 4 tickers |
| `03_research_table.md` | Prompt 3 — full research table |
| `04_execution_plan.md` | Prompt 4 — ranked execution plan |
| `05_final_report.md` | Prompt 5 — final morning decision report |

### Placeholders

| Token | Replaced with |
|-------|--------------|
| `{{DATE}}` | Today's date (YYYY-MM-DD) |
| `{{TIME}}` | Current time (HH:MM:SS) |
| `{{STEP_01_OUTPUT}}` | Full text of step 1 response |
| `{{STEP_02_OUTPUT}}` | Full text of step 2 response |
| `{{STEP_03_OUTPUT}}` | Full text of step 3 response |
| `{{STEP_04_OUTPUT}}` | Full text of step 4 response |
| `[PASTE THE 16 TICKERS FROM PROMPT 2]` | Ticker list extracted from step 2 |
| `[PASTE TABLE FROM PROMPT 3]` | Full step 3 response |
| `[PASTE OUTPUTS FROM PROMPTS 1 TO 4]` | All four prior outputs concatenated |

---

## Selectors

The automation tries multiple CSS selector candidates for each UI element. If Warren AI updates its UI, edit the `SELECTORS` object at the top of `src/warren_client.ts`.

Every selector attempt is logged:

```
[selector] ✓ textarea[placeholder*="message" i]
[selector] ✗ None matched:
  button[aria-label*="send" i]
  …
```

---

## Bot Detection

If Warren AI triggers a captcha or blocks automation, the runner prints a clear error and stops. It never tries to bypass bot detection. Open the browser manually, resolve the challenge, then re-run.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Missing required environment variable: WARREN_AI_URL` | Create `.env` from `.env.example` and set the URL |
| Login screen appears every run | Session expired — run `run-daily:visible` to log in again |
| `Cannot connect to Chrome via CDP` | Launch Chrome with `--remote-debugging-port=9222` and `--remote-allow-origins='*'` (Chrome 111+), then fully quit and relaunch |
| Response times out | Increase `MAX_WAIT_MS` in `.env` |
| Response captured too early | Increase `STABLE_RESPONSE_MS` (try 12000) |
| Wrong text captured | Update `SELECTORS.assistantMessage` in `warren_client.ts` |
| `error_stepN.png` in output | Open it to see page state at time of failure |
| Dry-run works but live run fails | Log in once with `run-daily:visible` to save session |

---

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `WARREN_AI_URL` | *(required)* | URL of Warren AI chat |
| `WARREN_BROWSER_MODE` | `persistent` | `persistent` or `cdp` |
| `CHROME_CDP_URL` | `http://127.0.0.1:9222` | CDP endpoint (cdp mode only) |
| `HEADLESS` | `false` | Run without visible browser window |
| `KEEP_BROWSER_OPEN` | `false` | Leave browser open after run |
| `RESUME` | `false` | Skip steps with existing output files |
| `DRY_RUN` | `false` | Print prompts only, no browser |
| `MAX_WAIT_MS` | `180000` | Max ms to wait for one AI response |
| `STABLE_RESPONSE_MS` | `8000` | Ms of no-change to declare response done |
| `POLL_MS` | `1000` | Response polling interval |
| `LOGIN_CHECK_TIMEOUT_MS` | `120000` | Max ms to wait for manual login |
| `MIN_RESPONSE_CHARS` | `500` | Flag response as suspicious if shorter |
| `BROWSER_PROFILE_DIR` | `./browser_profile` | Persistent session storage |
| `PROMPTS_DIR` | `./prompts` | Prompt markdown files |
| `OUTPUT_DIR` | `./output` | Daily output root |

---

## Scheduling

### Linux — cron (headless mode)

> **Prerequisite**: Log in once with `run-daily:visible` and verify `HEADLESS=true` works.

```bash
crontab -e
```

```
30 8 * * 1-5 cd /path/to/warren_ai_daily && npm run run-daily >> output/cron.log 2>&1
```

### Linux — systemd timer

`/etc/systemd/system/warren-daily.service`:
```ini
[Unit]
Description=Warren AI Daily Runner

[Service]
Type=oneshot
WorkingDirectory=/path/to/warren_ai_daily
ExecStart=/usr/bin/npm run run-daily
User=youruser
```

`/etc/systemd/system/warren-daily.timer`:
```ini
[Unit]
Description=Warren AI daily at 8:30 AM on weekdays

[Timer]
OnCalendar=Mon-Fri 08:30:00
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now warren-daily.timer
```

### Windows — Task Scheduler

1. Task Scheduler → Create Basic Task
2. Trigger: Daily 8:30 AM, Mon–Fri
3. Action: `C:\Program Files\nodejs\npm.cmd` `run run-daily` in `C:\path\to\warren_ai_daily`
4. Set `HEADLESS=true` after saving your session

---

## Project Structure

```
warren_ai_daily/
├── prompts/
│   ├── 01_market_regime.md
│   ├── 02_sector_tickers.md
│   ├── 03_research_table.md
│   ├── 04_execution_plan.md
│   └── 05_final_report.md
├── src/
│   ├── config.ts           environment loading + validation
│   ├── prompt_loader.ts    load + resolve prompts with placeholders
│   ├── warren_client.ts    Playwright browser automation
│   ├── report_writer.ts    save step outputs + run_summary.json
│   ├── validator.ts        post-run output validation
│   ├── preflight.ts        pre-run readiness check
│   └── run_daily.ts        main orchestrator
├── output/                 created at runtime (gitignored)
├── browser_profile/        persistent Chromium session (gitignored)
├── .env                    your config (gitignored — never commit)
├── .env.example            template — safe to commit
├── .gitignore
├── package.json
├── tsconfig.json
└── README.md
```

---

## Important Notes

- `browser_profile/` and `output/` are gitignored and should never be committed.
- `.env` is gitignored. Only `.env.example` is committed.
- The runner never attempts to bypass bot detection — it stops and reports.
- There are no AI or LLM calls inside the runner. It only sends your prompts and saves responses.
