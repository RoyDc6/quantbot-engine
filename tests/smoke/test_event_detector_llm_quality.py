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


def _price_data():
    close = [100 + i for i in range(30)]
    high = [c + 1 for c in close]
    low = [c - 1 for c in close]
    volume = [1_000_000 + i for i in range(30)]
    return {"close": close, "high": high, "low": low, "volume": volume}


def _write_cache(cache_dir: Path, key: str, payload: dict) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{key}.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def test_confidence_normalizes_percent_and_fraction():
    assert MarketEventDetector._normalize_confidence(80) == 0.8
    assert MarketEventDetector._normalize_confidence("55") == 0.55
    assert MarketEventDetector._normalize_confidence(0.7) == 0.7
    assert MarketEventDetector._normalize_confidence(None) == 0.0


def test_us_stale_cache_is_refreshed(tmp_path, monkeypatch):
    today = datetime.now().strftime("%Y-%m-%d")
    _write_cache(
        tmp_path,
        f"AAPL.US_{today}",
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
        f"00700.HK_{today}",
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
