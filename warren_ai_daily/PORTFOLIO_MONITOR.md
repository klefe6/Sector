# Portfolio monitor (Warren AI Prompts 6 & 7)

Separate from the **5-step new opportunity** workflow (`npm run run-daily:cdp`). Uses the same **CDP** Chrome session and selectors; does not change prompt files 01–05.

## Where holdings come from

1. **Primary:** SQLite `positions.db` in the repo root (Decision Cockpit). Statuses **`in_position`** and **`trimming_zone`** only.
2. **Sync:** `npm run export-portfolio` runs `scripts/export_portfolio_from_db.py` and writes `config/current_portfolio.json`.
3. **Manual:** Edit `config/current_portfolio.json` (see `config/README.md`).

`npm run portfolio-monitor` runs the export first (unless `PORTFOLIO_SKIP_EXPORT=true`).

## Commands

| Command | Purpose |
|---------|---------|
| `npm run morning-full` | **Recommended** — run new ideas + portfolio monitor (skips if done) |
| `npm run morning-full:dry` | Preview what morning-full would do; no prompts sent |
| `npm run export-portfolio` | Refresh `config/current_portfolio.json` from `positions.db` |
| `npm run portfolio-monitor` | CDP: send Prompt 6 then 7; save under `output/YYYY-MM-DD/` |
| `npm run portfolio-monitor:dry` | Print resolved prompts only; no browser |

## Environment

| Variable | Meaning |
|----------|---------|
| `PORTFOLIO_DRY_RUN=true` | No Warren submission |
| `PORTFOLIO_FORCE=true` | Ignore duplicate protection for today |
| `PORTFOLIO_SKIP_EXPORT=true` | Do not run Python export before monitor |
| `MORNING_FORCE_PORTFOLIO=true` | Force portfolio rerun inside morning-full |

## Outputs (per day)

- `output/YYYY-MM-DD/portfolio_step_06.md` — Prompt 6 response
- `output/YYYY-MM-DD/portfolio_step_07.md` — Prompt 7 response
- `output/YYYY-MM-DD/portfolio_summary.json` — metadata, positionsUsed, success flag
- `output/YYYY-MM-DD/full_morning_summary.json` — combined summary (written by `morning-full`)
- `output/YYYY-MM-DD/full_morning_summary.md` — combined summary markdown

If both step files exist **and** `portfolio_summary.json` has `"success": true`, a rerun is skipped
unless `PORTFOLIO_FORCE=true`. If step 6 exists and step 7 is missing, step 6 is **not** resent; only step 7 runs.

## Website

- **Selector tab → "Morning Status" banner** — freshness badges for both workflows.
- **Selector tab → "Current Portfolio Thesis Monitor"** panel — holdings needing attention.
- **API:** `GET /api/warren-ai/portfolio/latest` — portfolio monitor JSON.
- **API:** `GET /api/warren-ai/morning/latest` — combined morning status JSON.

## If no holdings are found

The runner exits **0** with a short message. Add positions in the Cockpit or fill
`config/current_portfolio.json`, then run `npm run export-portfolio` or `npm run portfolio-monitor` again.

## Morning order (recommended)

```bash
# One command for everything:
npm run morning-full

# OR individually:
npm run export-portfolio
npm run run-daily:cdp       # new ideas (prompts 1-5)
npm run portfolio-monitor   # portfolio (prompts 6-7)
```
