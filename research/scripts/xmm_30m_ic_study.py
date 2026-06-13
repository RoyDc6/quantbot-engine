#!/usr/bin/env python3
"""
30m XMM factor IC and quantile-return study for HK/US.

This is a research-only script. It fetches 30-minute Futu bars, computes the
xmm_30m factor, evaluates forward returns over several bar horizons, and writes
Markdown/CSV outputs for review.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parents[2]
for path in [ROOT, ROOT / "research"]:
    p = str(path)
    if p not in sys.path:
        sys.path.insert(0, p)

from research.data.futu_provider import FutuProvider
from research.data.symbol_registry import SymbolRegistry
from research.factors.technical.xmm_30m import XMM30mFactor


DEFAULT_HORIZONS = [1, 2, 4, 8, 13]
REPORT_DIR = Path(r"E:\AIWorkspace\02_ProjectReports")
OUTPUT_DIR = ROOT / "research" / "output"


@dataclass
class MarketData:
    market: str
    factor: pd.DataFrame
    close: pd.DataFrame
    bars: dict[str, int]
    latest: dict[str, str]
    errors: dict[str, str]


def _safe_spearman(x: pd.Series, y: pd.Series) -> float:
    valid = pd.concat([x, y], axis=1).replace([np.inf, -np.inf], np.nan).dropna()
    if len(valid) < 5 or valid.iloc[:, 0].nunique() < 2 or valid.iloc[:, 1].nunique() < 2:
        return math.nan
    value, _ = stats.spearmanr(valid.iloc[:, 0], valid.iloc[:, 1])
    return float(value) if not math.isnan(value) else math.nan


def _safe_pearson(x: pd.Series, y: pd.Series) -> float:
    valid = pd.concat([x, y], axis=1).replace([np.inf, -np.inf], np.nan).dropna()
    if len(valid) < 5 or valid.iloc[:, 0].nunique() < 2 or valid.iloc[:, 1].nunique() < 2:
        return math.nan
    value, _ = stats.pearsonr(valid.iloc[:, 0], valid.iloc[:, 1])
    return float(value) if not math.isnan(value) else math.nan


def fetch_market(market: str, symbols: list[str], count: int) -> MarketData:
    provider = FutuProvider()
    factor = XMM30mFactor()

    factor_map: dict[str, pd.Series] = {}
    close_map: dict[str, pd.Series] = {}
    bars: dict[str, int] = {}
    latest: dict[str, str] = {}
    errors: dict[str, str] = {}

    print(f"\n## Fetch {market} symbols={len(symbols)} count={count}", flush=True)
    for sym in symbols:
        try:
            kdata = provider.fetch_klines(sym, count=count, timeframe="30m")
            df = kdata.df.copy()
            dt_index = pd.to_datetime(df["date"])
            f = factor.compute(kdata)
            f.index = dt_index
            c = pd.Series(df["close"].astype(float).values, index=dt_index, name=sym)

            factor_map[sym] = f.rename(sym)
            close_map[sym] = c
            bars[sym] = len(df)
            latest[sym] = str(dt_index.iloc[-1])
            valid_n = int(f.dropna().shape[0])
            last_score = float(f.dropna().iloc[-1]) if valid_n else math.nan
            print(
                f"{market} {sym:>6} bars={len(df):3d} valid={valid_n:3d} "
                f"latest={latest[sym]} score={last_score:7.2f}",
                flush=True,
            )
        except Exception as exc:
            errors[sym] = f"{type(exc).__name__}: {exc}"
            print(f"{market} {sym:>6} ERROR {errors[sym]}", flush=True)

    return MarketData(
        market=market,
        factor=pd.DataFrame(factor_map).sort_index(),
        close=pd.DataFrame(close_map).sort_index(),
        bars=bars,
        latest=latest,
        errors=errors,
    )


def forward_returns(close: pd.DataFrame, horizon: int) -> pd.DataFrame:
    return close.shift(-horizon) / close - 1.0


def cross_sectional_ic(factor: pd.DataFrame, returns: pd.DataFrame, horizon: int) -> dict:
    common_index = factor.index.intersection(returns.index)
    ic_values = []
    pearson_values = []
    n_names = []

    for ts in common_index:
        f = factor.loc[ts]
        r = returns.loc[ts]
        valid = pd.concat([f, r], axis=1).replace([np.inf, -np.inf], np.nan).dropna()
        if len(valid) < 5:
            continue
        rank_ic = _safe_spearman(valid.iloc[:, 0], valid.iloc[:, 1])
        ic = _safe_pearson(valid.iloc[:, 0], valid.iloc[:, 1])
        if not math.isnan(rank_ic):
            ic_values.append(rank_ic)
            n_names.append(len(valid))
        if not math.isnan(ic):
            pearson_values.append(ic)

    ic_series = pd.Series(ic_values, dtype=float)
    pearson_series = pd.Series(pearson_values, dtype=float)
    return {
        "horizon": horizon,
        "n_dates": int(ic_series.shape[0]),
        "avg_names": float(np.mean(n_names)) if n_names else 0.0,
        "rank_ic_mean": float(ic_series.mean()) if len(ic_series) else math.nan,
        "rank_ic_median": float(ic_series.median()) if len(ic_series) else math.nan,
        "rank_ic_std": float(ic_series.std(ddof=1)) if len(ic_series) > 1 else math.nan,
        "rank_ic_ir": float(ic_series.mean() / ic_series.std(ddof=1)) if len(ic_series) > 1 and ic_series.std(ddof=1) else math.nan,
        "rank_ic_positive_rate": float((ic_series > 0).mean()) if len(ic_series) else math.nan,
        "pearson_ic_mean": float(pearson_series.mean()) if len(pearson_series) else math.nan,
    }


def pooled_summary(factor: pd.DataFrame, returns: pd.DataFrame, horizon: int) -> dict:
    f = factor.stack().rename("factor")
    r = returns.stack().rename("return")
    valid = pd.concat([f, r], axis=1).replace([np.inf, -np.inf], np.nan).dropna()
    if len(valid) < 20:
        return {"horizon": horizon, "n_obs": len(valid)}

    rank_ic = _safe_spearman(valid["factor"], valid["return"])
    pearson_ic = _safe_pearson(valid["factor"], valid["return"])
    hit_rate = float(((valid["factor"] > 0) & (valid["return"] > 0) | ((valid["factor"] < 0) & (valid["return"] < 0))).mean())

    quantile_returns: dict[str, float] = {}
    spread = math.nan
    try:
        valid = valid.copy()
        valid["quantile"] = pd.qcut(valid["factor"], 5, labels=["Q1", "Q2", "Q3", "Q4", "Q5"], duplicates="drop")
        grouped = valid.groupby("quantile", observed=True)["return"].mean()
        quantile_returns = {str(k): float(v) for k, v in grouped.items()}
        if "Q1" in quantile_returns and "Q5" in quantile_returns:
            spread = quantile_returns["Q5"] - quantile_returns["Q1"]
    except ValueError:
        pass

    return {
        "horizon": horizon,
        "n_obs": int(len(valid)),
        "pooled_rank_ic": rank_ic,
        "pooled_pearson_ic": pearson_ic,
        "direction_hit_rate": hit_rate,
        "q1_mean": quantile_returns.get("Q1", math.nan),
        "q2_mean": quantile_returns.get("Q2", math.nan),
        "q3_mean": quantile_returns.get("Q3", math.nan),
        "q4_mean": quantile_returns.get("Q4", math.nan),
        "q5_mean": quantile_returns.get("Q5", math.nan),
        "q5_minus_q1": spread,
    }


def latest_scores(md: MarketData) -> pd.DataFrame:
    rows = []
    for sym in md.factor.columns:
        valid = md.factor[sym].dropna()
        score = float(valid.iloc[-1]) if len(valid) else math.nan
        rows.append({
            "market": md.market,
            "symbol": sym,
            "latest_score": score,
            "state": "BULL" if score > 20 else "BEAR" if score < -20 else "NEUTRAL",
            "valid_factor_bars": int(len(valid)),
            "bars": md.bars.get(sym, 0),
            "latest_bar": md.latest.get(sym, ""),
        })
    return pd.DataFrame(rows).sort_values("latest_score", ascending=False)


def fmt_pct(value: float) -> str:
    if value is None or math.isnan(value):
        return "N/A"
    return f"{value * 100:+.3f}%"


def fmt_float(value: float, digits: int = 4) -> str:
    if value is None or math.isnan(value):
        return "N/A"
    return f"{value:+.{digits}f}"


def write_report(
    market_results: dict[str, dict[str, pd.DataFrame]],
    latest_df: pd.DataFrame,
    report_path: Path,
    count: int,
    horizons: list[int],
    elapsed: float,
) -> None:
    lines = []
    lines.append("# 30分钟 XMM 因子 IC / 分层收益验证")
    lines.append("")
    lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} Asia/Shanghai")
    lines.append(f"样本长度: 每标的最多 {count} 根 30m K 线")
    lines.append(f"持有期: {', '.join(str(h) + ' bars' for h in horizons)}")
    lines.append(f"执行耗时: {elapsed:.1f}s")
    lines.append("")
    lines.append("> 研究层验证，不是交易建议；未接入生产 FusionController。")
    lines.append("")

    lines.append("## 最新分数")
    lines.append("")
    lines.append("| 市场 | 标的 | 分数 | 状态 | 有效因子样本 | 最新K线 |")
    lines.append("|---|---|---:|---|---:|---|")
    for _, row in latest_df.iterrows():
        lines.append(
            f"| {row['market']} | {row['symbol']} | {row['latest_score']:+.2f} | "
            f"{row['state']} | {int(row['valid_factor_bars'])}/{int(row['bars'])} | {row['latest_bar']} |"
        )
    lines.append("")

    for market, tables in market_results.items():
        lines.append(f"## {market} 截面 IC")
        lines.append("")
        lines.append("| Horizon | N dates | Avg names | RankIC Mean | RankIC Median | RankIC IR | Positive Rate | Pearson IC |")
        lines.append("|---:|---:|---:|---:|---:|---:|---:|---:|")
        for _, row in tables["cs_ic"].iterrows():
            lines.append(
                f"| {int(row['horizon'])} | {int(row['n_dates'])} | {row['avg_names']:.1f} | "
                f"{fmt_float(row['rank_ic_mean'])} | {fmt_float(row['rank_ic_median'])} | "
                f"{fmt_float(row['rank_ic_ir'])} | {row['rank_ic_positive_rate']*100:.1f}% | "
                f"{fmt_float(row['pearson_ic_mean'])} |"
            )
        lines.append("")

        lines.append(f"## {market} 分层收益")
        lines.append("")
        lines.append("| Horizon | N obs | Pooled RankIC | Hit Rate | Q1 | Q2 | Q3 | Q4 | Q5 | Q5-Q1 |")
        lines.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for _, row in tables["pooled"].iterrows():
            lines.append(
                f"| {int(row['horizon'])} | {int(row['n_obs'])} | {fmt_float(row.get('pooled_rank_ic', math.nan))} | "
                f"{row.get('direction_hit_rate', math.nan)*100:.1f}% | "
                f"{fmt_pct(row.get('q1_mean', math.nan))} | {fmt_pct(row.get('q2_mean', math.nan))} | "
                f"{fmt_pct(row.get('q3_mean', math.nan))} | {fmt_pct(row.get('q4_mean', math.nan))} | "
                f"{fmt_pct(row.get('q5_mean', math.nan))} | {fmt_pct(row.get('q5_minus_q1', math.nan))} |"
            )
        lines.append("")

    lines.append("## 读数口径")
    lines.append("")
    lines.append("- 截面 RankIC: 每个 30m 时间点，用同一市场内不同标的的因子分数排名去预测未来收益排名。")
    lines.append("- Pooled RankIC: 将所有时间点和标的堆叠后的整体相关性，样本更多但时间相关性更强。")
    lines.append("- 分层收益: 按因子分数从低到高分成 Q1~Q5，观察未来收益是否单调提升。")
    lines.append("- `Q5-Q1` 为高分组减低分组，是最直观的分层收益差。")
    lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=500)
    parser.add_argument("--markets", nargs="+", default=["HK", "US"], choices=["HK", "US"])
    parser.add_argument("--horizons", nargs="+", type=int, default=DEFAULT_HORIZONS)
    parser.add_argument("--max-symbols", type=int, default=0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started = datetime.now()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    registry = SymbolRegistry()
    all_latest = []
    market_results: dict[str, dict[str, pd.DataFrame]] = {}

    for market in args.markets:
        symbols = [s.symbol for s in registry.get_symbols(market)]
        if args.max_symbols:
            symbols = symbols[:args.max_symbols]
        md = fetch_market(market, symbols, args.count)
        all_latest.append(latest_scores(md))

        cs_rows = []
        pooled_rows = []
        for horizon in args.horizons:
            fwd = forward_returns(md.close, horizon)
            cs_rows.append(cross_sectional_ic(md.factor, fwd, horizon))
            pooled_rows.append(pooled_summary(md.factor, fwd, horizon))

        market_results[market] = {
            "cs_ic": pd.DataFrame(cs_rows),
            "pooled": pd.DataFrame(pooled_rows),
        }

        ts = started.strftime("%Y%m%d_%H%M")
        md.factor.to_csv(OUTPUT_DIR / f"xmm_30m_factor_{market}_{ts}.csv", encoding="utf-8-sig")
        md.close.to_csv(OUTPUT_DIR / f"xmm_30m_close_{market}_{ts}.csv", encoding="utf-8-sig")
        market_results[market]["cs_ic"].to_csv(OUTPUT_DIR / f"xmm_30m_cs_ic_{market}_{ts}.csv", index=False, encoding="utf-8-sig")
        market_results[market]["pooled"].to_csv(OUTPUT_DIR / f"xmm_30m_pooled_{market}_{ts}.csv", index=False, encoding="utf-8-sig")

    latest_df = pd.concat(all_latest, ignore_index=True).sort_values(["market", "latest_score"], ascending=[True, False])
    ts = started.strftime("%Y%m%d_%H%M")
    latest_df.to_csv(OUTPUT_DIR / f"xmm_30m_latest_scores_{ts}.csv", index=False, encoding="utf-8-sig")

    report_path = REPORT_DIR / f"xmm_30m_ic_study_{ts}.md"
    elapsed = (datetime.now() - started).total_seconds()
    write_report(market_results, latest_df, report_path, args.count, args.horizons, elapsed)

    print(f"\nREPORT={report_path}", flush=True)
    print(f"OUTPUT_DIR={OUTPUT_DIR}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
