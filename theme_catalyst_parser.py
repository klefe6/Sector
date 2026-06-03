"""
Parse Warren AI Theme Catalyst Radar response into structured JSON.

Reads theme_catalyst_radar.md (or raw text), extracts JSON, writes:
  warren_ai_daily/output/theme_catalyst_radar_latest.json
  warren_ai_daily/output/YYYY_MM_DD_theme_catalyst_radar.json
  warren_ai_daily/output/YYYY-MM-DD/theme_catalyst_radar.json
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
WARREN_OUTPUT = os.path.join(_HERE, "warren_ai_daily", "output")


def _extract_json(text: str) -> dict | None:
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for pattern in (
        r"```json\s*(\{.*?\})\s*```",
        r"```\s*(\{.*?\})\s*```",
        r"(\{[\s\S]*\"catalysts\"[\s\S]*\})",
    ):
        m = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                continue
    return None


def normalize_warren_payload(raw: dict | None) -> dict:
    if not raw or not isinstance(raw, dict):
        return {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "warren_ai",
            "parse_ok": False,
            "catalysts": [],
            "parse_error": "invalid_or_empty_json",
        }
    catalysts = raw.get("catalysts") or []
    if not isinstance(catalysts, list):
        catalysts = []
    return {
        "generated_at": raw.get("generated_at") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": raw.get("source") or "warren_ai",
        "parse_ok": True,
        "catalysts": catalysts,
        "parse_error": None,
    }


def parse_theme_catalyst_text(text: str) -> dict:
    raw = _extract_json(text)
    return normalize_warren_payload(raw)


def write_warren_outputs(payload: dict, *, day: str | None = None) -> dict[str, str]:
    day = day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    os.makedirs(WARREN_OUTPUT, exist_ok=True)
    day_dir = os.path.join(WARREN_OUTPUT, day)
    os.makedirs(day_dir, exist_ok=True)

    paths = {
        "latest": os.path.join(WARREN_OUTPUT, "theme_catalyst_radar_latest.json"),
        "dated_flat": os.path.join(WARREN_OUTPUT, day.replace("-", "_") + "_theme_catalyst_radar.json"),
        "dated_folder": os.path.join(day_dir, "theme_catalyst_radar.json"),
    }
    body = json.dumps(payload, indent=2) + "\n"
    for p in paths.values():
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(body)
    return paths


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Parse Warren theme catalyst radar JSON")
    parser.add_argument("--input", help="Markdown or JSON file to parse")
    parser.add_argument("--day-dir", help="Day folder containing theme_catalyst_radar.md")
    parser.add_argument("--day", help="YYYY-MM-DD for output filenames")
    args = parser.parse_args()

    text = ""
    if args.input and os.path.isfile(args.input):
        with open(args.input, encoding="utf-8") as fh:
            text = fh.read()
    elif args.day_dir:
        md_path = os.path.join(args.day_dir, "theme_catalyst_radar.md")
        if os.path.isfile(md_path):
            with open(md_path, encoding="utf-8") as fh:
                text = fh.read()
    if not text:
        print("[theme_catalyst_parser] No input text found.", file=sys.stderr)
        return 1

    payload = parse_theme_catalyst_text(text)
    paths = write_warren_outputs(payload, day=args.day)
    print(f"[theme_catalyst_parser] parse_ok={payload.get('parse_ok')} catalysts={len(payload.get('catalysts') or [])}")
    for k, p in paths.items():
        print(f"  {k}: {p}")
    return 0 if payload.get("parse_ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
