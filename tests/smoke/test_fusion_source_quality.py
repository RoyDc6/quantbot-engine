from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from core.fusion_controller import FusionController, SourceStatus
from fusion_framework.signal_types import DecisionSignal
from fusion_framework.volume_profile import VolumeProfileBoxStrategy


def _kline(rows: int = 116) -> pd.DataFrame:
    close = np.linspace(100.0, 120.0, rows)
    df = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=rows, freq="B"),
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": np.full(rows, 1_000_000.0),
        }
    )
    df.attrs["source"] = "test"
    return df


class _NeutralXMM:
    def analyze(self, df):
        return {
            "signal": "HOLD",
            "position_size": 0.0,
            "reason": "neutral",
            "trend_layer": {"market": "SIDEWAYS"},
        }


class _PartialLLM:
    def generator(self, **kwargs):
        return DecisionSignal(
            ticker=kwargs["ticker"],
            direction="HOLD",
            confidence=0.5,
            date="2026-07-02",
            event_sentiment_score=40.0,
            event_type="momentum_shift",
            event_summary="LLM 解析不完整: summary 为空",
            signal_level="BULL",
            source="test",
            source_status="PARTIAL",
            warnings=["LLM解析不完整: 缺少 summary,confidence"],
        )


class _BullVP:
    def analyze(self, df, ticker=""):
        return DecisionSignal(
            ticker=ticker,
            direction="BUY",
            confidence=0.8,
            date="2026-07-06",
            signal_level="VP_BUY",
            factor_details={"state": "above_box"},
            source="test",
            source_status="OK",
        )


class _ValidLLM:
    def __init__(self):
        self.last_kline_data = None

    def generator(self, **kwargs):
        self.last_kline_data = kwargs["kline_data"]
        return DecisionSignal(
            ticker=kwargs["ticker"],
            direction="HOLD",
            confidence=1.0,
            date="2026-07-06",
            event_sentiment_score=100.0,
            event_type="momentum_shift",
            event_summary="strong technical momentum",
            event_confidence=1.0,
            signal_level="BULL",
            source="test",
            source_status="OK",
        )


def test_partial_llm_and_insufficient_vp_are_excluded_from_live_weights():
    controller = FusionController()
    controller._xmm = _NeutralXMM()
    controller._vp = VolumeProfileBoxStrategy(lookback=120)
    controller._llm = _PartialLLM()
    controller._gate = False
    controller._fetch_kline = lambda ticker: _kline()

    result = controller.analyze_ticker("02513.HK", market_state="BULL")

    assert result["status"]["xmm"] == SourceStatus.OK
    assert result["status"]["vp"] == SourceStatus.NO_DATA
    assert result["status"]["llm"] == SourceStatus.PARTIAL
    assert result["fusion"]["weights_used"] == pytest.approx(
        {"xmm": 0.8, "vp": 0.0, "llm": 0.0}
    )
    assert result["fusion"]["reserved_weights"] == pytest.approx({"llm": 0.2})
    assert result["fusion"]["normalization_denominator"] == 1.0
    assert result["fusion"]["score"] == 0.0
    assert result["fusion"]["raw_scores"]["llm"] == 40.0
    assert any("insufficient data" in warning for warning in result["warnings"])
    assert any("缺少 summary,confidence" in warning for warning in result["warnings"])


def test_default_llm_audit_mode_preserves_score_without_alpha_or_redistribution():
    controller = FusionController()
    controller._xmm = _NeutralXMM()
    controller._vp = _BullVP()
    llm = _ValidLLM()
    controller._llm = llm
    controller._gate = False
    controller._fetch_kline = lambda ticker: _kline(120)

    result = controller.analyze_ticker("00700.HK", market_state="BULL")
    fusion = result["fusion"]

    assert fusion["llm_alpha_mode"] == "audit_only"
    assert fusion["weights_used"] == {"xmm": 0.6, "vp": 0.25, "llm": 0.0}
    assert fusion["reserved_weights"] == {"llm": 0.15}
    assert fusion["normalization_denominator"] == 1.0
    assert fusion["raw_scores"]["llm"] == 100.0
    assert fusion["llm_audit_score"] == 100.0
    assert fusion["score"] == 20.0
    assert fusion["confidence"] == 0.38
    assert "LLM审计=+100" in fusion["reasoning"]
    assert any("alpha贡献为0" in warning for warning in fusion["warnings"])
    assert llm.last_kline_data["rsi_daily"] == result["rsi_daily"]


def test_weighted_llm_mode_remains_available_as_explicit_rollback():
    controller = FusionController({"llm_alpha_mode": "weighted"})
    controller._xmm = _NeutralXMM()
    controller._vp = _BullVP()
    controller._llm = _ValidLLM()
    controller._gate = False
    controller._fetch_kline = lambda ticker: _kline(120)

    fusion = controller.analyze_ticker("00700.HK", market_state="BULL")["fusion"]

    assert fusion["llm_alpha_mode"] == "weighted"
    assert fusion["weights_used"] == {"xmm": 0.6, "vp": 0.25, "llm": 0.15}
    assert fusion["reserved_weights"] == {"llm": 0.0}
    assert fusion["score"] == 35.0
    assert fusion["confidence"] == 0.53
    assert "LLM偏向=+100" in fusion["reasoning"]


def test_invalid_llm_alpha_mode_is_rejected():
    try:
        FusionController({"llm_alpha_mode": "mystery"})
    except ValueError as exc:
        assert "llm_alpha_mode" in str(exc)
    else:
        raise AssertionError("invalid llm_alpha_mode should fail")
