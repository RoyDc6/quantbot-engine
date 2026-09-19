from __future__ import annotations

import pandas as pd

from hk_daily_selection.config import StrategyConfig
from hk_daily_selection.selector import (
    _apply_sector_and_issuer_caps,
    _select_with_constraint_reasons,
    run_selection,
)


def test_future_bar_changes_do_not_change_prior_scores(
    synthetic_bundle,
    synthetic_selection,
) -> None:
    bars, membership = synthetic_bundle
    cutoff = pd.Timestamp(
        bars.loc[
            bars["symbol"] == StrategyConfig().benchmark_symbol,
            "date",
        ].sort_values().iloc[145]
    )
    mutated = bars.copy()
    future = mutated["date"] > cutoff
    for column in ["open", "high", "low", "close"]:
        mutated.loc[future, column] *= 1.75
    mutated.loc[future, "turnover"] *= 1.75

    changed = run_selection(mutated, membership, StrategyConfig())
    columns = [
        "symbol",
        "eligible",
        "selected",
        "rank",
        "composite_score",
        "rejection_reason",
        "regime",
    ]
    expected = (
        synthetic_selection.scores[
            synthetic_selection.scores["date"] == cutoff
        ][columns]
        .sort_values("symbol")
        .reset_index(drop=True)
    )
    actual = (
        changed.scores[changed.scores["date"] == cutoff][columns]
        .sort_values("symbol")
        .reset_index(drop=True)
    )
    pd.testing.assert_frame_equal(expected, actual)


def test_sector_and_issuer_caps_are_deterministic() -> None:
    eligible = pd.DataFrame(
        [
            ("00003.HK", "A", "I3", 0.95),
            ("00001.HK", "A", "I1", 0.95),
            ("00002.HK", "A", "I2", 0.90),
            ("00004.HK", "B", "I4", 0.89),
            ("00005.HK", "C", "I4", 0.88),
            ("00006.HK", "C", "I6", 0.87),
        ],
        columns=["symbol", "sector", "issuer_id", "composite_score"],
    )
    config = StrategyConfig(
        top_k=3,
        caution_top_k=2,
        max_per_sector=1,
        max_per_issuer=1,
    )
    selected = _apply_sector_and_issuer_caps(eligible, 3, config)
    chosen = eligible.loc[selected]

    assert chosen["symbol"].tolist() == ["00001.HK", "00004.HK", "00006.HK"]
    assert chosen.groupby("sector").size().max() == 1
    assert chosen.groupby("issuer_id").size().max() == 1


def test_constraint_reason_is_first_binding_decision() -> None:
    eligible = pd.DataFrame(
        [
            ("00001.HK", "Bank", "I1", 0.99),
            ("00002.HK", "Bank", "I2", 0.98),
            ("00003.HK", "Bank", "I3", 0.97),
            ("00004.HK", "Tech", "I4", 0.96),
            ("00005.HK", "Retail", "I5", 0.95),
        ],
        columns=["symbol", "sector", "issuer_id", "composite_score"],
    )
    config = StrategyConfig(max_per_sector=2, max_per_issuer=1)

    selected, reasons = _select_with_constraint_reasons(
        eligible, desired=3, config=config
    )

    assert eligible.loc[selected, "symbol"].tolist() == [
        "00001.HK",
        "00002.HK",
        "00004.HK",
    ]
    assert reasons[2] == "sector_limit"
    assert reasons[4] == "candidate_limit"


def test_caps_resolve_equal_scores_by_symbol() -> None:
    eligible = pd.DataFrame(
        [
            ("00003.HK", "A", "I3", 0.95),
            ("00001.HK", "B", "I1", 0.95),
            ("00002.HK", "C", "I2", 0.95),
        ],
        columns=["symbol", "sector", "issuer_id", "composite_score"],
    )

    selected, reasons = _select_with_constraint_reasons(
        eligible, desired=2, config=StrategyConfig()
    )

    assert eligible.loc[selected, "symbol"].tolist() == [
        "00001.HK",
        "00002.HK",
    ]
    assert reasons[0] == "candidate_limit"
