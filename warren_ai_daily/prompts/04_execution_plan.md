# Prompt 4 — Execution Plan and Ranking

Today is {{DATE}}.

Using the research table from Step 3, build a prioritized execution plan for today's swing trading session.

---

## Research Table (from Step 3)

[PASTE TABLE FROM PROMPT 3]

---

## Instructions

### Part A: Filter and Rank

Apply the following filter to remove names that are not ready:
- Remove if RSI > 72 (too extended)
- Remove if price is more than 12% above its 50 EMA (too extended)
- Remove if setup label is "Extended" or "Falling knife"
- Remove if fair value upside is negative (overvalued by InvestingPro)

From the remaining names, rank the top 8 from strongest to weakest entry opportunity.

Ranking factors (weight in order):
1. Technical setup quality (clean pullback to EMA > breakout > consolidation)
2. RSI position (ideal: 35–60)
3. Relative strength vs SPY and vs sector ETF
4. Fair value upside (more upside = better)
5. Conviction (A/B from my watchlist if available)

### Part B: Execution Details for Top 8

For each of the top 8 ranked names, provide:

| Rank | Ticker | Verdict | Entry Zone | Stop Loss | Target | Risk:Reward | Position Size Note | Key Risk |
|------|--------|---------|------------|-----------|--------|-------------|---------------------|----------|

- **Entry Zone**: price level or EMA touch that confirms the entry
- **Stop Loss**: logical level (recent swing low, below EMA, ATR-based)
- **Target**: first target (resistance, prior high, or fair value)
- **Risk:Reward**: must be at least 2:1 to qualify
- **Position Size Note**: Full / Half / Quarter based on setup clarity and regime
- **Key Risk**: the main thing that could invalidate this trade

### Part C: Names to Watch Tomorrow

List up to 4 names that did not qualify today but are worth monitoring if they set up.

### Part D: Names to Remove from Research

List any names that should be removed from the watchlist entirely and why (broken chart, fundamental deterioration, etc.).
