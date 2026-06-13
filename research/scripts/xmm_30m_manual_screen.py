#!/usr/bin/env python3
"""
Manual 30m XMM factor screener.

Research-only CLI for ad-hoc screening. It fetches 30-minute Futu bars,
computes the XMM 30m factor, classifies overheat risk, and writes a CSV.
It does not read daily signal JSON, generate daily reports, place orders, or
modify FusionController.
"""

from __future__ import annotations

import argparse
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


OUTPUT_DIR = ROOT / "research" / "output"


def normalize_symbol(symbol: str, market: str) -> str:
    symbol = symbol.strip().upper()
    if market == "HK" and symbol.endswith(".HK"):
        return symbol.split(".", 1)[0]
    if market == "US" and symbol.endswith(".US"):
        return symbol.split(".", 1)[0]
    if market == "US" and symbol.startswith("US."):
        return symbol.split(".", 1)[1]
    return symbol


def build_registry(symbols: list[str], market: str) -> SymbolRegistry:
    if not symbols:
        return SymbolRegistry()
    infos = []
    for sym in symbols:
        code = normalize_symbol(sym, market)
        futu_code = f"{market}.{code}"
        infos.append(SymbolInfo(code, market, code, "stock", futu_code))
    return SymbolRegistry(symbols=infos)


def default_symbols(market: str, registry: SymbolRegistry) -> list[str]:
    return [s.symbol for s in registry.get_symbols(market)]


def classify(raw: float, threshold: float, watch_threshold: float) -> str:
    if math.isnan(raw):
        return "NO_DATA"
    if raw >= threshold:
        return "OVERHEAT"
    if raw >= watch_threshold:
        return "WARM"
    if raw <= -threshold:
        return "COLD_REVERSAL"
    return "PASS"


def screen_symbols(args: argparse.Namespace) -> pd.DataFrame:
    symbols = [normalize_symbol(s, args.market) for s in args.symbols] if args.symbols else []
    registry = build_registry(symbols, args.market)
    if not symbols:
        symbols = default_symbols(args.market, registry)
        if args.max_symbols:
            symbols = symbols[:args.max_symbols]

    provider = FutuProvider(symbol_registry=registry)
    factor = XMM30mFactor()
    rows = []

    print(
        f"XMM 30m manual screen | market={args.market} symbols={len(symbols)} "
        f"count={args.count} params={args.short}/{args.long}",
        flush=True,
    )
    for sym in symbols:
        try:
            kdata = provider.fetch_klines(sym, count=args.count, timeframe="30m")
            scores = factor.compute(
                kdata,
                short_period=args.short,
                long_period=args.long,
                lookback=args.lookback,
                min_bars=args.min_bars,
            )
            valid = scores.dropna()
            raw = float(valid.iloc[-1]) if len(valid) else math.nan
            latest_ts = str(pd.to_datetime(kdata.df["date"]).iloc[-1])
            state = classify(raw, args.threshold, args.watch_threshold)
            row = {
                "market": args.market,
                "symbol": f"{sym}.{args.market}" if args.market == "HK" else f"{sym}.US",
                "raw_score": raw,
                "inverse_score": -raw if not math.isnan(raw) else math.nan,
                "state": state,
                "latest_bar": latest_ts,
                "valid_factor_bars": int(len(valid)),
                "bars": int(kdata.n_bars),
                "short_period": args.short,
                "long_period": args.long,
                "threshold": args.threshold,
                "watch_threshold": args.watch_threshold,
            }
            rows.append(row)
            raw_text = "N/A" if math.isnan(raw) else f"{raw:+7.2f}"
            print(f"{row['symbol']:<10} raw={raw_text} inverse={row['inverse_score']:+7.2f} {state:<13} latest={latest_ts}", flush=True)
        except Exception as exc:
            rows.append({
                "market": args.market,
                "symbol": f"{sym}.{args.market}" if args.market == "HK" else f"{sym}.US",
                "raw_score": math.nan,
                "inverse_score": math.nan,
                "state": "ERROR",
                "latest_bar": "",
                "valid_factor_bars": 0,
                "bars": 0,
                "error": f"{type(exc).__name__}: {exc}",
                "short_period": args.short,
                "long_period": args.long,
                "threshold": args.threshold,
                "watch_threshold": args.watch_threshold,
            })
            print(f"{sym:<10} ERROR {type(exc).__name__}: {exc}", flush=True)

    df = pd.DataFrame(rows)
    if not df.empty:
        ascending = args.sort in {"raw_asc", "inverse_asc"}
        sort_col = {
            "raw_desc": "raw_score",
            "raw_asc": "raw_score",
            "inverse_desc": "inverse_score",
            "inverse_asc": "inverse_score",
        }[args.sort]
        df = df.sort_values(sort_col, ascending=ascending, na_position="last")
    return df


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["HK", "US"], default="HK")
    parser.add_argument("--symbols", nargs="*", default=[])
    parser.add_argument("--max-symbols", type=int, default=0)
    parser.add_argument("--count", type=int, default=220)
    parser.add_argument("--short", type=int, default=25)
    parser.add_argument("--long", type=int, default=90)
    parser.add_argument("--lookback", type=int, default=160)
    parser.add_argument("--min-bars", type=int, default=100)
    parser.add_argument("--threshold", type=float, default=30.0)
    parser.add_argument("--watch-threshold", type=float, default=20.0)
    parser.add_argument(
        "--sort",
        choices=["raw_desc", "raw_asc", "inverse_desc", "inverse_asc"],
        default="raw_desc",
    )
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df = screen_symbols(args)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    output = args.output or OUTPUT_DIR / f"xmm_30m_manual_screen_{args.market}_{args.short}_{args.long}_{ts}.csv"
    df.to_csv(output, index=False, encoding="utf-8-sig")

    print("")
    print("Summary:")
    if df.empty:
        print("  no rows")
    else:
        counts = df["state"].value_counts().to_dict()
        for state, count in sorted(counts.items()):
            print(f"  {state}: {count}")
        print("")
        print(df[["symbol", "raw_score", "inverse_score", "state", "latest_bar"]].to_string(index=False))
    print("")
    print(f"CSV={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
