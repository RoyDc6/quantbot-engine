from types import SimpleNamespace

import core.futu_adapter as futu_adapter
from core.futu_adapter import FutuAdapter
from unified_runner import _fc_result_to_signal


class _Probe:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _QuoteContext:
    def __init__(self, ret=0, data=None):
        self.ret = ret
        self.data = data or {"server_ver": "test"}
        self.closed = False

    def get_global_state(self):
        return self.ret, self.data

    def close(self):
        self.closed = True


def test_futu_connection_refusal_fails_before_sdk_context(monkeypatch):
    opened = []

    def refuse(*args, **kwargs):
        raise ConnectionRefusedError("test refusal")

    monkeypatch.setattr(futu_adapter.socket, "create_connection", refuse)
    monkeypatch.setattr(
        futu_adapter,
        "ft",
        SimpleNamespace(
            RET_OK=0,
            OpenQuoteContext=lambda **kwargs: opened.append(kwargs),
        ),
        raising=False,
    )

    adapter = FutuAdapter(host="127.0.0.1", port=11111)
    adapter._available = True
    ok, message = adapter.test_connection(timeout=0.1)

    assert ok is False
    assert "TCP 不可达 127.0.0.1:11111" in message
    assert opened == []


def test_futu_connection_success_closes_probe_and_context(monkeypatch):
    probe = _Probe()
    context = _QuoteContext()
    monkeypatch.setattr(
        futu_adapter.socket,
        "create_connection",
        lambda *args, **kwargs: probe,
    )
    monkeypatch.setattr(
        futu_adapter,
        "ft",
        SimpleNamespace(RET_OK=0, OpenQuoteContext=lambda **kwargs: context),
        raising=False,
    )

    adapter = FutuAdapter(host="127.0.0.1", port=11111)
    adapter._available = True
    assert adapter.test_connection() == (True, "Futu OpenD 连接成功")
    assert probe.closed is True
    assert context.closed is True


def _fc_result(
    summary,
    rsi_daily=56.3,
    llm_status="OK",
    llm_route_status="PRIMARY",
):
    return {
        "ticker": "ADBE.US",
        "date": "2026-07-10",
        "close": 227.37,
        "rsi_daily": rsi_daily,
        "sources": {
            "xmm": {},
            "vp": {},
            "llm": {
                "sentiment_score": 90.0,
                "event_summary": summary,
                "event_type": "momentum_shift",
                "model": "test-model",
                "primary_model": "primary-model",
                "route_status": llm_route_status,
            },
        },
        "status": {"xmm": "OK", "vp": "OK", "llm": llm_status},
        "fusion": {
            "level": "HOLD",
            "score": -5.0,
            "confidence": 0.23,
            "weights_used": {"xmm": 0.6, "vp": 0.25, "llm": 0.0},
            "reserved_weights": {"llm": 0.15},
            "llm_alpha_mode": "audit_only",
            "raw_scores": {"xmm": 0.0, "vp": -20.0, "llm": 90.0},
        },
        "directive": {"level": "HOLD"},
        "gate": {"approved": True, "reject_reasons": []},
    }


def test_llm_rsi_mismatch_is_partial_only_in_report_metadata():
    raw_summary = "RSI 83.4 signals extreme overbought"
    signal = _fc_result_to_signal(_fc_result(raw_summary), "US")

    assert signal["llm_status"] == "PARTIAL"
    assert signal["llm_summary_quality"] == "RSI_MISMATCH"
    assert signal["llm_summary_raw"] == raw_summary
    assert "主信号 RSI 56.3" in signal["llm_summary"]
    assert any("摘要降级为PARTIAL" in item for item in signal["warnings"])
    assert signal["fusion_score"] == -5.0
    assert signal["gate_approved"] is True


def test_llm_rsi_within_tolerance_remains_ok():
    summary = "RSI14 at 53.0 with neutral momentum"
    signal = _fc_result_to_signal(_fc_result(summary), "US")

    assert signal["llm_status"] == "OK"
    assert signal["llm_summary_quality"] == "OK"
    assert signal["llm_summary"] == summary
    assert signal["llm_summary_raw"] == ""


def test_llm_summary_without_rsi_claim_remains_ok():
    summary = "Bull regime with mixed five-day momentum"
    signal = _fc_result_to_signal(_fc_result(summary), "US")

    assert signal["llm_status"] == "OK"
    assert signal["llm_summary_quality"] == "NO_RSI_CLAIM"
    assert signal["llm_summary"] == summary


def test_successful_fallback_is_not_flattened_to_ok():
    signal = _fc_result_to_signal(
        _fc_result(
            "Neutral momentum without RSI claim",
            llm_status="FALLBACK",
            llm_route_status="FALLBACK",
        ),
        "HK",
    )

    assert signal["llm_status"] == "FALLBACK"
    assert signal["llm_route_status"] == "FALLBACK"
    assert signal["llm_model"] == "test-model"
    assert signal["llm_primary_model"] == "primary-model"
    assert signal["fusion_score"] == -5.0
    assert signal["gate_approved"] is True


def test_llm_parenthesized_rsi_is_audited():
    signal = _fc_result_to_signal(
        _fc_result("Momentum strong with RSI14 (67.2)"),
        "US",
    )

    assert signal["llm_status"] == "PARTIAL"
    assert signal["llm_summary_quality"] == "RSI_MISMATCH"


def test_llm_rsi_threshold_contradiction_is_partial():
    signal = _fc_result_to_signal(
        _fc_result("RSI14 below 40 with weak momentum", rsi_daily=49.8),
        "HK",
    )

    assert signal["llm_status"] == "PARTIAL"
    assert signal["llm_summary_quality"] == "RSI_SEMANTIC_MISMATCH"


def test_llm_qualitative_rsi_contradiction_is_partial():
    signal = _fc_result_to_signal(
        _fc_result("RSI remains overbought", rsi_daily=57.0),
        "US",
    )

    assert signal["llm_status"] == "PARTIAL"
    assert signal["llm_summary_quality"] == "RSI_SEMANTIC_MISMATCH"


def test_llm_hedged_rsi_wording_is_not_a_direct_claim():
    signal = _fc_result_to_signal(
        _fc_result("Momentum is nearing overbought", rsi_daily=57.0),
        "US",
    )

    assert signal["llm_status"] == "OK"
    assert signal["llm_summary_quality"] == "NO_RSI_CLAIM"


def test_correct_rsi_number_does_not_hide_wrong_oversold_label():
    signal = _fc_result_to_signal(
        _fc_result("RSI14 at 38.5 signals oversold", rsi_daily=38.5),
        "US",
    )

    assert signal["llm_status"] == "PARTIAL"
    assert signal["llm_summary_quality"] == "RSI_SEMANTIC_MISMATCH"
    assert signal["llm_summary_rsi"] == 38.5


def test_correct_rsi_number_and_correct_oversold_label_remain_ok():
    signal = _fc_result_to_signal(
        _fc_result("RSI14 at 29.0 signals oversold", rsi_daily=29.0),
        "US",
    )

    assert signal["llm_status"] == "OK"
    assert signal["llm_summary_quality"] == "OK"
