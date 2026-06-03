# Prompt 3 — Full Technical, Fundamental, and Valuation Table

Today is {{DATE}}.

Build a complete research table for the 16 swing trading candidates listed below.

---

## Ticker List (from Step 2)

[PASTE THE 16 TICKERS FROM PROMPT 2]

---

## Instructions

For each ticker, research and compile all of the following fields. If a data point is unavailable, write N/A — do not guess.

### Technical Fields (as of today's pre-market or last close)
- Current Price
- 20 EMA — is price above or below? % distance
- 50 EMA — is price above or below? % distance
- 200 EMA — is price above or below?
- RSI (14-day)
- ATR (14-day, as % of price)
- 30-day return %
- Relative strength vs SPY (30-day return minus SPY 30-day return)
- Technical setup label: one of: Pullback to EMA, Breakout, Consolidation, Extended, Falling knife

### Fundamental Fields
- Market cap (B)
- Revenue growth (YoY last quarter)
- EPS trend (accelerating / decelerating / flat)
- Sector ETF it belongs to

### Valuation Fields (use InvestingPro or consensus estimates)
- Fair value estimate (if available)
- Fair value upside % vs current price
- P/E or forward P/E
- Analyst consensus: Strong Buy / Buy / Hold / Sell

## Output Format

Output a single markdown table with one row per ticker.
After the table, add a brief 1–2 sentence note on any ticker that has an unusually notable setup or risk.

| Ticker | Price | vs 20EMA | vs 50EMA | vs 200EMA | RSI | ATR% | 30d Ret | RS vs SPY | Setup | Mkt Cap | Rev Gro | EPS | Fair Value | FV Upside | P/E | Consensus |
|--------|-------|----------|----------|-----------|-----|------|---------|-----------|-------|---------|---------|-----|------------|-----------|-----|-----------|
