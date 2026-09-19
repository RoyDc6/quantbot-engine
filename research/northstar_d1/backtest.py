"""No-lookahead, long-only diagnostic backtest for Northstar-D1."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List

import numpy as np
import pandas as pd

from .data import CompletedDailyDataSource
from .factors import calc_dual_trend, calc_td_sequence, calc_structure_layer
from .model import NorthstarD1Model
from .runner import OUTPUT_ROOT, deployment_for, validate_market_symbols



def signal_history(
    df: pd.DataFrame,
    model: NorthstarD1Model | None = None,
) -> List[Dict]:
    """Calculate historical decisions once per close using data available at that close."""

    northstar = model or NorthstarD1Model()
    northstar._validate_input(df)
    cfg = northstar.config
    trend_frame = calc_dual_trend(df, cfg.short_period, cfg.long_period)
    structure_frame = calc_structure_layer(
        df,
        cfg.macd_fast,
        cfg.macd_slow,
        cfg.macd_signal,
        cfg.structure_threshold,
    )
    td_frame = calc_td_sequence(df["close"], cfg.td_period)

    history: List[Dict] = []
    for i in range(cfg.minimum_bars - 1, len(df)):
        trend_row = trend_frame.iloc[i]
        close = float(trend_row["close"])
        short_top = float(trend_row["short_top"])
        short_bottom = float(trend_row["short_bottom"])
        long_top = float(trend_row["long_top"])
        long_bottom = float(trend_row["long_bottom"])
        if close > short_top and close > long_top:
            market = "UP"
        elif close < short_bottom and close < long_bottom:
            market = "DOWN"
        else:
            market = "SIDEWAYS"
        trend = {
            "market": market,
            "cross_short_up": bool(trend_row["cross_short_up"]),
            "cross_short_down": bool(trend_row["cross_short_down"]),
            "cross_long_up": bool(trend_row["cross_long_up"]),
            "cross_long_down": bool(trend_row["cross_long_down"]),
        }
        structure_row = structure_frame.iloc[i]
        structure = {
            "bottom_structure": bool(structure_row["bottom_structure"]),
            "bottom_structure_new": bool(structure_row["bottom_structure_new"]),
            "top_structure": bool(structure_row["top_structure"]),
            "top_structure_new": bool(structure_row["top_structure_new"]),
        }
        td_count = int(td_frame["td_count"].iloc[i])
        td = {
            "td_count": td_count,
            "td_near": bool(td_frame["td_near"].iloc[i]),
            "td_reached": bool(td_frame["td_reached"].iloc[i]),
            "is_buy_seq": td_count > 0,
            "is_sell_seq": td_count < 0,
        }
        decision = northstar.decide(trend, structure, td)
        history.append(
            {
                "index": i,
                "date": _date_at(df, i),
                "close": close,
                "market": market,
                **decision,
            }
        )
    return history


def backtest_long_only(
    df: pd.DataFrame,
    model: NorthstarD1Model | None = None,
    *,
    transaction_cost_bps: float = 10.0,
) -> Dict:
    """Run an event-at-close backtest; new exposure starts after that close."""

    northstar = model or NorthstarD1Model()
    history = signal_history(df, northstar)
    if len(history) < 2:
        raise ValueError("insufficient backtest history")

    equity = 1.0
    exposure = 0.0
    turnover = 0.0
    equity_curve: List[float] = []
    daily_returns: List[float] = []
    exposures: List[float] = []
    event_count = 0
    cost_rate = transaction_cost_bps / 10_000.0
    previous_close = float(history[0]["close"])

    for point in history:
        asset_return = float(point["close"]) / previous_close - 1.0
        gross_return = exposure * asset_return
        equity *= 1.0 + gross_return

        new_exposure = exposure
        if point["action"] == "BUY":
            new_exposure = max(exposure, float(point["signal_fraction"]))
        elif point["action"] == "SELL":
            new_exposure = max(
                0.0,
                exposure * (1.0 - float(point["signal_fraction"])),
            )

        traded = abs(new_exposure - exposure)
        cost = traded * cost_rate
        if cost:
            equity *= 1.0 - cost
            event_count += 1
        turnover += traded
        net_return = (1.0 + gross_return) * (1.0 - cost) - 1.0
        daily_returns.append(net_return)
        equity_curve.append(equity)
        exposures.append(new_exposure)
        exposure = new_exposure
        previous_close = float(point["close"])

    curve = np.asarray(equity_curve, dtype=float)
    peaks = np.maximum.accumulate(curve)
    drawdowns = curve / peaks - 1.0
    returns = np.asarray(daily_returns, dtype=float)
    periods = len(returns)
    annualized_return = equity ** (252.0 / max(periods, 1)) - 1.0
    annualized_volatility = float(returns.std(ddof=1) * np.sqrt(252)) if periods > 1 else 0.0
    sharpe = (
        float(returns.mean() / returns.std(ddof=1) * np.sqrt(252))
        if periods > 1 and returns.std(ddof=1) > 0
        else 0.0
    )
    first_close = float(history[0]["close"])
    last_close = float(history[-1]["close"])

    return {
        "mode": "RESEARCH_ONLY",
        "backtest_type": "IN_SAMPLE_DIAGNOSTIC",
        "lookahead": False,
        "signal_timing": "close_t_decision_applies_after_close_t",
        "start": history[0]["date"],
        "end": history[-1]["date"],
        "bars": periods,
        "transaction_cost_bps": transaction_cost_bps,
        "total_return": round(equity - 1.0, 6),
        "benchmark_return": round(last_close / first_close - 1.0, 6),
        "annualized_return": round(float(annualized_return), 6),
        "annualized_volatility": round(annualized_volatility, 6),
        "sharpe_zero_rate": round(sharpe, 4),
        "max_drawdown": round(float(drawdowns.min()), 6),
        "average_exposure": round(float(np.mean(exposures)), 6),
        "ending_exposure": round(float(exposure), 6),
        "turnover": round(float(turnover), 6),
        "executed_signal_events": event_count,
        "signal_events": [
            {
                "date": point["date"],
                "close": point["close"],
                "action": point["action"],
                "signal_fraction": point["signal_fraction"],
                "confidence": point["confidence"],
            }
            for point in history
            if point["action"] in ("BUY", "SELL")
        ],
    }


def backtest_market(
    market: str,
    *,
    symbols: Iterable[str] | None = None,
    data_source: CompletedDailyDataSource | None = None,
    transaction_cost_bps: float = 10.0,
) -> Dict:
    market = market.upper()
    deployment = deployment_for(market)
    source = data_source or CompletedDailyDataSource()
    source_contract_enforced = bool(
        getattr(source, "live_contract_enforced", False)
    )
    model = NorthstarD1Model(source.config)
    requested = validate_market_symbols(market, symbols)
    universe = source.symbols(market, requested)
    results = []
    errors = []
    for symbol in universe:
        try:
            bars = source.fetch(symbol)
            metrics = backtest_long_only(
                bars,
                model,
                transaction_cost_bps=transaction_cost_bps,
            )
            info = source.universe.get_info(symbol) or {}
            results.append(
                {
                    "symbol": symbol,
                    "name": info.get("name", symbol),
                    "market_data": dict(bars.attrs.get("market_data") or {}),
                    **metrics,
                }
            )
        except Exception as exc:
            errors.append(
                {
                    "symbol": symbol,
                    "error": str(exc),
                    "fail_closed": True,
                }
            )
    freshness_ready = source_contract_enforced and bool(results) and not errors and all(
        item["market_data"].get("source") == "FUTU_OPEND_LIVE"
        and item["market_data"].get("freshness_status") == "FRESH"
        and item["market_data"].get("cache_used") is False
        and item["market_data"].get("fallback_used") is False
        for item in results
    )
    return {
        "schema_version": "2.0",
        "model_id": "NORTHSTAR_D1",
        "model_name": "Northstar-D1",
        "model_version": "1.0.0",
        "deployment_id": deployment["deployment_id"],
        "deployment_name": deployment["deployment_name"],
        "mode": "RESEARCH_ONLY",
        "validation_status": "IN_SAMPLE_DIAGNOSTIC",
        "market": market,
        "market_timezone": deployment["timezone"],
        "generated_at": datetime.now().isoformat(),
        "freshness_ready": freshness_ready,
        "market_data_contract": {
            "required_source": "FUTU_OPEND_LIVE",
            "runtime_source_contract_enforced": source_contract_enforced,
            "new_query_per_symbol": True,
            "cache_allowed": False,
            "fallback_allowed": False,
            "failure_policy": "FAIL_CLOSED",
        },
        "market_isolation": {
            "single_market_only": True,
            "combined_run_allowed": False,
            "universe_market": market,
            "output_partition": market.lower(),
        },
        "results": results,
        "errors": errors,
        "summary": {
            "symbols": len(results),
            "errors": len(errors),
            "positive_total_return": sum(r["total_return"] > 0 for r in results),
            "positive_sharpe": sum(r["sharpe_zero_rate"] > 0 for r in results),
        },
        "execution": {"enabled": False, "orders": [], "fills": []},
    }


def render_markdown(payload: Dict) -> str:
    lines = [
        f"# Northstar-D1 {payload['market']} 回测诊断",
        "",
        f"- 独立部署：`{payload['deployment_id']}`",
        f"- 状态：`{payload['validation_status']}`",
        f"- Futu新鲜度门禁：`{str(payload['freshness_ready']).lower()}`",
        "- 数据：每标的本次重新查询 Futu snapshot、交易日历和 QFQ 日线；无缓存、无fallback",
        "- 口径：long-only、信号在收盘计算并从下一时段生效、交易成本10bps",
        "- 说明：这是样本内诊断，不构成样本外放行证据",
        "",
        "| 标的 | 总收益 | 基准收益 | 年化 | Sharpe | 最大回撤 | 平均暴露 | 事件数 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in sorted(payload["results"], key=lambda x: x["total_return"], reverse=True):
        lines.append(
            f"| {item['symbol']} | {item['total_return']:.1%} | "
            f"{item['benchmark_return']:.1%} | {item['annualized_return']:.1%} | "
            f"{item['sharpe_zero_rate']:.2f} | {item['max_drawdown']:.1%} | "
            f"{item['average_exposure']:.1%} | {item['executed_signal_events']} |"
        )
    if payload["errors"]:
        lines.extend(["", "## 错误", ""])
        lines.extend(f"- {e['symbol']}: {e['error']}" for e in payload["errors"])
    lines.extend(
        [
            "",
            "## 综述结论",
            "",
            "本结果仅用于检查策略行为和回撤特征；完成样本外及稳健性验证前，不允许正式发布或接入执行。",
            "",
        ]
    )
    return "\n".join(lines)


def write_report(payload: Dict, output_root: Path = OUTPUT_ROOT) -> Dict[str, str]:
    output_dir = output_root / payload["market"].lower()
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"northstar_d1_{payload['market'].lower()}_backtest"
    json_path = output_dir / f"{stem}.json"
    md_path = output_dir / f"{stem}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def _date_at(df: pd.DataFrame, index: int) -> str:
    if "date" in df.columns:
        return str(pd.to_datetime(df["date"].iloc[index]).date())
    return str(pd.to_datetime(df.index[index]).date())


def build_market_parser(market: str) -> argparse.ArgumentParser:
    deployment = deployment_for(market)
    parser = argparse.ArgumentParser(
        description=f"{deployment['deployment_name']} diagnostic backtest"
    )
    parser.add_argument("--symbol", action="append")
    parser.add_argument("--cost-bps", type=float, default=10.0)
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    return parser


def main_for_market(market: str, argv: List[str] | None = None) -> int:
    market = market.upper()
    args = build_market_parser(market).parse_args(argv)
    payload = backtest_market(
        market,
        symbols=args.symbol,
        transaction_cost_bps=args.cost_bps,
    )
    summary = payload["summary"]
    print(
        f"[{payload['deployment_id']}] symbols={summary['symbols']} "
        f"errors={summary['errors']} "
        f"positive_return={summary['positive_total_return']} "
        f"positive_sharpe={summary['positive_sharpe']}"
    )
    if not args.no_write:
        paths = write_report(payload, args.output_root)
        print(f"  JSON: {paths['json']}")
        print(f"  MD:   {paths['markdown']}")
    return 0 if payload["results"] else 1


def main(argv: List[str] | None = None) -> int:
    print(
        "Northstar-D1 backtests are market-isolated. Use either "
        "`python -m research.northstar_d1.backtest_hk` or "
        "`python -m research.northstar_d1.backtest_us`.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
