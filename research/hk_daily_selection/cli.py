"""Command-line entry points for validation, screening, backtesting, and demo."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Sequence

import pandas as pd

from .backtest import run_backtest
from .canonical_reporting import write_canonical_daily_report
from .config import StrategyConfig
from .contracts import load_bars_csv, load_membership_csv
from .futu_readonly import (
    fetch_current_hsci_membership,
    fetch_history_bars,
)
from .reporting import write_backtest_report, write_daily_report
from .selector import run_selection
from .synthetic import make_synthetic_bundle


def _load_config(path: str | None) -> StrategyConfig:
    if not path:
        return StrategyConfig()
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return StrategyConfig(**payload)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _screen_input_metadata(
    args: argparse.Namespace,
    bars: pd.DataFrame,
    membership: pd.DataFrame,
) -> dict[str, object]:
    bars_path = Path(args.bars).resolve()
    membership_path = Path(args.membership).resolve()
    dates = pd.to_datetime(bars["date"], errors="coerce").dropna()
    repaired = bars.get(
        "ohlc_envelope_repaired", pd.Series(False, index=bars.index)
    )
    metadata: dict[str, object] = {
        "bars 路径": str(bars_path),
        "bars SHA256": _sha256(bars_path),
        "membership 路径": str(membership_path),
        "membership SHA256": _sha256(membership_path),
        "bars 行数/代码数": f"{len(bars)}/{bars['symbol'].nunique()}",
        "bars 日期范围": (
            f"{dates.min().date()} 至 {dates.max().date()}"
            if not dates.empty
            else "N/A"
        ),
        "date+symbol 重复": int(bars.duplicated(["date", "symbol"]).sum()),
        "OHLC 包络修复记录": int(repaired.fillna(False).astype(bool).sum()),
        "membership 记录": len(membership),
    }
    config_path_text = getattr(args, "config", None)
    if config_path_text:
        config_path = Path(config_path_text).resolve()
        metadata["config 路径"] = str(config_path)
        metadata["config SHA256"] = _sha256(config_path)
    else:
        metadata["config"] = "StrategyConfig 内置默认值"
    return metadata


def _write_selection_outputs(
    scores: pd.DataFrame,
    output_dir: Path,
    config: StrategyConfig,
    as_of: pd.Timestamp,
    source_posture: str,
    warnings: Sequence[str],
    *,
    main_report_path: Path | None = None,
    input_metadata: dict[str, object] | None = None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    scores.to_csv(output_dir / "daily_scores.csv", index=False)
    write_daily_report(
        scores,
        as_of,
        output_dir / f"daily_candidates_{as_of.date()}.md",
        config,
        source_posture=source_posture,
        evidence_warnings=warnings,
    )
    if main_report_path is not None:
        write_canonical_daily_report(
            scores,
            as_of,
            main_report_path,
            config,
            source_posture=source_posture,
            evidence_warnings=warnings,
            generated_at_hkt=pd.Timestamp.now(tz="Asia/Hong_Kong"),
            input_metadata=input_metadata,
        )


def _cmd_screen(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    bars = load_bars_csv(args.bars)
    membership = load_membership_csv(args.membership)
    if args.as_of:
        as_of = pd.Timestamp(args.as_of).normalize()
    else:
        benchmark_dates = bars.loc[
            bars["symbol"] == config.benchmark_symbol, "date"
        ]
        available_dates = (
            benchmark_dates if not benchmark_dates.empty else bars["date"]
        )
        if available_dates.empty:
            raise ValueError("bars contain no dates for screen")
        as_of = pd.Timestamp(available_dates.max()).normalize()
    run = run_selection(
        bars,
        membership,
        config,
        allow_survivorship_bias=args.allow_survivorship_bias,
        mode="screen",
        session_date=as_of,
    )
    _write_selection_outputs(
        run.scores,
        Path(args.output_dir),
        config,
        as_of,
        run.evidence_posture,
        run.quality.warnings,
        main_report_path=(
            Path(args.main_report)
            if getattr(args, "main_report", None)
            else None
        ),
        input_metadata=_screen_input_metadata(args, bars, membership),
    )
    return 0


def _cmd_backtest(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    bars = load_bars_csv(args.bars)
    membership = load_membership_csv(args.membership)
    selection = run_selection(
        bars,
        membership,
        config,
        allow_survivorship_bias=args.allow_survivorship_bias,
        mode="backtest",
    )
    result = run_backtest(
        selection.scores,
        bars,
        config,
        start=pd.Timestamp(args.start) if args.start else None,
        end=pd.Timestamp(args.end) if args.end else None,
        warnings=selection.quality.warnings,
    )
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    selection.scores.to_csv(output / "daily_scores.csv", index=False)
    result.equity_curve.to_csv(output / "equity_curve.csv")
    result.trades.to_csv(output / "trades.csv", index=False)
    result.forward_metrics.to_csv(output / "forward_metrics.csv", index=False)
    posture = (
        "PRELIMINARY / SURVIVORSHIP_BIASED"
        if args.allow_survivorship_bias
        else "PRELIMINARY / POINT_IN_TIME_CONTRACT_ENFORCED"
    )
    write_backtest_report(
        result,
        output / "backtest_summary.md",
        config,
        label=args.label or "USER_DATA",
        source_posture=posture,
    )
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    bars, membership = make_synthetic_bundle(config)
    selection = run_selection(bars, membership, config)
    result = run_backtest(
        selection.scores,
        bars,
        config,
        warnings=("SYNTHETIC_DATA: code-path demonstration only",),
    )
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    bars.to_csv(output / "synthetic_bars.csv", index=False)
    membership.to_csv(output / "synthetic_membership.csv", index=False)
    selection.scores.to_csv(output / "synthetic_scores.csv", index=False)
    result.equity_curve.to_csv(output / "synthetic_equity_curve.csv")
    result.trades.to_csv(output / "synthetic_trades.csv", index=False)
    result.forward_metrics.to_csv(
        output / "synthetic_forward_metrics.csv", index=False
    )
    as_of = pd.Timestamp(selection.scores["date"].max())
    write_daily_report(
        selection.scores,
        as_of,
        output / f"synthetic_daily_candidates_{as_of.date()}.md",
        config,
        source_posture="SYNTHETIC_DEMO_ONLY",
        evidence_warnings=("No real security or market data are used.",),
    )
    selected_dates = selection.scores.loc[
        selection.scores["selected"], "date"
    ]
    if not selected_dates.empty:
        example_as_of = pd.Timestamp(selected_dates.max())
        if example_as_of != as_of:
            write_daily_report(
                selection.scores,
                example_as_of,
                output
                / (
                    "synthetic_daily_candidates_example_"
                    f"{example_as_of.date()}.md"
                ),
                config,
                source_posture="SYNTHETIC_DEMO_ONLY / EXAMPLE_WITH_CANDIDATES",
                evidence_warnings=(
                    "Example date is selected only to demonstrate report layout.",
                    "No real security or market data are used.",
                ),
            )
    write_backtest_report(
        result,
        output / "synthetic_backtest_summary.md",
        config,
        label="SYNTHETIC_DEMO_ONLY",
        source_posture="NOT_EVIDENCE_OF_ALPHA",
    )
    return 0


def _cmd_futu_universe(args: argparse.Namespace) -> int:
    membership = fetch_current_hsci_membership(
        host=args.host,
        port=args.port,
        index_code=args.index_code,
        observed_asof=pd.Timestamp(args.as_of) if args.as_of else None,
        screen_top_n=args.screen_top_n,
        recent_action_lookback_days=args.recent_action_lookback_days,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    membership.to_csv(output, index=False)
    return 0


def _cmd_futu_history(args: argparse.Namespace) -> int:
    symbols = [
        line.strip()
        for line in Path(args.symbols).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    bars = fetch_history_bars(
        symbols,
        start=pd.Timestamp(args.start),
        completed_session=pd.Timestamp(args.completed_session),
        host=args.host,
        port=args.port,
        adjustment=args.adjustment,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    bars.to_csv(output, index=False)
    return 0


def _cmd_futu_screen_cache(args: argparse.Namespace) -> int:
    """Build one complete read-only daily-screen cache from Futu evidence."""

    config = _load_config(args.config)
    top_n = args.screen_top_n or config.screen_universe_top_n
    lookback_days = (
        args.recent_action_lookback_days
        or config.recent_corporate_action_lookback_days
    )
    session = pd.Timestamp(args.completed_session).normalize()
    membership_output = Path(args.membership_output)
    if args.reuse_membership and membership_output.exists():
        membership = load_membership_csv(membership_output)
    else:
        membership = fetch_current_hsci_membership(
            host=args.host,
            port=args.port,
            index_code=args.index_code,
            observed_asof=session,
            screen_top_n=top_n,
            recent_action_lookback_days=lookback_days,
        )
    stocks = membership[
        membership["security_type"].isin(config.allowed_security_types)
    ].copy()
    ranked = stocks[
        stocks["screen_universe_rank"].notna()
        & (stocks["screen_universe_rank"] >= 1)
        & (stocks["screen_universe_rank"] <= top_n)
    ].sort_values(["screen_universe_rank", "symbol"])
    if len(ranked) != min(top_n, len(stocks)):
        raise ValueError(
            "cannot build a complete ranked screen universe: "
            f"expected {min(top_n, len(stocks))}, got {len(ranked)}"
        )
    symbols = [config.benchmark_symbol, *ranked["symbol"].tolist()]

    # Persist the fully evidenced universe before the slower history stage so
    # a downstream bar-quality failure can be investigated without repeating
    # market-cap, sector, suspension, and rehab calls.
    membership_output.parent.mkdir(parents=True, exist_ok=True)
    membership.to_csv(membership_output, index=False)
    if args.symbols_output:
        symbols_output = Path(args.symbols_output)
        symbols_output.parent.mkdir(parents=True, exist_ok=True)
        symbols_output.write_text(
            "\n".join(ranked["symbol"].tolist()) + "\n",
            encoding="utf-8",
        )

    bars = fetch_history_bars(
        symbols,
        start=pd.Timestamp(args.start),
        completed_session=session,
        host=args.host,
        port=args.port,
        adjustment=args.adjustment,
    )

    bars_output = Path(args.bars_output)
    bars_output.parent.mkdir(parents=True, exist_ok=True)
    bars.to_csv(bars_output, index=False)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Research-only Hong Kong daily stock selection"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common_files(command: argparse.ArgumentParser) -> None:
        command.add_argument("--bars", required=True)
        command.add_argument("--membership", required=True)
        command.add_argument("--config")
        command.add_argument("--output-dir", required=True)
        command.add_argument(
            "--allow-survivorship-bias",
            action="store_true",
            help="Run only with an explicit PRELIMINARY bias label.",
        )

    screen = subparsers.add_parser("screen")
    add_common_files(screen)
    screen.add_argument("--as-of")
    screen.add_argument(
        "--main-report",
        help=(
            "Write the deterministic user-facing Markdown report to this "
            "path. The native daily_candidates report remains in output-dir."
        ),
    )
    screen.set_defaults(func=_cmd_screen)

    backtest = subparsers.add_parser("backtest")
    add_common_files(backtest)
    backtest.add_argument("--start")
    backtest.add_argument("--end")
    backtest.add_argument("--label")
    backtest.set_defaults(func=_cmd_backtest)

    demo = subparsers.add_parser("demo")
    demo.add_argument("--config")
    demo.add_argument("--output-dir", required=True)
    demo.set_defaults(func=_cmd_demo)

    universe = subparsers.add_parser("futu-universe")
    universe.add_argument("--host", default="127.0.0.1")
    universe.add_argument("--port", type=int, default=11111)
    universe.add_argument("--index-code", default="HK.800701")
    universe.add_argument("--as-of")
    universe.add_argument("--screen-top-n", type=int, default=50)
    universe.add_argument(
        "--recent-action-lookback-days", type=int, default=90
    )
    universe.add_argument("--output", required=True)
    universe.set_defaults(func=_cmd_futu_universe)

    history = subparsers.add_parser("futu-history")
    history.add_argument("--host", default="127.0.0.1")
    history.add_argument("--port", type=int, default=11111)
    history.add_argument("--symbols", required=True)
    history.add_argument("--start", required=True)
    history.add_argument("--completed-session", required=True)
    history.add_argument(
        "--adjustment", choices=["NONE", "QFQ", "HFQ"], default="NONE"
    )
    history.add_argument("--output", required=True)
    history.set_defaults(func=_cmd_futu_history)

    screen_cache = subparsers.add_parser("futu-screen-cache")
    screen_cache.add_argument("--host", default="127.0.0.1")
    screen_cache.add_argument("--port", type=int, default=11111)
    screen_cache.add_argument("--index-code", default="HK.800701")
    screen_cache.add_argument("--config")
    screen_cache.add_argument("--start", required=True)
    screen_cache.add_argument("--completed-session", required=True)
    screen_cache.add_argument("--screen-top-n", type=int)
    screen_cache.add_argument("--recent-action-lookback-days", type=int)
    screen_cache.add_argument(
        "--adjustment", choices=["NONE", "QFQ", "HFQ"], default="QFQ"
    )
    screen_cache.add_argument("--membership-output", required=True)
    screen_cache.add_argument("--bars-output", required=True)
    screen_cache.add_argument("--symbols-output")
    screen_cache.add_argument(
        "--reuse-membership",
        action="store_true",
        help="Reuse an already evidenced membership CSV and skip quote metadata calls.",
    )
    screen_cache.set_defaults(func=_cmd_futu_screen_cache)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
