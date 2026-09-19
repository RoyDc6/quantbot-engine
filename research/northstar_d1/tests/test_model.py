from __future__ import annotations

import copy
import importlib.util
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from core.universe_manager import UniverseManager
from research.northstar_d1.data import (
    CompletedDailyDataSource,
    MarketDataFreshnessError,
    evaluate_snapshot_freshness,
    expected_completed_session,
    filter_completed_daily_bars,
)
from research.northstar_d1.backtest import backtest_long_only
from research.northstar_d1.factors import (
    calc_dual_trend,
    calc_td_sequence,
    calc_structure_layer,
)
from research.northstar_d1.model import NorthstarD1Model
from research.northstar_d1.config import NorthstarD1Config
from research.northstar_d1.runner import (
    _decision_audit_complete,
    build_market_parser,
    deployment_for,
    main_for_market,
    render_markdown,
    run_market,
    validate_market_symbols,
    write_report,
)
from research.northstar_d1.runtime import RunCoordinator


ROOT = Path(__file__).resolve().parents[3]


def _load_original(module_name: str, filename: str):
    spec = importlib.util.spec_from_file_location(
        module_name,
        ROOT / "xmm-strategy" / "modules" / filename,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _bars(length: int = 180) -> pd.DataFrame:
    rng = np.random.default_rng(20260803)
    close = 100 + np.cumsum(rng.normal(0.08, 1.3, length))
    spread = rng.uniform(0.4, 2.0, length)
    dates = pd.bdate_range("2025-01-02", periods=length)
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "open": close + rng.normal(0, 0.4, length),
            "high": close + spread,
            "low": close - spread,
            "close": close,
            "volume": rng.integers(100_000, 900_000, length),
        }
    )


def _trend(**overrides):
    value = {
        "market": "UP",
        "cross_short_up": False,
        "cross_long_up": False,
        "cross_short_down": False,
        "cross_long_down": False,
    }
    value.update(overrides)
    return value


def _structure(**overrides):
    value = {
        "bottom_structure": False,
        "bottom_structure_new": False,
        "top_structure": False,
        "top_structure_new": False,
    }
    value.update(overrides)
    return value


def _td(**overrides):
    value = {
        "td_count": 0,
        "td_near": False,
        "td_reached": False,
        "is_buy_seq": False,
        "is_sell_seq": False,
    }
    value.update(overrides)
    return value


def test_copied_trend_factor_matches_original():
    bars = _bars()
    original = _load_original("original_xmm_trend", "trend.py")
    expected = original.calc_dual_trend(bars)
    actual = calc_dual_trend(bars)

    pairs = {
        "short_top": "短顶",
        "short_bottom": "短底",
        "long_top": "长顶",
        "long_bottom": "长底",
        "cross_short_up": "cross_short_up",
        "cross_short_down": "cross_short_down",
        "cross_long_up": "cross_long_up",
        "cross_long_down": "cross_long_down",
    }
    for new_name, old_name in pairs.items():
        if actual[new_name].dtype == bool:
            assert actual[new_name].equals(expected[old_name])
        else:
            np.testing.assert_allclose(
                actual[new_name], expected[old_name], equal_nan=True
            )


def test_copied_structure_core_matches_original():
    bars = _bars()
    original = _load_original("original_xmm_structure", "structure.py")
    expected = original.calc_xmm_structure(bars)
    actual = calc_structure_layer(bars)
    pairs = {
        "diff": "diff",
        "dea": "dea",
        "macd": "macd",
        "bottom_plateau": "底部钝化",
        "bottom_structure": "底部结构",
        "top_plateau": "顶部钝化",
        "top_structure": "顶部结构",
    }
    for new_name, old_name in pairs.items():
        if actual[new_name].dtype == bool:
            assert actual[new_name].equals(expected[old_name])
        else:
            np.testing.assert_allclose(
                actual[new_name], expected[old_name], equal_nan=True
            )


def test_long_breakout_directly_generates_buy_40_percent():
    decision = NorthstarD1Model().decide(
        _trend(cross_long_up=True), _structure(), _td()
    )
    assert decision["action"] == "BUY"
    assert decision["signal_type"] == "LONG_BREAKOUT"
    assert decision["signal_fraction"] == 0.40
    audit = decision["decision_audit"]
    assert audit["trend"] == {
        "market_state": "UP",
        "event": "LONG_BREAKOUT",
    }
    assert audit["structure"]["effect"] == "NONE"
    assert audit["sequence"]["stage"] == "NONE"


def test_short_and_long_breakout_generate_buy_60_percent():
    decision = NorthstarD1Model().decide(
        _trend(cross_short_up=True, cross_long_up=True),
        _structure(),
        _td(),
    )
    assert decision["action"] == "BUY"
    assert decision["signal_fraction"] == 0.60


def test_td_never_creates_an_action_by_itself():
    decision = NorthstarD1Model().decide(
        _trend(),
        _structure(),
        _td(td_count=9, td_reached=True, is_buy_seq=True),
    )
    assert decision["action"] == "HOLD"
    assert decision["signal_fraction"] == 0.0
    assert decision["decision_audit"]["sequence"] == {
        "count": 9,
        "stage": "REACHED",
        "direction": "BUY",
        "alignment": "NOT_APPLICABLE",
        "effect": "NO_ACTION_TO_MODIFY",
        "confidence_delta": 0.0,
    }


def test_structure_is_aligned_modifier_not_trend_veto():
    decision = NorthstarD1Model().decide(
        _trend(cross_long_up=True),
        _structure(top_structure=True),
        _td(),
    )
    assert decision["action"] == "BUY"
    assert decision["signal_fraction"] == 0.30
    assert "STRUCTURE_OPPOSED_TRIM" in decision["reason_codes"]
    audit = decision["decision_audit"]
    assert audit["structure"]["effect"] == "OPPOSED_TRIM"
    assert audit["structure"]["fraction_delta"] == -0.10
    assert audit["structure"]["confidence_delta"] == -0.10


def test_sequence_audit_records_aligned_confidence_effect():
    decision = NorthstarD1Model().decide(
        _trend(cross_long_up=True),
        _structure(),
        _td(td_count=9, td_reached=True, is_buy_seq=True),
    )
    assert decision["action"] == "BUY"
    assert decision["signal_fraction"] == 0.40
    assert decision["confidence"] == 0.88
    assert decision["decision_audit"]["sequence"] == {
        "count": 9,
        "stage": "REACHED",
        "direction": "BUY",
        "alignment": "ALIGNED",
        "effect": "CONFIDENCE_UP",
        "confidence_delta": 0.10,
    }


def test_report_quality_rejects_missing_or_mismatched_audit_layers():
    decision = NorthstarD1Model().decide(
        _trend(cross_long_up=True), _structure(), _td()
    )
    signal = {
        "action": "BUY",
        "decision_audit": decision["decision_audit"],
    }
    signal["decision_audit"]["decision"].update(
        {"candidate_action": "BUY", "emitted_action": "BUY"}
    )
    assert _decision_audit_complete(signal) is True

    missing_sequence = copy.deepcopy(signal)
    del missing_sequence["decision_audit"]["sequence"]
    assert _decision_audit_complete(missing_sequence) is False

    mismatched_emission = copy.deepcopy(signal)
    mismatched_emission["decision_audit"]["decision"][
        "emitted_action"
    ] = "HOLD"
    assert _decision_audit_complete(mismatched_emission) is False


def test_downtrend_bottom_structure_is_watch_only():
    decision = NorthstarD1Model().decide(
        _trend(market="DOWN"),
        _structure(bottom_structure_new=True),
        _td(),
    )
    assert decision["action"] == "HOLD"
    assert "COUNTERTREND_STRUCTURE_WATCH_ONLY" in decision["reason_codes"]


def test_td_sign_contract_has_buy_positive_and_sell_negative():
    falling = pd.Series([20 - i for i in range(15)], dtype=float)
    rising = pd.Series([20 + i for i in range(15)], dtype=float)
    assert int(calc_td_sequence(falling)["td_count"].iloc[-1]) > 0
    assert int(calc_td_sequence(rising)["td_count"].iloc[-1]) < 0


def test_hk_incomplete_same_day_bar_is_excluded():
    bars = _bars(130)
    bars.loc[bars.index[-2], "date"] = "2026-07-31"
    bars.loc[bars.index[-1], "date"] = "2026-08-03"
    filtered = filter_completed_daily_bars(
        bars,
        "HK",
        now=datetime(2026, 8, 3, 12, 0, tzinfo=ZoneInfo("Asia/Hong_Kong")),
    )
    assert filtered["date"].iloc[-1] == "2026-07-31"
    assert filtered.attrs["bar_confirmed"] is True
    assert filtered.attrs["incomplete_bars_excluded"] == 1


def test_hk_same_day_bar_is_retained_after_cutoff():
    bars = _bars(130)
    bars.loc[bars.index[-1], "date"] = "2026-08-03"
    filtered = filter_completed_daily_bars(
        bars,
        "HK",
        now=datetime(2026, 8, 3, 16, 20, tzinfo=ZoneInfo("Asia/Hong_Kong")),
    )
    assert filtered["date"].iloc[-1] == "2026-08-03"
    assert filtered.attrs["incomplete_bars_excluded"] == 0


def test_futu_calendar_controls_expected_completed_session():
    calendar = [
        {"time": "2026-07-31", "trade_date_type": "WHOLE"},
        {"time": "2026-08-03", "trade_date_type": "WHOLE"},
    ]
    before_close = expected_completed_session(
        calendar,
        "HK",
        queried_at=datetime(
            2026, 8, 3, 12, 0, tzinfo=ZoneInfo("Asia/Hong_Kong")
        ),
    )
    after_close = expected_completed_session(
        calendar,
        "HK",
        queried_at=datetime(
            2026, 8, 3, 16, 20, tzinfo=ZoneInfo("Asia/Hong_Kong")
        ),
    )
    assert before_close == "2026-07-31"
    assert after_close == "2026-08-03"


def test_futu_morning_session_uses_shortened_day_cutoff():
    calendar = [
        {"time": "2026-07-31", "trade_date_type": "WHOLE"},
        {"time": "2026-08-03", "trade_date_type": "MORNING"},
    ]
    completed = expected_completed_session(
        calendar,
        "HK",
        queried_at=datetime(
            2026, 8, 3, 12, 20, tzinfo=ZoneInfo("Asia/Hong_Kong")
        ),
    )
    assert completed == "2026-08-03"

    bars = _bars(130)
    bars.loc[bars.index[-1], "date"] = "2026-08-03"
    filtered = filter_completed_daily_bars(
        bars,
        "HK",
        now=datetime(
            2026, 8, 3, 12, 20, tzinfo=ZoneInfo("Asia/Hong_Kong")
        ),
        expected_session=completed,
    )
    assert filtered["date"].iloc[-1] == "2026-08-03"
    assert filtered.attrs["session_cutoff"] == "FUTU_TRADING_CALENDAR"


def test_live_snapshot_age_is_strict_but_closed_market_is_not_last_trade_strict():
    queried_at = datetime(
        2026, 8, 3, 10, 4, tzinfo=ZoneInfo("Asia/Hong_Kong")
    )
    fresh = evaluate_snapshot_freshness(
        update_time="2026-08-03 10:00:01",
        market="HK",
        market_state="MORNING",
        queried_at=queried_at,
    )
    assert fresh["market_live"] is True
    assert fresh["age_seconds"] == 239.0

    with pytest.raises(MarketDataFreshnessError, match="stale during MORNING"):
        evaluate_snapshot_freshness(
            update_time="2026-08-03 09:50:00",
            market="HK",
            market_state="MORNING",
            queried_at=queried_at,
        )

    closed = evaluate_snapshot_freshness(
        update_time="2026-07-31 16:00:00",
        market="HK",
        market_state="REST",
        queried_at=datetime(
            2026, 8, 3, 12, 0, tzinfo=ZoneInfo("Asia/Hong_Kong")
        ),
    )
    assert closed["market_live"] is False
    assert closed["freshness_basis"] == "LIVE_OPEND_RESPONSE_MARKET_NOT_TRADING"


def test_completed_source_queries_futu_freshness_on_every_fetch():
    bars = _bars(130)
    bars.loc[bars.index[-2], "date"] = "2026-07-30"
    bars.loc[bars.index[-1], "date"] = "2026-07-31"

    class FakeAdapter:
        host = "127.0.0.1"
        port = 11111

        @staticmethod
        def fetch_kline(symbol, count, ktype, autype):
            return bars.copy()

    class FakeUniverse:
        @staticmethod
        def get_info(symbol):
            return {"name": "Test", "market": "HK", "ticker": symbol}

    class CountingProbe:
        calls = 0

        def query(self, symbol, market):
            self.calls += 1
            request_id = f"request-{self.calls}"
            queried_at = datetime.now(timezone.utc).isoformat()
            return {
                "source": "FUTU_OPEND_LIVE",
                "freshness_status": "FRESH",
                "request_id": request_id,
                "queried_at": queried_at,
                "cache_used": False,
                "fallback_used": False,
                "expected_completed_session": "2026-07-31",
                "snapshot": {
                    "last_price": 100.0,
                    "update_time": queried_at,
                    "market_state": "REST",
                },
            }

    probe = CountingProbe()
    source = CompletedDailyDataSource(
        adapter=FakeAdapter(),
        universe=FakeUniverse(),
        freshness_probe=probe,
    )
    first = source.fetch("TEST.HK")
    second = source.fetch("TEST.HK")
    assert probe.calls == 2
    assert first.attrs["request_id"] == "request-1"
    assert second.attrs["request_id"] == "request-2"
    assert first.attrs["source"] == "FUTU_OPEND_LIVE"
    assert first.attrs["cache_used"] is False
    assert first.attrs["fallback_used"] is False


def test_completed_source_fails_closed_when_daily_session_is_missing():
    bars = _bars(130)
    bars.loc[bars.index[-1], "date"] = "2026-07-31"

    class FakeAdapter:
        host = "127.0.0.1"
        port = 11111

        @staticmethod
        def fetch_kline(symbol, count, ktype, autype):
            return bars.copy()

    class FakeUniverse:
        @staticmethod
        def get_info(symbol):
            return {"name": "Test", "market": "HK", "ticker": symbol}

    class Probe:
        @staticmethod
        def query(symbol, market):
            queried_at = datetime.now(timezone.utc).isoformat()
            return {
                "source": "FUTU_OPEND_LIVE",
                "freshness_status": "FRESH",
                "request_id": "request",
                "queried_at": queried_at,
                "cache_used": False,
                "fallback_used": False,
                "expected_completed_session": "2026-08-03",
                "snapshot": {
                    "last_price": 100.0,
                    "update_time": queried_at,
                    "market_state": "REST",
                },
            }

    source = CompletedDailyDataSource(
        adapter=FakeAdapter(),
        universe=FakeUniverse(),
        freshness_probe=Probe(),
    )
    with pytest.raises(MarketDataFreshnessError, match="daily bar mismatch"):
        source.fetch("TEST.HK")


def test_universe_matches_current_quantbot_us_hk_pool():
    universe = UniverseManager()
    assert universe.get_hk_symbols() == [
        "00700.HK",
        "09988.HK",
        "03690.HK",
        "01024.HK",
        "01810.HK",
        "00981.HK",
        "02513.HK",
    ]
    assert universe.get_us_symbols() == [
        "AAPL.US",
        "AMZN.US",
        "MSFT.US",
        "GOOGL.US",
        "META.US",
        "NVDA.US",
        "TSLA.US",
        "AMD.US",
        "AVGO.US",
        "ORCL.US",
        "NFLX.US",
        "CRM.US",
        "ADBE.US",
        "INTC.US",
        "QCOM.US",
    ]


def test_runner_is_research_only_and_cannot_create_orders():
    class FakeUniverse:
        @staticmethod
        def get_symbols_by_market(market):
            return ["TEST.HK"] if market == "HK" else []

        @staticmethod
        def get_info(symbol):
            return {"name": "Test", "market": "HK", "ticker": symbol}

    class FakeDataSource:
        config = NorthstarD1Config()
        universe = FakeUniverse()

        def symbols(self, market, requested=None):
            return self.universe.get_symbols_by_market(market)

        @staticmethod
        def fetch(symbol, now=None):
            bars = _bars()
            bars.attrs["bar_confirmed"] = True
            bars.attrs["source"] = "FUTU_OPEND_LIVE"
            bars.attrs["freshness_status"] = "FRESH"
            bars.attrs["cache_used"] = False
            bars.attrs["fallback_used"] = False
            bars.attrs["request_id"] = "test-request"
            bars.attrs["market_data"] = {
                "source": "FUTU_OPEND_LIVE",
                "freshness_status": "FRESH",
                "cache_used": False,
                "fallback_used": False,
                "request_id": "test-request",
                "snapshot": {
                    "last_price": float(bars["close"].iloc[-1]),
                    "update_time": "2026-08-03T12:00:00+08:00",
                },
            }
            return bars

    payload = run_market("HK", data_source=FakeDataSource())
    assert payload["mode"] == "RESEARCH_ONLY"
    assert payload["deployment_id"] == "NORTHSTAR_D1_HK"
    assert payload["market_isolation"] == {
        "single_market_only": True,
        "combined_run_allowed": False,
        "universe_market": "HK",
        "output_partition": "hk",
    }
    assert all(
        signal["deployment_id"] == "NORTHSTAR_D1_HK"
        for signal in payload["signals"]
    )
    # Dependency-injected fake data can exercise logic but cannot pass the
    # production Futu source contract or emit paper intents.
    assert payload["data_ready"] is False
    assert payload["freshness_ready"] is False
    assert payload["market_data_contract"]["runtime_source_contract_enforced"] is False
    assert payload["paper_intents"] == []
    assert payload["validation_status"] == "NOT_EVALUATED"
    assert payload["formal_publish_allowed"] is False
    assert payload["orders"] == []
    assert payload["fills"] == []
    assert payload["execution"] == {
        "enabled": False,
        "account_access": False,
        "order_api_called": False,
    }
    assert payload["run_verdict"] == "RUN_FAILED_CLOSED"
    assert set(payload["run_gate_checks"]) == {
        "deployment_id_match",
        "market_match",
        "single_market_only",
        "combined_run_disallowed",
        "data_ready",
        "freshness_ready",
        "summary_errors_zero",
        "errors_empty",
        "execution_disabled",
        "account_access_disabled",
        "order_api_not_called",
        "orders_empty",
        "fills_empty",
    }
    assert len(payload["run_gate_checks"]) == 13
    assert all(
        isinstance(value, bool) for value in payload["run_gate_checks"].values()
    )
    assert payload["report_quality_ready"] is True
    assert payload["report_quality_checks"] == {
        "company_names_complete": True,
        "decision_audit_complete": True,
        "private_detail_policy_enforced": True,
    }
    assert all(
        signal["decision_audit"]["decision"]["emitted_action"]
        == signal["action"]
        for signal in payload["signals"]
    )

    payload["signals"][0]["decision_audit"]["decision"][
        "reason_codes"
    ].append("TD_INTERNAL_REASON_CODE")
    markdown = render_markdown(payload)
    assert "## 三层决策审计（私有）" in markdown
    assert "### 结构信号" in markdown
    assert "### 序列信号" in markdown
    assert "| 标的代码 | 公司名称 | 候选→输出 |" in markdown
    assert "| 标的代码 | 公司名称 | 计数 |" in markdown
    assert "| 标的代码 | 公司名称 | 来源 |" in markdown
    assert markdown.count(
        f"| {payload['signals'][0]['symbol']} | {payload['signals'][0]['name']} |"
    ) >= 4
    assert "HOLD→HOLD" in markdown
    assert "仅展示模型当次使用的状态与影响" in markdown
    assert "日线完成性：`COMPLETED_SESSION_ONLY`" in markdown
    assert "| 动作 | 比例语义 | 原始比例 |" in markdown
    assert "无仓位变化" in markdown
    assert "TD_INTERNAL_REASON_CODE" not in markdown
    for private_token in (
        "macd_fast",
        "macd_slow",
        "macd_signal",
        "structure_threshold",
        "td_period",
        "short_period",
        "long_period",
    ):
        assert private_token not in markdown

    signal = payload["signals"][0]
    signal["action"] = "SELL"
    signal["fraction_semantics"] = "fraction_of_existing_position_to_reduce"
    signal["raw_signal_fraction"] = 0.40
    signal["risk_capped_fraction"] = 0.40
    signal["market_data"]["opend_clock_skew_seconds"] = 18.941
    signal["market_data"]["snapshot"].update(
        {
            "age_seconds": -12.273,
            "market_live": False,
            "age_limit_seconds": None,
            "market_state": "AFTER_HOURS_END",
            "freshness_basis": "LIVE_OPEND_RESPONSE_MARKET_NOT_TRADING",
        }
    )
    payload["paper_intents"] = [
        {
            "symbol": signal["symbol"],
            "name": signal["name"],
            "action": "SELL",
            "risk_capped_fraction": 0.40,
            "fraction_semantics": signal["fraction_semantics"],
            "confidence": 0.78,
        }
    ]
    semantic_markdown = render_markdown(payload)
    assert "SELL减少既有暴露" in semantic_markdown
    assert "-12.273（时钟差）" in semantic_markdown
    assert "18.941" in semantic_markdown
    assert "N/A（非交易时段）" in semantic_markdown


def test_hk_and_us_deployments_have_distinct_ids_timezones_and_cli():
    hk = deployment_for("HK")
    us = deployment_for("US")
    assert hk["deployment_id"] == "NORTHSTAR_D1_HK"
    assert us["deployment_id"] == "NORTHSTAR_D1_US"
    assert hk["timezone"] == "Asia/Hong_Kong"
    assert us["timezone"] == "America/New_York"
    assert hk["schedule_timezone"] == "Asia/Hong_Kong"
    assert (hk["schedule_hour"], hk["schedule_minute"]) == (16, 20)
    assert us["schedule_timezone"] == "Asia/Shanghai"
    assert (us["schedule_hour"], us["schedule_minute"]) == (10, 0)
    assert hk != us

    hk_args = build_market_parser("HK").parse_args([])
    us_args = build_market_parser("US").parse_args([])
    assert not hasattr(hk_args, "market")
    assert not hasattr(us_args, "market")
    with pytest.raises(SystemExit):
        build_market_parser("HK").parse_args(["--market", "US"])

    assert validate_market_symbols("HK", ["09988.HK"]) == ["09988.HK"]
    assert validate_market_symbols("US", ["AAPL.US"]) == ["AAPL.US"]
    with pytest.raises(ValueError, match="cross-market symbols"):
        validate_market_symbols("HK", ["AAPL.US"])
    with pytest.raises(ValueError, match="cross-market symbols"):
        validate_market_symbols("US", ["09988.HK"])


def test_reports_are_always_partitioned_by_market(tmp_path):
    payload = {
        "market": "HK",
        "signal_asof": "2026-07-31",
        "model_id": "NORTHSTAR_D1",
        "model_version": "1.0.0",
        "deployment_id": "NORTHSTAR_D1_HK",
        "mode": "RESEARCH_ONLY",
        "data_ready": False,
        "freshness_ready": False,
        "validation_status": "NOT_EVALUATED",
        "formal_publish_allowed": False,
        "summary": {"buy": 0, "sell": 0, "hold": 0, "errors": 0},
        "signals": [],
        "paper_intents": [],
        "errors": [],
    }
    paths = write_report(payload, tmp_path)
    assert Path(paths["markdown"]).parent == tmp_path / "hk"
    assert Path(paths["json"]).parent == tmp_path / "hk"
    assert Path(paths["archive_json"]).is_file()
    assert Path(paths["archive_markdown"]).is_file()
    assert Path(paths["archive_manifest"]).is_file()


def _successful_empty_payload() -> dict:
    return {
        "schema_version": "2.2",
        "model_id": "NORTHSTAR_D1",
        "model_name": "Northstar-D1",
        "model_version": "1.0.0",
        "deployment_id": "NORTHSTAR_D1_HK",
        "deployment_name": "Northstar-D1 HK",
        "mode": "RESEARCH_ONLY",
        "market": "HK",
        "market_timezone": "Asia/Hong_Kong",
        "generated_at": "2026-08-04T16:20:00+08:00",
        "signal_asof": "2026-08-04",
        "data_ready": True,
        "freshness_ready": True,
        "run_verdict": "RUN_PASS",
        "run_gate_checks": {"all_test_gates": True},
        "report_quality_ready": True,
        "report_quality_checks": {
            "company_names_complete": True,
            "decision_audit_complete": True,
            "private_detail_policy_enforced": True,
        },
        "validation_status": "NOT_EVALUATED",
        "formal_publish_allowed": False,
        "signals": [],
        "paper_intents": [],
        "orders": [],
        "fills": [],
        "errors": [],
        "summary": {"buy": 0, "sell": 0, "hold": 0, "errors": 0},
    }


def test_report_archives_are_immutable_and_canonical_is_atomic(tmp_path):
    first_payload = _successful_empty_payload()
    first = write_report(first_payload, tmp_path, run_id="HK-SLOT-A01")
    first_archive = Path(first["archive_json"])
    first_bytes = first_archive.read_bytes()

    second_payload = _successful_empty_payload()
    second_payload["summary"]["buy"] = 1
    second = write_report(second_payload, tmp_path, run_id="HK-SLOT-A02")

    assert first_archive.read_bytes() == first_bytes
    assert Path(second["archive_json"]).parent != first_archive.parent
    canonical = json.loads(Path(second["json"]).read_text(encoding="utf-8"))
    assert canonical["run"]["run_id"] == "HK-SLOT-A02"
    assert canonical["summary"]["buy"] == 1
    manifest = json.loads(
        Path(second["archive_manifest"]).read_text(encoding="utf-8")
    )
    assert manifest["sha256"]["json"] == hashlib.sha256(
        Path(second["archive_json"]).read_bytes()
    ).hexdigest().upper()
    assert manifest["sha256"]["markdown"] == hashlib.sha256(
        Path(second["archive_markdown"]).read_bytes()
    ).hexdigest().upper()
    with pytest.raises(FileExistsError):
        write_report(second_payload, tmp_path, run_id="HK-SLOT-A02")


def test_run_coordinator_suppresses_verified_success(tmp_path, monkeypatch):
    monkeypatch.delenv("NORTHSTAR_AUTOMATION_ID", raising=False)
    monkeypatch.delenv("NORTHSTAR_AUTOMATION_RUN_ID", raising=False)
    monkeypatch.delenv("NORTHSTAR_AUTOMATION_CONVERSATION_ID", raising=False)
    monkeypatch.delenv("NORTHSTAR_AUTOMATION_RUN_KIND", raising=False)
    monkeypatch.delenv("NORTHSTAR_INVOCATION_SOURCE", raising=False)
    deployment = deployment_for("HK")
    now = datetime(2026, 8, 4, 16, 20, tzinfo=ZoneInfo("Asia/Hong_Kong"))
    first = RunCoordinator(
        market="HK",
        deployment=deployment,
        output_root=tmp_path,
        now=now,
    )
    decision = first.acquire()
    assert decision.should_run is True
    metadata = first.metadata()
    assert metadata["scheduled_at_local"] == "2026-08-04T16:20:00+08:00"
    assert metadata["started_at_local"] == "2026-08-04T16:20:00+08:00"
    assert metadata["schedule_lag_seconds"] == 0.0
    assert metadata["invocation_source"] == "LOCAL_OR_UNATTRIBUTED"
    assert metadata["automation_id"] is None
    assert metadata["automation_run_id"] is None
    assert metadata["automation_conversation_id"] is None
    assert metadata["automation_run_kind"] is None
    payload = _successful_empty_payload()
    payload["run"] = metadata
    payload["report_generated_at_utc"] = "2026-08-04T08:20:01+00:00"
    paths = write_report(payload, tmp_path, run_id=decision.run_id)
    first.mark_success(paths)
    state = json.loads(
        (tmp_path / "hk" / ".runtime" / "latest.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["schedule_lag_seconds"] == 0.0
    assert state["invocation_source"] == "LOCAL_OR_UNATTRIBUTED"
    assert state["lock_release"]["status"] == "RELEASED"
    assert state["lock_release"]["method"] == "ATOMIC_RENAME"
    assert not (tmp_path / "hk" / ".runtime" / "active.lock").exists()
    released_lock = Path(state["lock_release"]["archive_path"])
    assert released_lock.is_file()
    assert json.loads(released_lock.read_text(encoding="utf-8"))["run_id"] == decision.run_id
    report_text = Path(paths["markdown"]).read_text(encoding="utf-8")
    assert "计划时点：`2026-08-04T16:20:00+08:00`" in report_text
    assert "调度偏差：`延迟 0.0s`" in report_text
    assert "Automation ID：`NOT_PROVIDED`" in report_text

    duplicate = RunCoordinator(
        market="HK",
        deployment=deployment,
        output_root=tmp_path,
        now=now + timedelta(seconds=30),
    ).acquire()
    assert duplicate.action == "DUPLICATE_SUPPRESSED"
    assert duplicate.run_id == decision.run_id


def test_run_coordinator_records_verified_workbuddy_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_INVOCATION_SOURCE", "WORKBUDDY_AUTOMATION")
    monkeypatch.setenv("NORTHSTAR_AUTOMATION_ID", "automation-hk")
    monkeypatch.setenv("NORTHSTAR_AUTOMATION_RUN_ID", "run-natural-hk")
    monkeypatch.setenv(
        "NORTHSTAR_AUTOMATION_CONVERSATION_ID",
        "conversation-natural-hk",
    )
    monkeypatch.setenv("NORTHSTAR_AUTOMATION_RUN_KIND", "scheduled")
    deployment = deployment_for("HK")
    now = datetime(2026, 8, 4, 16, 20, tzinfo=ZoneInfo("Asia/Hong_Kong"))
    coordinator = RunCoordinator(
        market="HK",
        deployment=deployment,
        output_root=tmp_path,
        now=now,
    )

    decision = coordinator.acquire()
    metadata = coordinator.metadata()

    assert decision.should_run is True
    assert metadata["invocation_source"] == "WORKBUDDY_AUTOMATION"
    assert metadata["automation_id"] == "automation-hk"
    assert metadata["automation_run_id"] == "run-natural-hk"
    assert metadata["automation_conversation_id"] == "conversation-natural-hk"
    assert metadata["automation_run_kind"] == "scheduled"

    payload = _successful_empty_payload()
    payload["run"] = metadata
    paths = write_report(payload, tmp_path, run_id=decision.run_id)
    coordinator.mark_success(paths)
    report_text = Path(paths["markdown"]).read_text(encoding="utf-8")
    assert "调用来源：`WORKBUDDY_AUTOMATION`" in report_text
    assert "Automation ID：`automation-hk`" in report_text
    assert "Automation Run ID：`run-natural-hk`" in report_text
    assert "Automation Conversation ID：`conversation-natural-hk`" in report_text
    assert "Automation Run Kind：`scheduled`" in report_text


def test_run_coordinator_skips_before_market_schedule_without_lock_or_state(tmp_path):
    deployment = deployment_for("HK")
    now = datetime(2026, 8, 4, 11, 10, tzinfo=ZoneInfo("Asia/Hong_Kong"))
    coordinator = RunCoordinator(
        market="HK",
        deployment=deployment,
        output_root=tmp_path,
        now=now,
    )

    decision = coordinator.acquire()

    assert decision.action == "SKIPPED_EARLY"
    assert decision.should_run is False
    runtime_dir = tmp_path / "hk" / ".runtime"
    assert not (runtime_dir / "latest.json").exists()
    assert not (runtime_dir / "active.lock").exists()
    events = [
        json.loads(line)
        for line in (runtime_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert events == [
        {
            "schema_version": "1.0",
            "recorded_at_utc": "2026-08-04T03:10:00+00:00",
            "deployment_id": "NORTHSTAR_D1_HK",
            "market": "HK",
            "slot_key": "HK:2026-08-04:16:20:Asia/Hong_Kong",
            "event": "SKIPPED_EARLY",
            "run_id": None,
            "attempt": 0,
            "actual_time_local": "2026-08-04T11:10:00+08:00",
            "threshold_local": "2026-08-04T16:20:00+08:00",
            "reason": (
                "actual time 2026-08-04T11:10:00+08:00 is earlier than "
                "scheduled threshold 2026-08-04T16:20:00+08:00"
            ),
        }
    ]


def test_run_coordinator_blocks_success_with_missing_manifest(tmp_path):
    deployment = deployment_for("HK")
    now = datetime(2026, 8, 4, 16, 20, tzinfo=ZoneInfo("Asia/Hong_Kong"))
    first = RunCoordinator(
        market="HK",
        deployment=deployment,
        output_root=tmp_path,
        now=now,
    )
    decision = first.acquire()
    payload = _successful_empty_payload()
    payload["run"] = first.metadata()
    paths = write_report(payload, tmp_path, run_id=decision.run_id)
    first.mark_success(paths)
    Path(paths["archive_manifest"]).unlink()

    blocked = RunCoordinator(
        market="HK",
        deployment=deployment,
        output_root=tmp_path,
        now=now + timedelta(seconds=30),
    ).acquire()
    assert blocked.action == "BLOCKED_CORRUPT_PRIOR_SUCCESS"


def test_failed_slot_waits_five_minutes_and_allows_only_one_retry(tmp_path):
    deployment = deployment_for("HK")
    now = datetime(2026, 8, 4, 16, 20, tzinfo=ZoneInfo("Asia/Hong_Kong"))
    first = RunCoordinator(
        market="HK",
        deployment=deployment,
        output_root=tmp_path,
        now=now,
    )
    assert first.acquire().attempt == 1
    first.mark_failed("test failure")

    early = RunCoordinator(
        market="HK",
        deployment=deployment,
        output_root=tmp_path,
        now=now + timedelta(minutes=4, seconds=59),
    ).acquire()
    assert early.action == "RETRY_DELAY_ACTIVE"

    retry = RunCoordinator(
        market="HK",
        deployment=deployment,
        output_root=tmp_path,
        now=now + timedelta(minutes=5),
    )
    retry_decision = retry.acquire()
    assert retry_decision.should_run is True
    assert retry_decision.attempt == 2
    retry.mark_failed("second failure")

    exhausted = RunCoordinator(
        market="HK",
        deployment=deployment,
        output_root=tmp_path,
        now=now + timedelta(minutes=11),
    ).acquire()
    assert exhausted.action == "RETRY_EXHAUSTED"


def test_cli_second_invocation_does_not_scan_again(tmp_path, monkeypatch):
    from research.northstar_d1 import runner as runner_module

    calls = {"count": 0}

    def fake_run_market(market, symbols=None):
        calls["count"] += 1
        return _successful_empty_payload()

    monkeypatch.setattr(runner_module, "run_market", fake_run_market)
    coordinator_class = runner_module.RunCoordinator

    def fixed_coordinator(**kwargs):
        return coordinator_class(
            **kwargs,
            now=datetime(
                2026,
                8,
                4,
                16,
                20,
                tzinfo=ZoneInfo("Asia/Hong_Kong"),
            ),
        )

    monkeypatch.setattr(runner_module, "RunCoordinator", fixed_coordinator)
    args = ["--output-root", str(tmp_path)]
    assert main_for_market("HK", args) == 0
    assert main_for_market("HK", args) == 0
    assert calls["count"] == 1

    markdown = next((tmp_path / "hk" / "runs").rglob("*.md"))
    text = markdown.read_text(encoding="utf-8")
    assert "RUN_PASS" in text
    assert "NOT_EVALUATED" in text
    assert "运行通过只代表数据与安全门禁通过" in text


def test_cli_early_backstop_does_not_scan(tmp_path, monkeypatch):
    from research.northstar_d1 import runner as runner_module

    calls = {"count": 0}

    def fake_run_market(market, symbols=None):
        calls["count"] += 1
        return _successful_empty_payload()

    coordinator_class = runner_module.RunCoordinator

    def early_coordinator(**kwargs):
        return coordinator_class(
            **kwargs,
            now=datetime(
                2026,
                8,
                4,
                11,
                10,
                tzinfo=ZoneInfo("Asia/Hong_Kong"),
            ),
        )

    monkeypatch.setattr(runner_module, "run_market", fake_run_market)
    monkeypatch.setattr(runner_module, "RunCoordinator", early_coordinator)

    assert main_for_market("HK", ["--output-root", str(tmp_path)]) == 0
    assert calls["count"] == 0
    assert not (tmp_path / "hk" / ".runtime" / "latest.json").exists()
    assert not (tmp_path / "hk" / ".runtime" / "active.lock").exists()


def test_cli_lock_release_failure_records_failure_without_success_event(
    tmp_path,
    monkeypatch,
):
    from research.northstar_d1 import runner as runner_module

    coordinator_class = runner_module.RunCoordinator

    def fixed_coordinator(**kwargs):
        return coordinator_class(
            **kwargs,
            now=datetime(
                2026,
                8,
                4,
                16,
                20,
                tzinfo=ZoneInfo("Asia/Hong_Kong"),
            ),
        )

    def fail_lock_release(self, *, required=False):
        raise OSError("simulated sandbox lock-release failure")

    monkeypatch.setattr(
        runner_module,
        "run_market",
        lambda market, symbols=None: _successful_empty_payload(),
    )
    monkeypatch.setattr(runner_module, "RunCoordinator", fixed_coordinator)
    monkeypatch.setattr(
        coordinator_class,
        "_release_owned_lock",
        fail_lock_release,
    )

    assert main_for_market("HK", ["--output-root", str(tmp_path)]) == 1

    runtime_dir = tmp_path / "hk" / ".runtime"
    state = json.loads(
        (runtime_dir / "latest.json").read_text(encoding="utf-8")
    )
    assert state["status"] == "FAILED"
    assert state["exit_code"] == 1
    assert "simulated sandbox lock-release failure" in state["reason"]
    assert state["lock_release"]["status"] == "FAILED"
    events = [
        json.loads(line)["event"]
        for line in (runtime_dir / "events.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert events == ["RUN_STARTED", "RUN_FAILED"]


def test_model_blocks_unproven_non_futu_data(monkeypatch):
    model = NorthstarD1Model()
    candidate = model.decide(
        _trend(cross_long_up=True), _structure(), _td()
    )
    monkeypatch.setattr(model, "decide", lambda *_args: candidate)

    result = model.analyze(_bars(), symbol="TEST.HK", market="HK")
    assert result["data_fresh"] is False
    assert result["signal_eligible"] is False
    assert result["status"] == "BLOCKED"
    assert result["action"] == "HOLD"
    assert result["raw_signal_fraction"] == 0.40
    assert result["signal_fraction"] == 0.0
    assert "FUTU_LIVE_FRESHNESS_BLOCKED" in result["status_codes"]
    audit = result["decision_audit"]["decision"]
    assert audit["candidate_action"] == "BUY"
    assert audit["emitted_action"] == "HOLD"
    assert audit["emission_status"] == "BLOCKED_TO_HOLD"
    assert audit["signal_eligible"] is False
    assert audit["eligibility_override_codes"] == [
        "FUTU_LIVE_FRESHNESS_BLOCKED"
    ]


def test_backtest_is_close_timed_and_research_only():
    result = backtest_long_only(_bars(), transaction_cost_bps=10.0)
    assert result["mode"] == "RESEARCH_ONLY"
    assert result["backtest_type"] == "IN_SAMPLE_DIAGNOSTIC"
    assert result["lookahead"] is False
    assert result["signal_timing"] == "close_t_decision_applies_after_close_t"
    assert result["bars"] == 61
    assert np.isfinite(result["total_return"])
    assert np.isfinite(result["max_drawdown"])
