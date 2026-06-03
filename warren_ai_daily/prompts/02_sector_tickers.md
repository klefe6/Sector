# Prompt 2 — Top 4 Sectors and 16 Ticker Candidate List

Today is {{DATE}}.

Based on the market regime and sector rotation analysis below, generate a candidate list of 16 tickers — 4 per sector — for today's swing trading research session.

---

## Market Regime Context (from Step 1)

{{STEP_01_OUTPUT}}

---

{{RRG_MANDATORY_SECTORS}}

## Instructions

For each sector in the final scan list (your step 01 selections PLUS any RRG mandatory sectors above), select exactly 4 ticker candidates.

Selection criteria:
- Stocks with meaningful options liquidity and average daily volume above 500K shares
- Any market capitalization is acceptable — large-cap and mega-cap sector leaders are welcome when they have a qualifying technical setup
- Within the sector but with their own catalyst or momentum story
- Mix of: 1–2 near a clean technical setup + 1–2 showing early breakout
- No penny stocks (minimum price $15)
- Prefer names I can research on InvestingPro for fair value and financial health
- If Technology (XLK) is a top sector, include large-cap names such as NVDA, AVGO, MSFT, AMD, PANW, CRWD, AMAT, MU, QCOM, CRM, ADBE, NOW alongside any smaller setups

## Output Format

For each sector, output exactly this structure:

### Sector: [ETF Ticker] — [Sector Name]
**Why this sector today:** [one sentence]

| # | Ticker | Company | Why It's a Candidate |
|---|--------|---------|----------------------|
| 1 | XXXX   | Name    | Short reason         |
| 2 | XXXX   | Name    | Short reason         |
| 3 | XXXX   | Name    | Short reason         |
| 4 | XXXX   | Name    | Short reason         |

---

End with a flat list of all 16 tickers for easy copy-paste:

**All 16 Tickers:**
TICK1, TICK2, TICK3, TICK4, TICK5, TICK6, TICK7, TICK8, TICK9, TICK10, TICK11, TICK12, TICK13, TICK14, TICK15, TICK16

{{THEME_UNIVERSE}}
