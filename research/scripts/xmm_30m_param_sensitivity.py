#!/usr/bin/env python3
"""
30m XMM parameter sensitivity study.

Research-only workflow:
- fetch HK/US 30m bars once
- compute xmm_30m for multiple short/long parameter pairs
- evaluate raw trend-following interpretation and inverted overheat/reversal
  interpretation across IC and quantile-return metrics
"""

from __future__ import annotations

import argparse
import math
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
for path in [ROOT, ROOT / "research"]:
    p = str(path)
    if p not in sys.path:
        sys.path.insert(0, p)

from research.data.futu_provider import FutuProvider
from research.data.symbol_registry import SymbolRegistry
from research.factors.technical.xmm_30m import XMM30mFactor
from research.scripts.xmm_30m_ic_study import (
    DEFAULT_HORIZONS,
    OUTPUT_DIR,
    REPORT_DIR,
    cross_sectional_ic,
    fmt_float,
    fmt_pct,
    forward_returns,
    pooled_summary,
)


DEFAULT_PARAM_GRID = [(12, 48), (20, 80), (25, 90)]


def parse_param_grid(values: list[str]) -> list[tuple[int, int]]:
    grid = []
    for value in values:
        if "/" not in value:
            raise ValueError(f"参数格式应为 short/long: {value}")
        short, long = value.split("/", 1)
        grid.append((int(short), int(long)))
    return grid


def fetch_market_data(market: str, symbols: list[str], count: int) -> tuple[dict, pd.DataFrame, dict[str, str]]:
    provider = FutuProvider()
    data_map = {}
    close_map = {}
    latest = {}

    print(f"\n## Fetch {market} symbols={len(symbols)} count={count}", flush=True)
    for sym in symbols:
        try:
            kdata = provider.fetch_klines(sym, count=count, timeframe="30m")
            df = kdata.df.copy()
            dt_index = pd.to_datetime(df["date"])
            close_map[sym] = pd.Series(df["close"].astype(float).values, index=dt_index, name=sym)
            data_map[sym] = kdata
            latest[sym] = str(dt_index.iloc[-1])
            print(f"{market} {sym:>6} bars={len(df):3d} latest={latest[sym]}", flush=True)
        except Exception as exc:
            print(f"{market} {sym:>6} ERROR {type(exc).__name__}: {exc}", flush=True)

    return data_map, pd.DataFrame(close_map).sort_index(), latest


def compute_factor_frame(data_map: dict, short: int, long: int, lookback: int, min_bars: int) -> pd.DataFrame:
    factor = XMM30mFactor()
    factor_map = {}
    for sym, kdata in data_map.items():
        df = kdata.df
        dt_index = pd.to_datetime(df["date"])
        series = factor.compute(
            kdata,
            short_period=short,
            long_period=long,
            lookback=lookback,
            min_bars=min_bars,
        )
        series.index = dt_index
        factor_map[sym] = series.rename(sym)
    return pd.DataFrame(factor_map).sort_index()


def evaluate_factor(market: str, config: str, orientation: str, factor_df: pd.DataFrame, close_df: pd.DataFrame, horizons: list[int]) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows_cs = []
    rows_pooled = []
    for horizon in horizons:
        returns = forward_returns(close_df, horizon)
        cs = cross_sectional_ic(factor_df, returns, horizon)
        pooled = pooled_summary(factor_df, returns, horizon)
        for row in (cs, pooled):
            row["market"] = market
            row["config"] = config
            row["orientation"] = orientation
        rows_cs.append(cs)
        rows_pooled.append(pooled)
    return pd.DataFrame(rows_cs), pd.DataFrame(rows_pooled)


def summarize_best(pooled_df: pd.DataFrame, cs_df: pd.DataFrame) -> pd.DataFrame:
    merged = pooled_df.merge(
        cs_df[["market", "config", "orientation", "horizon", "rank_ic_mean", "rank_ic_ir"]],
        on=["market", "config", "orientation", "horizon"],
        how="left",
    )
    merged["abs_spread"] = merged["q5_minus_q1"].abs()
    return merged.sort_values(["market", "horizon", "q5_minus_q1"], ascending=[True, True, False])


def write_report(
    report_path: Path,
    summary_df: pd.DataFrame,
    cs_df: pd.DataFrame,
    pooled_df: pd.DataFrame,
    latest_rows: list[dict],
    count: int,
    horizons: list[int],
    elapsed: float,
) -> None:
    lines = []
    lines.append("# 30分钟 XMM 参数敏感性验证")
    lines.append("")
    lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} Asia/Shanghai")
    lines.append(f"样本长度: 每标的最多 {count} 根 30m K 线")
    lines.append(f"持有期: {', '.join(str(h) + ' bars' for h in horizons)}")
    lines.append(f"执行耗时: {elapsed:.1f}s")
    lines.append("")
    lines.append("> `raw` 表示趋势跟随解释；`inverse` 表示短线过热/反转解释。")
    lines.append("")

    latest_df = pd.DataFrame(latest_rows)
    lines.append("## 最新分数")
    lines.append("")
    lines.append("| 市场 | 参数 | 标的 | Raw 分数 | 最新K线 |")
    lines.append("|---|---|---|---:|---|")
    for _, row in latest_df.sort_values(["market", "config", "raw_score"], ascending=[True, True, False]).iterrows():
        lines.append(f"| {row['market']} | {row['config']} | {row['symbol']} | {row['raw_score']:+.2f} | {row['latest_bar']} |")
    lines.append("")

    for market in sorted(summary_df["market"].unique()):
        lines.append(f"## {market} 每个 horizon 的最佳方向")
        lines.append("")
        lines.append("| Horizon | 参数 | 方向 | RankIC Mean | RankIC IR | Q5-Q1 | Hit Rate | N obs |")
        lines.append("|---:|---|---|---:|---:|---:|---:|---:|")
        for horizon in horizons:
            subset = summary_df[(summary_df["market"] == market) & (summary_df["horizon"] == horizon)].copy()
            if subset.empty:
                continue
            best = subset.sort_values("q5_minus_q1", ascending=False).iloc[0]
            lines.append(
                f"| {horizon} | {best['config']} | {best['orientation']} | "
                f"{fmt_float(best['rank_ic_mean'])} | {fmt_float(best['rank_ic_ir'])} | "
                f"{fmt_pct(best['q5_minus_q1'])} | {best['direction_hit_rate']*100:.1f}% | {int(best['n_obs'])} |"
            )
        lines.append("")

    lines.append("## 完整分层收益")
    lines.append("")
    lines.append("| 市场 | 参数 | 方向 | Horizon | RankIC Mean | Pooled RankIC | Q1 | Q5 | Q5-Q1 |")
    lines.append("|---|---|---|---:|---:|---:|---:|---:|---:|")
    for _, row in summary_df.sort_values(["market", "config", "orientation", "horizon"]).iterrows():
        lines.append(
            f"| {row['market']} | {row['config']} | {row['orientation']} | {int(row['horizon'])} | "
            f"{fmt_float(row['rank_ic_mean'])} | {fmt_float(row['pooled_rank_ic'])} | "
            f"{fmt_pct(row['q1_mean'])} | {fmt_pct(row['q5_mean'])} | {fmt_pct(row['q5_minus_q1'])} |"
        )
    lines.append("")

    lines.append("## 研究判断规则")
    lines.append("")
    lines.append("- 若 `raw` 的 RankIC 和 Q5-Q1 持续为正，说明更像趋势确认。")
    lines.append("- 若 `inverse` 持续为正，说明原始高分更像短线过热，应该反向解释。")
    lines.append("- 单个 horizon 有利但其它 horizon 不稳定，只能作为观察信号，不能进入生产权重。")
    lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=220)
    parser.add_argument("--markets", nargs="+", default=["HK", "US"], choices=["HK", "US"])
    parser.add_argument("--horizons", nargs="+", type=int, default=DEFAULT_HORIZONS)
    parser.add_argument("--params", nargs="+", default=[f"{s}/{l}" for s, l in DEFAULT_PARAM_GRID])
    parser.add_argument("--lookback", type=int, default=160)
    parser.add_argument("--min-bars", type=int, default=100)
    parser.add_argument("--max-symbols", type=int, default=0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started = datetime.now()
    ts = started.strftime("%Y%m%d_%H%M")
    param_grid = parse_param_grid(args.params)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    registry = SymbolRegistry()
    all_cs = []
    all_pooled = []
    latest_rows = []

    for market in args.markets:
        symbols = [s.symbol for s in registry.get_symbols(market)]
        if args.max_symbols:
            symbols = symbols[:args.max_symbols]
        data_map, close_df, latest = fetch_market_data(market, symbols, args.count)
        close_df.to_csv(OUTPUT_DIR / f"xmm_30m_sensitivity_close_{market}_{ts}.csv", encoding="utf-8-sig")

        for short, long in param_grid:
            config = f"{short}/{long}"
            print(f"\n## Compute {market} config={config}", flush=True)
            raw_factor = compute_factor_frame(data_map, short, long, args.lookback, args.min_bars)
            raw_factor.to_csv(OUTPUT_DIR / f"xmm_30m_sensitivity_factor_{market}_{config.replace('/', '_')}_{ts}.csv", encoding="utf-8-sig")

            for sym in raw_factor.columns:
                valid = raw_factor[sym].dropna()
                latest_rows.append({
                    "market": market,
                    "config": config,
                    "symbol": sym,
                    "raw_score": float(valid.iloc[-1]) if len(valid) else math.nan,
                    "valid_factor_bars": int(len(valid)),
                    "latest_bar": latest.get(sym, ""),
                })

            for orientation, factor_df in [("raw", raw_factor), ("inverse", -raw_factor)]:
                cs, pooled = evaluate_factor(market, config, orientation, factor_df, close_df, args.horizons)
                all_cs.append(cs)
                all_pooled.append(pooled)

    cs_df = pd.concat(all_cs, ignore_index=True)
    pooled_df = pd.concat(all_pooled, ignore_index=True)
    summary_df = summarize_best(pooled_df, cs_df)

    cs_path = OUTPUT_DIR / f"xmm_30m_sensitivity_cs_ic_{ts}.csv"
    pooled_path = OUTPUT_DIR / f"xmm_30m_sensitivity_pooled_{ts}.csv"
    summary_path = OUTPUT_DIR / f"xmm_30m_sensitivity_summary_{ts}.csv"
    latest_path = OUTPUT_DIR / f"xmm_30m_sensitivity_latest_{ts}.csv"
    cs_df.to_csv(cs_path, index=False, encoding="utf-8-sig")
    pooled_df.to_csv(pooled_path, index=False, encoding="utf-8-sig")
    summary_df.to_csv(summary_path, index=False, encoding="utf-8-sig")
    pd.DataFrame(latest_rows).to_csv(latest_path, index=False, encoding="utf-8-sig")

    report_path = REPORT_DIR / f"xmm_30m_param_sensitivity_{ts}.md"
    elapsed = (datetime.now() - started).total_seconds()
    write_report(report_path, summary_df, cs_df, pooled_df, latest_rows, args.count, args.horizons, elapsed)

    print(f"\nREPORT={report_path}", flush=True)
    print(f"SUMMARY={summary_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
