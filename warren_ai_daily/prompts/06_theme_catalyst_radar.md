# Prompt 6 — Theme Catalyst Radar (Research Only)

Today is {{DATE}}.

This is a **research and catalyst discovery** prompt for the AtlasInvestor Theme Catalyst Radar.
It is separate from the daily sector scan and execution workflow.

**Hard rules:**
- Do NOT recommend buys, sells, or position sizes.
- Do NOT output entry, stop, or target prices.
- Do NOT rank stocks for immediate trading.
- Use Warren AI only as a research source for upcoming themes, sympathy baskets, and Wall Street attention.
- Public proxy stocks are indirect sympathy plays — never treat them as the private company itself.

---

## Context

### Known manual catalysts (may be incomplete — add or enrich)
{{MANUAL_CATALYSTS_BLOCK}}

### User theme registry (for overlap awareness)
{{THEME_UNIVERSE}}

---

## Questions

Answer all of the following:

1. What upcoming market catalysts could create sector or theme interest over the next **2 to 4 weeks**?
2. Are there IPOs, S-1 filings, roadshows, pricing dates, listing dates, government contracts, regulatory events, or earnings clusters worth tracking?
3. For **private company catalysts** (e.g. SpaceX), what **public proxy stocks and ETFs** could react sympathetically?
4. Which themes are gaining **Wall Street attention** right now?
5. Which proxy baskets appear to be showing **relative strength** already?
6. Which themes are **early watch only** and should **not** be chased yet?
7. What would **confirm** or **invalidate** each theme?

---

## Required output

Respond with **only valid JSON** (no markdown prose outside the JSON block).
Use this exact top-level shape:

```json
{
  "generated_at": "YYYY-MM-DDTHH:MM:SSZ",
  "source": "warren_ai",
  "catalysts": [
    {
      "theme": "space",
      "event_type": "IPO",
      "event_date": "2026-06-12",
      "confidence": "medium",
      "source_freshness": "warren_ai",
      "why_it_matters": "One sentence on market impact.",
      "private_company": "SpaceX",
      "public_proxy_tickers": ["RKLB", "ASTS"],
      "proxy_etfs": ["ARKX", "UFO"],
      "wall_street_interest_summary": "Brief note on analyst/media attention.",
      "confirmation_triggers": ["At least 3 proxies beat SPY with volume"],
      "invalidation_triggers": ["Proxy basket fades below SPY for 5 days"],
      "risk_notes": ["Sympathy trade risk — not direct exposure"],
      "suggested_watch_status": "early_watch"
    }
  ]
}
```

Field notes:
- `event_type`: one of IPO, S1 filing, roadshow, pricing date, listing date, major contract, government funding, earnings cluster, regulatory decision, product launch, geopolitical event, sector wide policy event
- `confidence`: low | medium | high
- `suggested_watch_status`: early_watch | confirmed | crowded | fading
- `event_date`: YYYY-MM-DD or null if unknown
- Include **all** catalysts you identify, not only those in the manual list

This content is for research purposes only and not investment advice.
