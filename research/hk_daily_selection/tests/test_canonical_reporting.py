from __future__ import annotations

from argparse import Namespace

import pandas as pd

from hk_daily_selection.canonical_reporting import (
    write_canonical_daily_report,
)
from hk_daily_selection.cli import _cmd_screen
from hk_daily_selection.config import StrategyConfig
from hk_daily_selection.synthetic import make_synthetic_bundle


def _report_scores() -> pd.DataFrame:
    date = pd.Timestamp("2026-08-05")
    rows: list[dict[str, object]] = []
    ranks = [1, 2, 3, 5, 7, 4, 10]
    symbols = [
        "00005.HK",
        "00300.HK",
        "02888.HK",
        "09618.HK",
        "02359.HK",
        "03968.HK",
        "03690.HK",
    ]
    for index, (rank, symbol) in enumerate(zip(ranks, symbols)):
        selected = index < 5
        rows.append(
            {
                "date": date,
                "symbol": symbol,
                "name": f"Name {symbol}",
                "sector": "银行" if symbol in {"00005.HK", "02888.HK", "03968.HK"} else "其他",
                "eligible": True,
                "selected": selected,
                "priority": "A" if selected else "B",
                "rank": float(rank),
                "composite_score": 0.90 - index * 0.04,
                "momentum_score": 0.81,
                "relative_strength_score": 0.72,
                "trend_quality_score": 0.83,
                "participation_score": 0.64,
                "risk_score": 0.75,
                "entry_quality_score": 0.32,
                "close": 97.55,
                "ma_20": 94.35,
                "ma_60": 87.81,
                "realized_vol_20": 0.176,
                "atr_20": 1.9325 if symbol == "00300.HK" else 3.55,
                "extension_atr_20": 1.6572 if symbol == "00300.HK" else 2.7493,
                "turnover_ratio_5_20": 1.125,
                "corporate_action_status": "RECENT_ACTION",
                "has_recent_corporate_action": True,
                "last_corporate_action_date": (
                    pd.Timestamp("2026-08-05")
                    if symbol == "02888.HK"
                    else pd.Timestamp("2026-05-13")
                ),
                "snapshot_is_suspended": False,
                "volume": 1_000_000,
                "adjustment": "QFQ",
                "fetched_at_hkt": "2026-08-05T16:34:28+08:00",
                "rejection_reason": "",
                "selection_reason": (
                    "selected_under_constraints"
                    if selected
                    else (
                        "sector_limit"
                        if symbol == "03968.HK"
                        else "candidate_limit"
                    )
                ),
                "regime": "ON",
                "breadth_above_ma20": 0.80,
                "benchmark_close": 3832.67,
                "benchmark_ma_20": 3702.67,
                "benchmark_ma_60": 3697.17,
                "parent_universe_size": 531,
                "screen_universe_size": 50,
                "universe_definition": "HSCI_TOP_50_BY_TOTAL_MARKET_VAL_ASOF",
                "universe_ranking_coverage_ratio": 1.0,
                "bar_coverage_ratio": 1.0,
                "breadth_coverage_ratio": 1.0,
                "sector_coverage_ratio": 1.0,
                "suspension_coverage_ratio": 1.0,
                "corporate_action_coverage_ratio": 1.0,
                "breadth_sample_count": 50,
                "data_readiness": "READY",
                "formal_publish_allowed": True,
            }
        )
    return pd.DataFrame(rows)


def test_canonical_report_uses_raw_rank_metrics_and_reasons(tmp_path) -> None:
    report = write_canonical_daily_report(
        _report_scores(),
        pd.Timestamp("2026-08-05"),
        tmp_path / "main.md",
        StrategyConfig(),
        source_posture="RESEARCH_GRADE_INPUT_CONTRACT",
        generated_at_hkt="2026-08-05T16:38:07+08:00",
        input_metadata={"bars SHA256": "abc123"},
    ).read_text(encoding="utf-8")

    assert "| 4 | 5 | 09618.HK" in report
    assert "| 00300.HK | 97.55 | 94.35 | 87.81 | 17.6% | 1.9325 | 1.6572" in report
    assert "| 4 | 03968.HK" in report and "行业上限已满" in report
    assert "| 10 | 03690.HK" in report and "A 类名额已满" in report
    assert "02888.HK Name 02888.HK | RECENT_ACTION | 是 | 2026-08-05" in report
    assert "True=0，False=7，Unknown=0" in report
    assert "适合已在车上的持仓者" not in report
    assert "中小盘" not in report


def test_cli_can_write_deterministic_main_report(tmp_path) -> None:
    bars, membership = make_synthetic_bundle(StrategyConfig(), periods=180)
    anchor = pd.Timestamp(bars["date"].max())
    bars_path = tmp_path / "bars.csv"
    membership_path = tmp_path / "membership.csv"
    output_dir = tmp_path / "evidence"
    main_report = tmp_path / "HK每日优选.md"
    bars.to_csv(bars_path, index=False)
    membership.to_csv(membership_path, index=False)
    args = Namespace(
        bars=str(bars_path),
        membership=str(membership_path),
        config=None,
        output_dir=str(output_dir),
        allow_survivorship_bias=False,
        as_of=str(anchor.date()),
        main_report=str(main_report),
    )

    assert _cmd_screen(args) == 0
    assert main_report.exists()
    report = main_report.read_text(encoding="utf-8")
    assert "同源确定性生成" in report
    assert "bars SHA256" in report
    assert "综述结论" in report
    scores = pd.read_csv(output_dir / "daily_scores.csv")
    assert "selection_reason" in scores.columns
