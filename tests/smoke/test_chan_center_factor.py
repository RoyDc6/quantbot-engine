# -*- coding: utf-8 -*-
"""Independent ZS factor tests."""

import sys
import inspect
from pathlib import Path
from unittest.mock import PropertyMock, patch
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.models.chan_center_factor import (  # noqa: E402
    ChanCenterFactor,
    analyze_chan_center,
    _find_centers,
    _score_latest_center,
)


def _ohlc(close):
    close = np.asarray(close, dtype=float)
    index = pd.date_range("2025-01-01", periods=len(close), freq="D")
    return pd.DataFrame({
        "open": close,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "volume": np.full(len(close), 1000.0),
    }, index=index)


def test_zs_center_boundaries_match_confirmed_pen_rules():
    pivot_x = [0, 5, 10, 15, 20, 25, 30]
    pivot_type = [-1, 1, -1, 1, -1, 1, -1]
    pivot_price = [10, 20, 12, 18, 13, 25, 22]

    centers = _find_centers(
        pivot_x, pivot_type, pivot_price, pivot_count=7, data_len=40
    )

    assert len(centers) == 1
    center = centers[0]
    assert center["start"] == 0
    assert center["formation_end"] == 15
    assert center["end"] == 25
    assert center["zg"] == 18
    assert center["zd"] == 12
    assert center["active"] is False


def test_zs_score_rewards_confirmed_up_break_and_keeps_components_separate():
    close = np.array([15.0] * 18 + [18.5, 19.5])
    centers = [{
        "start": 0,
        "formation_end": 8,
        "end": 17,
        "zg": 18.0,
        "zd": 12.0,
        "entry_direction": 1,
        "active": False,
        "overlap": False,
        "extension_segments": 2,
    }]

    result = _score_latest_center(close, atr_value=1.0, centers=centers)

    assert result["direction"] == "BUY"
    assert result["factor_score"] >= 25
    assert result["confidence"] > 0.70
    assert result["details"]["event"] == "UP_BREAK"
    assert result["details"]["hold_bars"] == 2


def test_zs_inside_center_is_capped_and_active_confidence_is_limited():
    close = np.array([15.0] * 30)
    centers = [{
        "start": 5,
        "formation_end": 15,
        "end": 29,
        "zg": 18.0,
        "zd": 12.0,
        "entry_direction": -1,
        "active": True,
        "overlap": False,
        "extension_segments": 3,
    }]

    result = _score_latest_center(close, atr_value=1.0, centers=centers)

    assert result["direction"] == "HOLD"
    assert abs(result["factor_score"]) <= 25
    assert result["confidence"] <= 0.70
    assert result["details"]["phase"] == "extending"


def test_zs_factor_returns_neutral_when_no_confirmed_center_exists():
    df = _ohlc(np.linspace(10, 20, 60))

    signal = analyze_chan_center(df, ticker="TEST.HK")

    assert signal.direction == "HOLD"
    assert signal.factor_score == 0.0
    assert signal.factor_details["phase"] == "no_center"
    assert signal.source == "ZS"
    assert "shadow_only" not in signal.factor_details


def test_fusion_controller_has_no_zs_connection_point():
    from core.fusion_controller import FusionController

    assert "run_zs" not in inspect.signature(FusionController.analyze_ticker).parameters
    assert not hasattr(FusionController, "zs")

    controller = FusionController()
    df = _ohlc(np.linspace(10, 20, 80))
    with patch.object(controller, "_fetch_kline", return_value=df), \
         patch.object(FusionController, "gate", new_callable=PropertyMock, return_value=None):
        result = controller.analyze_ticker(
            "TEST.HK", run_xmm=False, run_vp=False, run_llm=False
        )

    assert "zs" not in result["sources"]
    assert "zs" not in result["status"]
