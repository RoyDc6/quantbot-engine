import json
import ast
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from market_state import event_detector
from market_state.event_detector import MarketEventDetector
from core.models.llm_bias_model import LLMBiasModel


def _price_data():
    close = [100 + i for i in range(30)]
    high = [c + 1 for c in close]
    low = [c - 1 for c in close]
    volume = [1_000_000 + i for i in range(30)]
    return {"close": close, "high": high, "low": low, "volume": volume}


def test_price_summary_uses_upstream_canonical_rsi_only():
    detector = MarketEventDetector()
    price_data = _price_data()
    price_data["rsi_daily"] = 63.3

    summary = detector._build_price_summary(price_data)

    assert "RSI14: 63.3" in summary

    price_data.pop("rsi_daily")
    summary_without_canonical = detector._build_price_summary(price_data)
    assert "RSI14:" not in summary_without_canonical


def _write_cache(cache_dir: Path, key: str, payload: dict) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{key}.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def _cache_key_for(symbol: str, today: str, market_state: str = "CRAB") -> str:
    """构造与 MarketEventDetector._cache_key 一致的缓存 key（含模型名 slug）。"""
    detector = MarketEventDetector()
    context = f"{market_state}_{today}_{event_detector.PRICE_PROMPT_VERSION}"
    return detector._cache_key(symbol, today, context=context)


def test_confidence_normalizes_percent_and_fraction():
    assert MarketEventDetector._normalize_confidence(80) == 0.8
    assert MarketEventDetector._normalize_confidence("55") == 0.55
    assert MarketEventDetector._normalize_confidence(0.7) == 0.7
    assert MarketEventDetector._normalize_confidence(None) == 0.0


def test_us_stale_cache_is_refreshed(tmp_path, monkeypatch):
    today = datetime.now().strftime("%Y-%m-%d")
    _write_cache(
        tmp_path,
        _cache_key_for("AAPL.US", today),
        {
            "sentiment_score": 99,
            "event_type": "none",
            "event_summary": "stale",
            "confidence": 1.0,
            "timestamp": (datetime.now() - timedelta(hours=5)).isoformat(),
        },
    )

    calls = {"count": 0}

    def fake_chat(*args, **kwargs):
        calls["count"] += 1
        return {
            "bullish_factors": "fresh",
            "bearish_factors": "none",
            "sentiment_score": 10,
            "event_type": "none",
            "summary": "fresh LLM read",
            "confidence": 80,
        }

    monkeypatch.setattr(event_detector, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(event_detector, "NIM_AVAILABLE", True)
    monkeypatch.setattr(event_detector, "nvidia_llm", SimpleNamespace(chat=fake_chat), raising=False)

    detector = MarketEventDetector(cache_ttl_hours=24, market_cache_ttl_hours={"US": 2})
    result = detector.analyze_sentiment("AAPL.US", _price_data(), force=False)

    assert calls["count"] == 1
    assert result["sentiment_score"] == 10
    assert result["event_summary"] == "fresh LLM read"
    assert result["confidence"] == 0.8


def test_hk_fresh_cache_is_reused(tmp_path, monkeypatch):
    today = datetime.now().strftime("%Y-%m-%d")
    _write_cache(
        tmp_path,
        _cache_key_for("00700.HK", today),
        {
            "sentiment_score": 12,
            "event_type": "none",
            "event_summary": "fresh cache",
            "confidence": 0.6,
            "timestamp": (datetime.now() - timedelta(hours=1)).isoformat(),
        },
    )

    def fail_chat(*args, **kwargs):
        raise AssertionError("fresh cache should avoid LLM call")

    monkeypatch.setattr(event_detector, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(event_detector, "NIM_AVAILABLE", True)
    monkeypatch.setattr(event_detector, "nvidia_llm", SimpleNamespace(chat=fail_chat), raising=False)

    detector = MarketEventDetector(cache_ttl_hours=24, market_cache_ttl_hours={"US": 2})
    result = detector.analyze_sentiment("00700.HK", _price_data(), force=False)

    assert result["sentiment_score"] == 12
    assert result["event_summary"] == "fresh cache"


def test_analyze_sentiment_passes_quantbot_timeout_to_nim(tmp_path, monkeypatch):
    calls = []

    def fake_chat(*args, **kwargs):
        calls.append(kwargs)
        return {
            "sentiment_score": 10,
            "event_type": "none",
            "summary": "quick read",
            "confidence": 80,
        }

    monkeypatch.setattr(event_detector, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(event_detector, "NIM_AVAILABLE", True)
    monkeypatch.setattr(event_detector, "nvidia_llm", SimpleNamespace(chat=fake_chat), raising=False)

    detector = MarketEventDetector(
        cache_ttl_hours=24,
        market_cache_ttl_hours={"US": 2},
        llm_timeout_seconds=7,
    )
    result = detector.analyze_sentiment("00700.HK", _price_data(), force=True)

    assert result["sentiment_score"] == 10
    assert calls[0]["timeout"] == 7


def test_authorized_primary_fallback_models_and_timeout_are_configured():
    assert event_detector.config.LLM_MODEL == "nvidia/nemotron-3.5-lightning-30b-a3b"
    assert event_detector.config.LLM_FALLBACK_MODEL == ""
    assert event_detector.DEFAULT_NIM_CHAT_TIMEOUT_SECONDS == 15.0


def test_primary_api_failure_trips_run_level_fallback_circuit(tmp_path, monkeypatch):
    calls = []

    def fake_chat(*args, **kwargs):
        calls.append(kwargs["model"])
        if kwargs["model"] == "primary-model":
            return "[ERROR] Read timed out. (read timeout=15.0)"
        return {
            "sentiment_score": 20,
            "event_type": "momentum_shift",
            "summary": "fallback recovered",
            "confidence": 80,
        }

    monkeypatch.setattr(event_detector, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(event_detector, "NIM_AVAILABLE", True)
    monkeypatch.setattr(
        event_detector,
        "nvidia_llm",
        SimpleNamespace(chat=fake_chat),
        raising=False,
    )

    detector = MarketEventDetector(
        model="primary-model",
        fallback_model="fallback-model",
        cache_ttl_hours=24,
        market_cache_ttl_hours={"US": 2},
    )
    first = detector.analyze_sentiment("AAPL.US", _price_data(), force=True)
    second = detector.analyze_sentiment("MSFT.US", _price_data(), force=True)

    assert calls == ["primary-model", "fallback-model", "fallback-model"]
    assert detector._primary_model_unhealthy is True
    assert first["parse_status"] == "OK"
    assert first["llm_model"] == "fallback-model"
    assert first["llm_primary_model"] == "primary-model"
    assert first["route_status"] == "FALLBACK"
    assert any("本轮切换回退" in warning for warning in first["warnings"])
    assert second["llm_model"] == "fallback-model"
    assert second["route_status"] == "FALLBACK"
    assert any("本轮使用回退模型" in warning for warning in second["warnings"])


def test_crab_guidance_is_only_injected_for_crab_regime(tmp_path, monkeypatch):
    prompts = []

    def fake_chat(prompt, **kwargs):
        prompts.append(prompt)
        return {
            "sentiment_score": 0,
            "event_type": "none",
            "summary": "valid",
            "confidence": 60,
        }

    monkeypatch.setattr(event_detector, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(event_detector, "NIM_AVAILABLE", True)
    monkeypatch.setattr(
        event_detector,
        "nvidia_llm",
        SimpleNamespace(chat=fake_chat),
        raising=False,
    )

    detector = MarketEventDetector(
        model="primary-model",
        fallback_model="",
        cache_ttl_hours=24,
        market_cache_ttl_hours={"US": 2},
    )
    detector.analyze_sentiment("AAPL.US", _price_data(), market_state="BULL", force=True)
    detector.analyze_sentiment("00700.HK", _price_data(), market_state="CRAB", force=True)

    assert "CRAB means neutral" not in prompts[0]
    assert "Keep CRAB scores" not in prompts[0]
    assert "CRAB means neutral" in prompts[1]
    assert "Keep CRAB scores" in prompts[1]
    assert all(
        "Treat supplied indicator values as authoritative" in prompt
        for prompt in prompts
    )


def test_api_timeout_preserves_timeout_value_in_summary_and_warning(tmp_path, monkeypatch):
    api_error = (
        "[ERROR] HTTPSConnectionPool(host='integrate.api.nvidia.com', port=443): "
        "Read timed out. (read timeout=8.0)"
    )

    monkeypatch.setattr(event_detector, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(event_detector, "NIM_AVAILABLE", True)
    monkeypatch.setattr(
        event_detector,
        "nvidia_llm",
        SimpleNamespace(chat=lambda *args, **kwargs: api_error),
        raising=False,
    )

    detector = MarketEventDetector(
        fallback_model="",
        cache_ttl_hours=24,
        market_cache_ttl_hours={"US": 2},
    )
    result = detector.analyze_sentiment("AAPL.US", _price_data(), force=True)

    assert result["parse_status"] == "SKIPPED"
    assert result["event_summary"] == "NVIDIA NIM 请求超时（8.0s）"
    assert result["warnings"] == ["NVIDIA NIM 请求超时（8.0s）"]
    assert "read timeout=8.0" in result["llm_raw"]


def test_raised_api_timeout_uses_same_structured_diagnostic(tmp_path, monkeypatch):
    def raise_timeout(*args, **kwargs):
        raise TimeoutError("NVIDIA request timed out (read timeout=8.0)")

    monkeypatch.setattr(event_detector, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(event_detector, "NIM_AVAILABLE", True)
    monkeypatch.setattr(
        event_detector,
        "nvidia_llm",
        SimpleNamespace(chat=raise_timeout),
        raising=False,
    )

    detector = MarketEventDetector(
        fallback_model="",
        cache_ttl_hours=24,
        market_cache_ttl_hours={"US": 2},
    )
    result = detector.analyze_sentiment("AAPL.US", _price_data(), force=True)

    assert result["parse_status"] == "SKIPPED"
    assert result["event_summary"] == "NVIDIA NIM 请求超时（8.0s）"
    assert result["warnings"] == ["NVIDIA NIM 请求超时（8.0s）"]


def test_compact_retry_uses_shorter_retry_timeout(tmp_path, monkeypatch):
    calls = []
    replies = iter(
        [
            "not json",
            '{"sentiment_score": 0, "event_type": "none", "summary": "neutral", "confidence": 60}',
        ]
    )

    def fake_chat(*args, **kwargs):
        calls.append(kwargs)
        return next(replies)

    monkeypatch.setattr(event_detector, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(event_detector, "NIM_AVAILABLE", True)
    monkeypatch.setattr(event_detector, "nvidia_llm", SimpleNamespace(chat=fake_chat), raising=False)

    detector = MarketEventDetector(
        cache_ttl_hours=24,
        market_cache_ttl_hours={"US": 2},
        llm_timeout_seconds=9,
        llm_retry_timeout_seconds=3,
    )
    result = detector.analyze_sentiment("AAPL.US", _price_data(), force=True)

    assert result["parse_status"] == "OK"
    assert [call["timeout"] for call in calls] == [9, 3]


def test_partial_json_gets_parse_warning_and_summary(tmp_path, monkeypatch):
    def truncated_chat(*args, **kwargs):
        return '{"sentiment_score": -20, "event_type": "none", "confidence": 80'

    monkeypatch.setattr(event_detector, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(event_detector, "NIM_AVAILABLE", True)
    monkeypatch.setattr(event_detector, "nvidia_llm", SimpleNamespace(chat=truncated_chat), raising=False)

    detector = MarketEventDetector(cache_ttl_hours=24, market_cache_ttl_hours={"US": 2})
    result = detector.analyze_sentiment("AMD.US", _price_data(), force=True)

    assert result["sentiment_score"] == -20
    assert result["confidence"] == 0.8
    assert result["parse_status"] == "PARTIAL"
    assert "summary" in result["event_summary"]
    assert result["warnings"]


def test_partial_json_compact_retry_can_recover(tmp_path, monkeypatch):
    replies = iter(
        [
            '{"sentiment_score": 40, "event_type": "momentum_shift"',
            '{"sentiment_score": 20, "event_type": "none", "summary": "balanced", "confidence": 70}',
        ]
    )

    monkeypatch.setattr(event_detector, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(event_detector, "NIM_AVAILABLE", True)
    monkeypatch.setattr(
        event_detector,
        "nvidia_llm",
        SimpleNamespace(chat=lambda *args, **kwargs: next(replies)),
        raising=False,
    )

    detector = MarketEventDetector(cache_ttl_hours=24, market_cache_ttl_hours={"US": 2})
    result = detector.analyze_sentiment("META.US", _price_data(), force=True)

    assert result["parse_status"] == "OK"
    assert result["sentiment_score"] == 20
    assert result["confidence"] == 0.7
    assert "重试成功" in result["warnings"][0]


def test_unparseable_response_compact_retry_can_recover(tmp_path, monkeypatch):
    replies = iter(
        [
            "I will explain the market in a long narrative without JSON.",
            '{"sentiment_score": -20, "event_type": "none", "summary": "slightly bearish", "confidence": 65}',
        ]
    )

    monkeypatch.setattr(event_detector, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(event_detector, "NIM_AVAILABLE", True)
    monkeypatch.setattr(
        event_detector,
        "nvidia_llm",
        SimpleNamespace(chat=lambda *args, **kwargs: next(replies)),
        raising=False,
    )

    detector = MarketEventDetector(cache_ttl_hours=24, market_cache_ttl_hours={"US": 2})
    result = detector.analyze_sentiment("AAPL.US", _price_data(), force=True)

    assert result["parse_status"] == "OK"
    assert result["sentiment_score"] == -20
    assert "首次响应ERROR" in result["warnings"][0]


def test_unparseable_response_and_retry_are_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(event_detector, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(event_detector, "NIM_AVAILABLE", True)
    monkeypatch.setattr(
        event_detector,
        "nvidia_llm",
        SimpleNamespace(chat=lambda *args, **kwargs: "still no json"),
        raising=False,
    )

    detector = MarketEventDetector(cache_ttl_hours=24, market_cache_ttl_hours={"US": 2})
    result = detector.analyze_sentiment("AAPL.US", _price_data(), force=True)

    assert result["parse_status"] == "ERROR"
    assert result["sentiment_score"] == 0
    assert result["warnings"]


def test_unknown_event_type_is_normalized(tmp_path, monkeypatch):
    response = {
        "sentiment_score": 30,
        "event_type": "strong_uptrend",
        "summary": "positive trend",
        "confidence": 80,
    }

    monkeypatch.setattr(event_detector, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(event_detector, "NIM_AVAILABLE", True)
    monkeypatch.setattr(
        event_detector,
        "nvidia_llm",
        SimpleNamespace(chat=lambda *args, **kwargs: response),
        raising=False,
    )

    detector = MarketEventDetector(cache_ttl_hours=24, market_cache_ttl_hours={"US": 2})
    result = detector.analyze_sentiment("AAPL.US", _price_data(), force=True)

    assert result["event_type"] == "none"
    assert any("event_type 非标准" in warning for warning in result["warnings"])


def test_llm_bias_preserves_partial_source_status(monkeypatch):
    model = LLMBiasModel()
    monkeypatch.setattr(
        model._detector,
        "analyze_sentiment",
        lambda **kwargs: {
            "sentiment_score": 40,
            "event_type": "momentum_shift",
            "event_summary": "LLM 解析不完整: summary 为空",
            "llm_model": "fallback-model",
            "confidence": 0.5,
            "parse_status": "PARTIAL",
            "warnings": ["LLM解析不完整: 缺少 summary,confidence"],
        },
    )

    signal = model.generator("02513.HK", _price_data(), force=True)

    assert signal.source_status == "PARTIAL"
    assert signal.event_sentiment_score == 40
    assert signal.llm_model == "fallback-model"
    assert signal.warnings


def test_llm_bias_marks_successful_fallback_as_fallback(monkeypatch):
    model = LLMBiasModel()
    monkeypatch.setattr(
        model._detector,
        "analyze_sentiment",
        lambda **kwargs: {
            "sentiment_score": 10,
            "event_type": "none",
            "event_summary": "fallback response",
            "llm_model": "fallback-model",
            "llm_primary_model": "primary-model",
            "route_status": "FALLBACK",
            "confidence": 0.5,
            "parse_status": "OK",
            "warnings": ["LLM本轮使用回退模型"],
        },
    )

    signal = model.generator("00700.HK", _price_data(), force=True)

    assert signal.source_status == "FALLBACK"
    assert signal.llm_route_status == "FALLBACK"
    assert signal.llm_model == "fallback-model"
    assert signal.llm_primary_model == "primary-model"


def test_llm_bias_caches_successful_signal_by_context(monkeypatch):
    model = LLMBiasModel()
    calls = {"count": 0}

    def fake_analyze_sentiment(**kwargs):
        calls["count"] += 1
        return {
            "sentiment_score": 25,
            "event_type": "momentum_shift",
            "event_summary": "positive",
            "confidence": 0.5,
            "parse_status": "OK",
            "warnings": [],
        }

    kline_data = _price_data()
    kline_data["signal_asof"] = "2026-07-09"

    monkeypatch.setattr(model._detector, "analyze_sentiment", fake_analyze_sentiment)

    first = model.generator("00700.HK", kline_data, market_state="BULL")
    second = model.generator("00700.HK", kline_data, market_state="BULL")

    assert calls["count"] == 1
    assert first is second
    assert model.get_bias_score("00700.HK") == 25


def test_unified_runner_forces_llm_refresh_for_us_market():
    tree = ast.parse((ROOT / "unified_runner.py").read_text(encoding="utf-8"))

    force_exprs = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "analyze_ticker":
            continue
        for keyword in node.keywords:
            if keyword.arg == "force_llm":
                force_exprs.append(ast.unparse(keyword.value))

    assert "market == 'US'" in force_exprs
