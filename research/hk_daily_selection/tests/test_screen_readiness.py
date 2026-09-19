"""Fail-close tests for formal daily-screen candidate publication."""

from __future__ import annotations

import pandas as pd

from hk_daily_selection.config import StrategyConfig
from hk_daily_selection.reporting import write_daily_report
from hk_daily_selection.selector import (
    _select_screen_universe,
    run_selection,
)
from hk_daily_selection.synthetic import make_synthetic_bundle


def _ready_bundle() -> tuple[pd.DataFrame, pd.DataFrame, pd.Timestamp]:
    bars, membership = make_synthetic_bundle(
        StrategyConfig(), periods=180
    )
    backtest = run_selection(bars, membership, StrategyConfig())
    eligible_dates = backtest.scores.loc[
        backtest.scores["eligible"], "date"
    ]
    anchor = pd.Timestamp(
        eligible_dates.max()
        if not eligible_dates.empty
        else bars.loc[
            bars["symbol"] == StrategyConfig().benchmark_symbol, "date"
        ].max()
    )
    return bars, membership, anchor


def test_complete_screen_can_reach_research_grade_posture() -> None:
    bars, membership, anchor = _ready_bundle()
    run = run_selection(
        bars,
        membership,
        StrategyConfig(),
        mode="screen",
        session_date=anchor,
    )

    assert run.publish_candidates
    assert run.evidence_posture == "RESEARCH_GRADE_INPUT_CONTRACT"
    assert set(run.scores["data_readiness"]) == {"READY"}
    assert set(run.scores["formal_publish_allowed"]) == {True}


def test_low_bar_coverage_is_partial_and_never_publishes_a_class() -> None:
    bars, membership, anchor = _ready_bundle()
    stock_symbols = sorted(
        symbol
        for symbol in bars["symbol"].unique()
        if symbol != StrategyConfig().benchmark_symbol
    )
    keep = stock_symbols[0]
    missing_anchor = (
        (bars["date"] == anchor)
        & bars["symbol"].isin(stock_symbols)
        & bars["symbol"].ne(keep)
    )
    bars = bars.loc[~missing_anchor].copy()

    run = run_selection(
        bars,
        membership,
        StrategyConfig(),
        mode="screen",
        session_date=anchor,
    )

    assert not run.publish_candidates
    assert run.evidence_posture == "PARTIAL / DATA_READINESS"
    assert int(run.scores["selected"].sum()) == 0
    assert int((run.scores["priority"] == "NotEvaluated").sum()) == 9
    assert any("completed bars coverage" in item for item in run.quality.warnings)


def test_missing_sector_suspension_or_action_evidence_fail_closes() -> None:
    bars, membership, anchor = _ready_bundle()
    cases = {
        "sector": lambda frame: frame.assign(sector="", sector_source=""),
        "suspension": lambda frame: frame.assign(
            snapshot_is_suspended=pd.NA,
            suspension_source="",
            market_snapshot_observed_at_hkt=pd.NaT,
        ),
        "corporate_action": lambda frame: frame.assign(
            corporate_action_status="UNKNOWN",
            corporate_action_observed_at_hkt=pd.NaT,
        ),
    }

    for mutate in cases.values():
        run = run_selection(
            bars,
            mutate(membership.copy()),
            StrategyConfig(),
            mode="screen",
            session_date=anchor,
        )
        assert not run.publish_candidates
        assert run.evidence_posture == "PARTIAL / DATA_READINESS"
        assert int(run.scores["selected"].sum()) == 0


def test_partial_report_separates_diagnostics_from_formal_candidates(
    tmp_path,
) -> None:
    bars, membership, anchor = _ready_bundle()
    membership = membership.assign(sector="", sector_source="")
    run = run_selection(
        bars,
        membership,
        StrategyConfig(),
        mode="screen",
        session_date=anchor,
    )
    report = write_daily_report(
        run.scores,
        anchor,
        tmp_path / "daily.md",
        StrategyConfig(),
        source_posture=run.evidence_posture,
        evidence_warnings=run.quality.warnings,
    ).read_text(encoding="utf-8")

    assert "正式候选发布：禁止" in report
    assert "A 类研究候选：0 只" in report
    assert "诊断排名（不可发布为 A 类）" in report
    assert "NotEvaluated（数据缺失）" in report


def test_report_uses_regime_specific_candidate_limit(tmp_path) -> None:
    bars, membership, anchor = _ready_bundle()
    config = StrategyConfig()
    run = run_selection(
        bars,
        membership,
        config,
        mode="screen",
        session_date=anchor,
    )
    report = write_daily_report(
        run.scores,
        anchor,
        tmp_path / "daily.md",
        config,
        source_posture=run.evidence_posture,
        evidence_warnings=run.quality.warnings,
    ).read_text(encoding="utf-8")
    regime = str(run.scores["regime"].iloc[0])
    expected_limit = {
        "ON": config.top_k,
        "CAUTION": config.caution_top_k,
        "OFF": 0,
        "UNKNOWN": 0,
    }[regime]

    assert f"上限 {expected_limit}" in report


def test_screen_universe_is_ranked_top_50_not_first_50_rows() -> None:
    config = StrategyConfig(screen_universe_top_n=50)
    symbols = [f"{index:05d}.HK" for index in range(1, 61)]
    members = pd.DataFrame(
        {
            "symbol": list(reversed(symbols)),
            "screen_universe_rank": list(reversed(range(1, 61))),
            "total_market_val": list(range(1, 61)),
        }
    )
    universe, metadata = _select_screen_universe(members, config)

    assert len(universe) == 50
    assert set(universe["screen_universe_rank"]) == set(range(1, 51))
    assert metadata["parent_universe_size"] == 60
    assert metadata["universe_definition"] == (
        "HSCI_TOP_50_BY_TOTAL_MARKET_VAL_ASOF"
    )


def test_recent_action_requires_same_day_qfq_for_candidate_eligibility() -> None:
    bars, membership, anchor = _ready_bundle()
    target = membership.iloc[0]["symbol"]
    membership = membership.copy()
    membership.loc[
        membership["symbol"] == target, "has_recent_corporate_action"
    ] = True
    membership.loc[
        membership["symbol"] == target, "corporate_action_status"
    ] = "RECENT_ACTION"

    none_run = run_selection(
        bars.assign(adjustment="NONE"),
        membership,
        StrategyConfig(),
        mode="screen",
        session_date=anchor,
    )
    none_reason = none_run.scores.loc[
        none_run.scores["symbol"] == target, "rejection_reason"
    ].iloc[0]
    assert "recent_corporate_action" in none_reason

    qfq_run = run_selection(
        bars.assign(adjustment="QFQ"),
        membership,
        StrategyConfig(),
        mode="screen",
        session_date=anchor,
    )
    qfq_reason = qfq_run.scores.loc[
        qfq_run.scores["symbol"] == target, "rejection_reason"
    ].iloc[0]
    assert "recent_corporate_action" not in qfq_reason
