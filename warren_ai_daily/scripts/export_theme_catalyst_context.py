"""Export manual catalyst block for Warren theme catalyst prompt."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import atlas_theme_catalyst_config as atcfg


def main() -> None:
    cfg = atcfg.load_config()
    print(json.dumps(cfg.get("catalysts") or [], indent=2))


if __name__ == "__main__":
    main()
