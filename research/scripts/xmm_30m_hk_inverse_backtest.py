#!/usr/bin/env python3
"""
HK 30m XMM 25/90 inverse trading-rule backtest.

Research-only. Uses cached factor/close CSV files from the parameter
sensitivity run. No production signal or execution code is touched.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
for path in [ROOT, ROOT / "research"]:
    p = str(path)
    if p not in sys.path:
        sys.path.insert(0, p)

from research.scripts.xmm_30m_ic_study import fmt_float, fmt_pct


REPORT_DIR = Path(r"E:\AIWorkspace\02_ProjectReports")
OUTPUT_DIR = ROOT / "research" / "output"


@dataclass
class StrategyResult:
    name: str
    net_returns: pd.Series
    gross_returns: pd.Series
    weights: pd.DataFrame
    turnover: pd.Series


def latest_file(pattern: str) -> Path:
    files = sorted(OUTPUT_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError(f"No files match {pattern}")
    return files[0]


def load_frame(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, index_col=0)
    df.index = pd.to_datetime(df.index)
    return df.apply(pd.to_numeric, errors="coerce")


def next_bar_returns(close: pd.DataFrame) -> pd.DataFrame:
    return close.shift(-1) / close - 1.0


def equal_weight(mask: pd.DataFrame) -> pd.DataFrame:
    valid_counts = mask.sum(axis=1).replace(0, np.nan)
    weights = mask.div(valid_counts, axis=0).fillna(0.0)
    return weights


def bottom_n_weights(scores: pd.DataFrame, n: int) -> pd.DataFrame:
    weights = pd.DataFrame(0.0, index=scores.index, columns=scores.columns)
    for ts, row in scores.iterrows():
        valid = row.dropna()
        if valid.empty:
            continue
        chosen = valid.nsmallest(min(n, len(valid))).index
        weights.loc[ts, chosen] = 1.0 / len(chosen)
    return weights


def top_n_weights(scores: pd.DataFrame, n: int) -> pd.DataFrame:
    weights = pd.DataFrame(0.0, index=scores.index, columns=scores.columns)
    for ts, row in scores.iterrows():
        valid = row.dropna()
        if valid.empty:
            continue
        chosen = valid.nlargest(min(n, len(valid))).index
        weights.loc[ts, chosen] = 1.0 / len(chosen)
    return weights


def score_band_weights(scores: pd.DataFrame, low: float | None = None, high: float | None = None) -> pd.DataFrame:
    mask = pd.DataFrame(True, index=scores.index, columns=scores.columns)
    mask &= scores.notna()
    if low is not None:
        mask &= scores >= low
    if high is not None:
        mask &= scores <= high
    return equal_weight(mask)


def run_strategy(name: str, weights: pd.DataFrame, returns: pd.DataFrame, cost_bps: float) -> StrategyResult:
    common_index = weights.index.intersection(returns.index)
    weights = weights.loc[common_index].fillna(0.0)
    returns = returns.loc[common_index].fillna(0.0)
    gross = (weights * returns).sum(axis=1)
    turnover = weights.diff().abs().sum(axis=1).fillna(weights.abs().sum(axis=1))
    cost = turnover * (cost_bps / 10000.0)
    net = gross - cost
    return StrategyResult(name, net, gross, weights, turnover)


def max_drawdown(returns: pd.Series) -> float:
    equity = (1.0 + returns.fillna(0.0)).cumprod()
    peak = equity.cummax()
    dd = equity / peak - 1.0
    return float(dd.min()) if len(dd) else math.nan


def summarize(result: StrategyResult, bars_per_year: int = 252 * 13) -> dict:
    r = result.net_returns.dropna()
    gross = result.gross_returns.dropna()
    if r.empty:
        return {"strategy": result.name}
    total = float((1.0 + r).prod() - 1.0)
    gross_total = float((1.0 + gross).prod() - 1.0)
    mean = float(r.mean())
    vol = float(r.std(ddof=1))
    sharpe = mean / vol * math.sqrt(bars_per_year) if vol and not math.isnan(vol) else math.nan
    active = (result.weights.abs().sum(axis=1) > 0).reindex(r.index).fillna(False)
    avg_names = (result.weights.astype(bool).sum(axis=1)).reindex(r.index).mean()
    return {
        "strategy": result.name,
        "n_bars": int(len(r)),
        "active_rate": float(active.mean()),
        "avg_names": float(avg_names),
        "gross_total": gross_total,
        "net_total": total,
        "mean_bar": mean,
        "win_rate": float((r > 0).mean()),
        "sharpe_annualized": sharpe,
        "max_drawdown": max_drawdown(r),
        "avg_turnover": float(result.turnover.reindex(r.index).mean()),
    }


def event_study(scores: pd.DataFrame, close: pd.DataFrame, threshold: float, horizons: list[int]) -> pd.DataFrame:
    rows = []
    for sym in scores.columns:
        s = scores[sym]
        c = close[sym]
        events = (s >= threshold) & (s.shift(1) < threshold)
        for ts in s.index[events.fillna(False)]:
            if ts not in c.index:
                continue
            loc = c.index.get_loc(ts)
            if isinstance(loc, slice):
                continue
            for h in horizons:
                if loc + h >= len(c):
                    continue
                start = float(c.iloc[loc])
                end = float(c.iloc[loc + h])
                if start > 0:
                    rows.append({
                        "symbol": sym,
                        "event_time": ts,
                        "horizon": h,
                        "forward_return": end / start - 1.0,
                        "score": float(s.loc[ts]),
                    })
    if not rows:
        return pd.DataFrame()
    events = pd.DataFrame(rows)
    summary = events.groupby("horizon").agg(
        n_events=("forward_return", "count"),
        mean_return=("forward_return", "mean"),
        median_return=("forward_return", "median"),
        win_rate=("forward_return", lambda x: float((x > 0).mean())),
    ).reset_index()
    return summary


def write_report(
    report_path: Path,
    factor_path: Path,
    close_path: Path,
    summary: pd.DataFrame,
    event_summary: pd.DataFrame,
    threshold: float,
    cost_bps: float,
) -> None:
    lines = []
    lines.append("# HK 30分钟 XMM 25/90 inverse 交易规则回测")
    lines.append("")
    lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} Asia/Shanghai")
    lines.append(f"Factor: `{factor_path}`")
    lines.append(f"Close: `{close_path}`")
    lines.append(f"过热阈值: raw score >= {threshold:g}")
    lines.append(f"交易成本: {cost_bps:g} bps per 1.0 turnover")
    lines.append("")
    lines.append("> 研究层回测，不是交易建议；未接入生产 FusionController。")
    lines.append("")

    lines.append("## 策略表现")
    lines.append("")
    lines.append("| 策略 | Bars | Active | Avg Names | Gross Total | Net Total | Mean / Bar | Win Rate | Sharpe | Max DD | Avg Turnover |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for _, row in summary.iterrows():
        lines.append(
            f"| {row['strategy']} | {int(row['n_bars'])} | {row['active_rate']*100:.1f}% | "
            f"{row['avg_names']:.1f} | {fmt_pct(row['gross_total'])} | {fmt_pct(row['net_total'])} | "
            f"{fmt_pct(row['mean_bar'])} | {row['win_rate']*100:.1f}% | "
            f"{fmt_float(row['sharpe_annualized'], 2)} | {fmt_pct(row['max_drawdown'])} | {row['avg_turnover']:.3f} |"
        )
    lines.append("")

    lines.append("## 过热事件研究")
    lines.append("")
    if event_summary.empty:
        lines.append("无过热穿越事件。")
    else:
        lines.append("| Horizon Bars | Events | Mean Return | Median Return | Win Rate |")
        lines.append("|---:|---:|---:|---:|---:|")
        for _, row in event_summary.iterrows():
            lines.append(
                f"| {int(row['horizon'])} | {int(row['n_events'])} | {fmt_pct(row['mean_return'])} | "
                f"{fmt_pct(row['median_return'])} | {row['win_rate']*100:.1f}% |"
            )
    lines.append("")

    lines.append("## 策略定义")
    lines.append("")
    lines.append("- `base_equal_weight`: 全 HK 默认池等权持有。")
    lines.append("- `avoid_overheat`: raw score >= 阈值的标的下一根 bar 不持有，其余等权。")
    lines.append("- `cool_bottom2`: 每根 bar 买入 raw score 最低的 2 个标的。")
    lines.append("- `overheat_top2`: 每根 bar 买入 raw score 最高的 2 个标的，用来观察追涨风险。")
    lines.append("- `low_score_le_5`: 只买 raw score <= 5 的冷却标的。")
    lines.append("")

    lines.append("## 读数规则")
    lines.append("")
    lines.append("- 若 `avoid_overheat` 显著优于 `base_equal_weight`，说明该因子适合做风险过滤器。")
    lines.append("- 若 `cool_bottom2` 优于 `overheat_top2`，说明反向/冷却解释成立。")
    lines.append("- 若加入成本后优势消失，则只能做信号过滤，不能独立交易。")
    lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--factor", type=Path, default=None)
    parser.add_argument("--close", type=Path, default=None)
    parser.add_argument("--threshold", type=float, default=20.0)
    parser.add_argument("--cost-bps", type=float, default=10.0)
    parser.add_argument("--bottom-n", type=int, default=2)
    parser.add_argument("--top-n", type=int, default=2)
    parser.add_argument("--event-horizons", nargs="+", type=int, default=[1, 2, 4, 8, 13])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started = datetime.now()
    ts = started.strftime("%Y%m%d_%H%M")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    factor_path = args.factor or latest_file("xmm_30m_sensitivity_factor_HK_25_90_*.csv")
    close_path = args.close or latest_file("xmm_30m_sensitivity_close_HK_*.csv")
    scores = load_frame(factor_path)
    close = load_frame(close_path)
    common_cols = [c for c in scores.columns if c in close.columns]
    scores = scores[common_cols]
    close = close[common_cols]
    returns = next_bar_returns(close)
    valid_index = scores.notna().any(axis=1) & returns.notna().any(axis=1)
    scores = scores.loc[valid_index]
    returns = returns.loc[valid_index]
    close = close.loc[valid_index]

    valid_mask = scores.notna()
    strategies = [
        run_strategy("base_equal_weight", equal_weight(valid_mask), returns, args.cost_bps),
        run_strategy("avoid_overheat", score_band_weights(scores, high=args.threshold - 1e-12), returns, args.cost_bps),
        run_strategy(f"cool_bottom{args.bottom_n}", bottom_n_weights(scores, args.bottom_n), returns, args.cost_bps),
        run_strategy(f"overheat_top{args.top_n}", top_n_weights(scores, args.top_n), returns, args.cost_bps),
        run_strategy("low_score_le_5", score_band_weights(scores, high=5.0), returns, args.cost_bps),
    ]

    summary = pd.DataFrame([summarize(s) for s in strategies])
    event_summary = event_study(scores, close, args.threshold, args.event_horizons)

    tag = f"thr{args.threshold:g}_cost{args.cost_bps:g}".replace(".", "p")
    summary_path = OUTPUT_DIR / f"xmm_30m_hk_inverse_backtest_summary_{tag}_{ts}.csv"
    events_path = OUTPUT_DIR / f"xmm_30m_hk_inverse_overheat_events_{tag}_{ts}.csv"
    report_path = REPORT_DIR / f"xmm_30m_hk_inverse_backtest_{tag}_{ts}.md"
    summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
    event_summary.to_csv(events_path, index=False, encoding="utf-8-sig")
    write_report(report_path, factor_path, close_path, summary, event_summary, args.threshold, args.cost_bps)

    print(f"REPORT={report_path}", flush=True)
    print(f"SUMMARY={summary_path}", flush=True)
    print(f"EVENTS={events_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
