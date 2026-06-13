#!/usr/bin/env python3
"""
Research-layer HK 30m XMM overheat filter.

Applies the HK 25/90 30m XMM raw-score overheat rule to daily HK signal JSON.
This script only writes research reports; it does not modify daily signals,
FusionController, orders, or execution logic.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.data.futu_provider import FutuProvider
from research.data.symbol_registry import SymbolInfo, SymbolRegistry
from research.factors.technical.xmm_30m import XMM30mFactor


SIGNAL_DIR = ROOT / "paper_trading" / "signals"
OUTPUT_DIR = ROOT / "research" / "output"
REPORT_DIR = Path(r"E:\AIWorkspace\02_ProjectReports")


def latest_file(directory: Path, pattern: str) -> Path:
    files = sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError(f"No files match {directory / pattern}")
    return files[0]


def symbol_to_factor_col(symbol: str) -> str:
    if symbol.endswith(".HK"):
        return symbol.split(".", 1)[0]
    return symbol


def classify_filter(signal: dict, raw_score: float | None, threshold: float, watch_threshold: float) -> tuple[str, str]:
    level = signal.get("fusion_level", "HOLD")
    xmm_action = signal.get("xmm_action", "HOLD")

    if raw_score is None or math.isnan(raw_score):
        return "NO_30M_DATA", "无 30m XMM 分数，不调整，仅标记缺失"

    if raw_score >= threshold:
        if level in {"BUY", "STRONG_BUY"} or xmm_action == "BUY":
            return "DELAY_BUY", f"30m raw={raw_score:.2f} >= {threshold:g}，追涨过热，建议延迟/降确认"
        return "OVERHEAT_WATCH", f"30m raw={raw_score:.2f} >= {threshold:g}，不追高，维持观察"

    if raw_score >= watch_threshold:
        if level in {"BUY", "STRONG_BUY"} or xmm_action == "BUY":
            return "BUY_CAUTION", f"30m raw={raw_score:.2f} >= {watch_threshold:g}，买入信号需谨慎"
        return "WARM_WATCH", f"30m raw={raw_score:.2f} >= {watch_threshold:g}，轻度过热观察"

    return "PASS", f"30m raw={raw_score:.2f} 未触发过热过滤"


def load_latest_scores(factor_path: Path) -> tuple[pd.Series, str]:
    df = pd.read_csv(factor_path, index_col=0)
    df.index = pd.to_datetime(df.index)
    latest = df.dropna(how="all").iloc[-1]
    latest_ts = str(df.dropna(how="all").index[-1])
    return latest, latest_ts


def compute_missing_score(symbol: str, count: int = 220) -> tuple[float, str]:
    code = symbol_to_factor_col(symbol)
    registry = SymbolRegistry(symbols=[SymbolInfo(code, "HK", code, "stock", f"HK.{code}")])
    provider = FutuProvider(symbol_registry=registry)
    kdata = provider.fetch_klines(code, count=count, timeframe="30m")
    scores = XMM30mFactor().compute(kdata, short_period=25, long_period=90)
    valid = scores.dropna()
    if valid.empty:
        return math.nan, ""
    latest_ts = str(pd.to_datetime(kdata.df["date"]).iloc[-1])
    return float(valid.iloc[-1]), latest_ts


def write_report(report_path: Path, rows: list[dict], signal_path: Path, factor_path: Path, factor_ts: str, threshold: float, watch_threshold: float) -> None:
    lines = []
    lines.append("# HK 30m XMM 过热过滤器研究报告")
    lines.append("")
    lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} Asia/Shanghai")
    lines.append(f"日线信号: `{signal_path}`")
    lines.append(f"30m factor: `{factor_path}`")
    lines.append(f"30m factor 最新bar: {factor_ts}")
    lines.append(f"过热阈值: raw >= {threshold:g}")
    lines.append(f"观察阈值: raw >= {watch_threshold:g}")
    lines.append("")
    lines.append("> 研究层过滤报告，不修改日线信号、不下单、不接入 FusionController。")
    lines.append("")

    counts = pd.Series([r["filter_action"] for r in rows]).value_counts().to_dict()
    lines.append("## 总览")
    lines.append("")
    lines.append("| Filter Action | 数量 |")
    lines.append("|---|---:|")
    for key, value in sorted(counts.items()):
        lines.append(f"| {key} | {value} |")
    lines.append("")

    lines.append("## 明细")
    lines.append("")
    lines.append("| 标的 | Fusion | Score | XMM | 30m Raw | 来源 | Filter | 建议 |")
    lines.append("|---|---|---:|---|---:|---|---|---|")
    for r in rows:
        raw = "N/A" if r["xmm_30m_raw"] is None or math.isnan(r["xmm_30m_raw"]) else f"{r['xmm_30m_raw']:.2f}"
        lines.append(
            f"| {r['symbol']} | {r['fusion_level']} | {r['fusion_score']:+.1f} | "
            f"{r['xmm_action']} | {raw} | {r.get('xmm_30m_source', '')} | {r['filter_action']} | {r['filter_reason']} |"
        )
    lines.append("")

    blocked = [r for r in rows if r["filter_action"] in {"DELAY_BUY", "BUY_CAUTION"}]
    if blocked:
        lines.append("## 买入信号过滤")
        lines.append("")
        for r in blocked:
            lines.append(f"- **{r['symbol']}**: {r['filter_reason']}")
        lines.append("")
    else:
        lines.append("## 买入信号过滤")
        lines.append("")
        lines.append("当前日线 HK 信号中没有 BUY / STRONG_BUY 被 30m 过热规则拦截。")
        lines.append("")

    lines.append("## 接入建议")
    lines.append("")
    lines.append("- 先作为研究层报告和日志字段，不进入执行层。")
    lines.append("- 若 `fusion_level in BUY/STRONG_BUY` 且 `xmm_30m_raw >= 30`，建议 `DELAY_BUY`。")
    lines.append("- 若非买入信号但 `xmm_30m_raw >= 30`，仅标记 `OVERHEAT_WATCH`。")
    lines.append("- 30m 数据缺失时不做过滤，只标记 `NO_30M_DATA`。")
    lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signals", type=Path, default=None)
    parser.add_argument("--factor", type=Path, default=None)
    parser.add_argument("--threshold", type=float, default=30.0)
    parser.add_argument("--watch-threshold", type=float, default=20.0)
    parser.add_argument("--fill-missing", action="store_true", default=True)
    parser.add_argument("--no-fill-missing", dest="fill_missing", action="store_false")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started = datetime.now()
    ts = started.strftime("%Y%m%d_%H%M")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    signal_path = args.signals or latest_file(SIGNAL_DIR, "*_HK.json")
    factor_path = args.factor or latest_file(OUTPUT_DIR, "xmm_30m_sensitivity_factor_HK_25_90_*.csv")

    payload = json.loads(signal_path.read_text(encoding="utf-8"))
    signals = payload.get("signals", payload if isinstance(payload, list) else [])
    latest_scores, factor_ts = load_latest_scores(factor_path)
    filled_scores: dict[str, tuple[float, str]] = {}

    rows = []
    for sig in signals:
        symbol = sig.get("symbol", "")
        col = symbol_to_factor_col(symbol)
        raw = float(latest_scores[col]) if col in latest_scores.index and pd.notna(latest_scores[col]) else math.nan
        raw_source = "csv"
        if (math.isnan(raw) and args.fill_missing and symbol.endswith(".HK")):
            try:
                raw, filled_ts = compute_missing_score(symbol)
                filled_scores[symbol] = (raw, filled_ts)
                raw_source = "computed"
            except Exception:
                raw_source = "missing"
        action, reason = classify_filter(sig, raw, args.threshold, args.watch_threshold)
        rows.append({
            "date": sig.get("date", payload.get("date", "")),
            "symbol": symbol,
            "fusion_level": sig.get("fusion_level", "HOLD"),
            "fusion_score": float(sig.get("fusion_score", 0.0) or 0.0),
            "fusion_confidence": float(sig.get("fusion_confidence", 0.0) or 0.0),
            "xmm_action": sig.get("xmm_action", "HOLD"),
            "xmm_reason": sig.get("xmm_reason", ""),
            "xmm_30m_raw": raw,
            "xmm_30m_source": raw_source,
            "filter_action": action,
            "filter_reason": reason,
        })

    out_csv = OUTPUT_DIR / f"xmm_30m_hk_overheat_filter_{ts}.csv"
    report_path = REPORT_DIR / f"xmm_30m_hk_overheat_filter_{ts}.md"
    pd.DataFrame(rows).to_csv(out_csv, index=False, encoding="utf-8-sig")
    write_report(report_path, rows, signal_path, factor_path, factor_ts, args.threshold, args.watch_threshold)

    print(f"REPORT={report_path}", flush=True)
    print(f"CSV={out_csv}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
