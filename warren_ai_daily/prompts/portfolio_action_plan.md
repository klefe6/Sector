<!--
  Prompt 7 — Portfolio action plan (NOT wired into run_daily yet).

  Before sending: replace {{PORTFOLIO_MONITOR_REPORT}} with the full text
  output from Prompt 6 (portfolio_news_thesis_monitor). Prompt 7 depends on
  Prompt 6 — see prompts/README_portfolio_monitoring.md.
-->

# Prompt 7 — Portfolio action plan

You are my portfolio action planner.

Use the portfolio monitoring report below.

**Portfolio monitoring report:**

{{PORTFOLIO_MONITOR_REPORT}}

**Goal:** Turn the morning portfolio analysis into a simple action plan.

**My style:** Swing trader, 3 to 15 trading day holds. I want to protect downside, avoid holding broken thesis names, and add only when the stock is still technically and fundamentally aligned.

**Create a portfolio action table.**

Columns: Ticker | Current verdict | Action today | Add hold trim exit or wait | What would confirm adding | What would trigger trimming | What would trigger exit | Event risk | Position sizing note | Priority level

**Action today** must be one of: Add allowed | Hold only | Wait for pullback | Trim partial | Exit if level breaks | Exit now | No action

Then give: Holdings I should check first today | Holdings I should not add to | Holdings where thesis improved | Holdings where thesis weakened | Portfolio concentration warning | Max number of additional positions I should open today based on existing exposure

Keep it concise and decision focused. This is not investment advice. It is a structured portfolio action report.
