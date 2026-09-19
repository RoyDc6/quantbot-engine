from __future__ import annotations

import json
from pathlib import Path

from hk_daily_selection.config import StrategyConfig


def test_checked_in_default_config_matches_code_defaults() -> None:
    path = Path(__file__).resolve().parents[1] / "default_config.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    from_json = StrategyConfig(**payload)
    defaults = StrategyConfig()

    assert from_json == defaults
    assert from_json.allowed_security_types == ("STOCK",)
    assert from_json.all_in_cost_bps_per_side == 24.27
