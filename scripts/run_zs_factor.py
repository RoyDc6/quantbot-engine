# -*- coding: utf-8 -*-
"""Run the independent ZS factor for one ticker.

This command fetches OHLC data and evaluates ChanCenterFactor directly.  It
does not initialize FusionController, HardGate, account queries, or execution.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.adapter_factory import AdapterFactory  # noqa: E402
from core.models.chan_center_factor import analyze_chan_center  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="独立运行缠论中枢 ZS 因子")
    parser.add_argument("--ticker", required=True, help="例如 00700.HK / AAPL.US")
    parser.add_argument("--count", type=int, default=252, help="OHLC K线数量，默认252")
    parser.add_argument("--output", type=Path, help="可选JSON输出路径")
    args = parser.parse_args()

    adapter = AdapterFactory.get_adapter(args.ticker)
    df = adapter.fetch_kline(args.ticker, count=args.count)
    if df is None or len(df) == 0:
        parser.error(f"无法取得 {args.ticker} 的OHLC数据")

    signal = analyze_chan_center(df, ticker=args.ticker)
    payload = asdict(signal)
    payload["bars"] = len(df)
    payload["independent_factor"] = True

    rendered = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
