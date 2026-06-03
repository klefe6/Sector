# Portfolio JSON for Warren AI (Prompts 6 & 7)

## Default source

`npm run export-portfolio` runs `scripts/export_portfolio_from_db.py`, which reads **active positions** from the Decision Cockpit SQLite database (`../../positions.db`): statuses `in_position` and `trimming_zone` only.

## Manual edit

You may edit `current_portfolio.json` directly. Shape:

```json
{
  "updated_at": "2026-05-05",
  "source": "manual",
  "positions": [
    {
      "ticker": "AAPL",
      "company": "Apple Inc.",
      "side": "Long",
      "quantity": null,
      "avg_cost": null,
      "sector": null,
      "thesis": "",
      "notes": ""
    }
  ]
}
```

Leave `positions` as `[]` if you have no holdings to monitor.
