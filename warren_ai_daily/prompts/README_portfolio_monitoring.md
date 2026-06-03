# Portfolio monitoring prompts (Prompts 6 & 7)

These templates are **stored only** for a future workflow. They are **not** loaded by `run_daily.ts` or `prompt_loader.ts` yet.

| File | Role |
|------|------|
| `portfolio_news_thesis_monitor.md` | **Prompt 6** — overnight / recent changes vs thesis for current holdings only. |
| `portfolio_action_plan.md` | **Prompt 7** — action table from the monitoring report. |

## Injection order (when wired)

1. **Prompt 6** requires **`{{CURRENT_PORTFOLIO}}`** to be replaced with live portfolio holdings from your source of truth (e.g. Decision Cockpit SQLite / exported list) before the prompt is sent to Warren AI.
2. **Prompt 7** requires **`{{PORTFOLIO_MONITOR_REPORT}}`** to be replaced with the **full assistant output** from Prompt 6 before sending.

Do not send Prompt 7 until Prompt 6’s response is captured. The existing 5-step opportunity workflow is unchanged until you explicitly extend the runner.
