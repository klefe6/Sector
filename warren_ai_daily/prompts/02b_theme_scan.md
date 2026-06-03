# Prompt 2b — Dedicated Theme Opportunity Scan and Weekly Theme Scoring

Today is {{DATE}}.

This is a separate, focused evaluation of user-defined investment themes.

**Important rules:**
- Themes are NOT GICS sectors. Do not confuse them.
- Do not change any ticker's classic sector label (XLK, XLC, XLE, etc.).
- A ticker may belong to multiple themes.
- Examples: META remains XLC, NVDA remains XLK, CEG remains XLU.
- Themes are research and context signals, not automatic buy signals.

---

## Context

### Market Regime (from Step 1)
{{STEP_01_OUTPUT}}

### Sector Scan Summary (from Step 2)
{{STEP_02_OUTPUT}}

{{RRG_MANDATORY_SECTORS}}

{{PRIOR_THEME_SCORES}}

---

## Theme Universe

{{THEME_UNIVERSE}}

---

## Instructions

For each theme listed in the Theme Universe above, produce:

**1. Weekly Theme Score** (integer, −100 to +100)

Score based on these components (each scored 0–100, where 0=worst, 50=neutral, 100=best):

| Component | Weight | What to assess |
|-----------|--------|----------------|
| Market Performance | 25% | 5d, 20d, 60d price strength of theme tickers vs SPY, QQQ |
| Breadth | 15% | How many theme tickers are above 20 EMA, 50 EMA, 200 SMA |
| Setup Quality | 20% | How many tickers have clean pullback or breakout setups (vs extended or falling knife) |
| News & Sentiment | 20% | Recent earnings tone, analyst commentary, capex news, institutional activity |
| Regulation & Policy | 10% | Recent or upcoming regulatory/policy help or harm |
| Valuation & Risk | 5% | Is the theme attractively priced or crowded/overvalued? |
| Catalysts | 5% | Near-term catalysts in the next 1–5 weeks |

Weighted final score = (each component × weight) scaled to −100 to +100.
Scores above 0 = net positive. Scores below 0 = net negative.

**2. Score Label**
- +70 to +100 → Very strong
- +35 to +69 → Improving
- −34 to +34 → Neutral or mixed
- −35 to −69 → Deteriorating
- −70 to −100 → Avoid or high risk

**3. Direction** (vs prior week score if provided)
- If prior score available: Improving, Stable, or Deteriorating (based on score change)
- If no prior score: New

**4. Candidate Scan** — for each theme ticker, evaluate using execution discipline:
- Liquidity (min 500K avg daily volume, min $15 price)
- Price above or near key moving averages
- Clean pullback or breakout setup (not extended, not falling knife)
- RSI not overbought (prefer < 72)
- Fair value upside available and positive
- Entry quality and risk/reward

---

## Required Output Format

Output one complete block per theme, following this EXACT format so it can be parsed:

---

## Theme: [THEME NAME]

**Weekly Score:** [integer, e.g. 72]
**Label:** [Very strong | Improving | Neutral or mixed | Deteriorating | Avoid or high risk]
**Direction:** [Improving | Stable | Deteriorating | New]
**Previous Score:** [integer or N/A]
**Score Change:** [+N or −N or N/A]

### Score Components

| Component | Score |
|-----------|-------|
| Market Performance | [0–100] |
| Breadth | [0–100] |
| Setup Quality | [0–100] |
| News Sentiment | [0–100] |
| Regulation Policy | [0–100] |
| Valuation Risk | [0–100] |
| Catalysts | [0–100] |

**Summary:** [1–2 sentences explaining the theme's overall condition]

**Positive Drivers:**
1. [driver]
2. [driver]

**Negative Drivers:**
1. [driver]
2. [driver]

### Candidate Scan

| Ticker | Sector ETF | Setup | Verdict | Rejection Reason | Entry | Stop | Target | R:R |
|--------|-----------|-------|---------|-----------------|-------|------|--------|-----|
| XXXX | XLK | Pullback | Pass | — | $000 | $000 | $000 | N:1 |
| YYYY | XLC | Extended | Reject | too_extended | — | — | — | — |

**Best Candidates:** [TICK1, TICK2, TICK3 or None]
**Avoid or Watch:** [TICK: reason, TICK: reason or None]

---

(Repeat the above block for every theme in the Theme Universe.)

After all themes, output a summary:

## Weekly Theme Score Summary

| Theme | Score | Label | Direction | Passed | Best Tickers |
|-------|-------|-------|-----------|--------|--------------|
| AI Infrastructure | 72 | Very strong | Improving | 3 | MSFT, NVDA, VRT |
| Semiconductors | 58 | Improving | Stable | 1 | NVDA |
| ... | | | | | |

**Top Improving Themes:** [list]
**Strong but Extended:** [list]
**High Score, No Clean Setup:** [list]
**Weak or Deteriorating:** [list]

This content is for research purposes only and not investment advice.
