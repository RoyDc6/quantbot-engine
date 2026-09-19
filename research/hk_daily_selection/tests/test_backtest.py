from __future__ import annotations

from dataclasses import replace

import pandas as pd

from hk_daily_selection.backtest import run_backtest
from hk_daily_selection.config import StrategyConfig
from hk_daily_selection.contracts import prepare_bars


def _manual_backtest_fixture() -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = pd.bdate_range("2026-01-05", periods=7)
    rows = []
    for index, date in enumerate(dates):
        for symbol, price in [
            ("800701.HK", 100.0),
            ("00001.HK", 10.0 + index),
        ]:
            rows.append(
                {
                    "date": date,
                    "symbol": symbol,
                    "open": price,
                    "high": price * 1.01,
                    "low": price * 0.99,
                    "close": price,
                    "volume": 1_000_000,
                    "turnover": price * 1_000_000,
                    "is_suspended": False,
                }
            )
    bars = prepare_bars(pd.DataFrame(rows))
    scores = pd.DataFrame(
        {
            "date": dates,
            "symbol": "00001.HK",
            "selected": True,
            "eligible": True,
            "rank": 1.0,
            "composite_score": 0.8,
            "regime": "ON",
            "research_capacity_hkd": 80_000.0,
        }
    )
    return scores, bars


def _zero_cost_config() -> StrategyConfig:
    return StrategyConfig(
        top_k=1,
        caution_top_k=1,
        exit_rank=1,
        min_hold_days=3,
        max_hold_days=3,
        statutory_levies_bps_per_side=0.0,
        stamp_duty_bps_per_side=0.0,
        broker_commission_bps_per_side=0.0,
        slippage_bps_per_side=0.0,
    )


def test_signal_executes_at_next_open_and_respects_max_hold() -> None:
    scores, bars = _manual_backtest_fixture()
    result = run_backtest(scores, bars, _zero_cost_config())
    trades = result.trades
    first_buy = trades[trades["side"] == "BUY"].iloc[0]
    first_sell = trades[trades["side"] == "SELL"].iloc[0]

    assert first_buy["signal_date"] == scores["date"].iloc[0]
    assert first_buy["trade_date"] == scores["date"].iloc[1]
    assert first_buy["price"] == 11.0
    assert first_buy["notional_hkd"] <= 80_000.0
    assert first_sell["trade_date"] == scores["date"].iloc[4]
    assert first_sell["held_sessions"] == 3
    assert first_sell["reason"] == "max_hold"
    same_day_reentry = trades[
        (trades["side"] == "BUY")
        & (trades["symbol"] == first_sell["symbol"])
        & (trades["trade_date"] == first_sell["trade_date"])
    ]
    assert same_day_reentry.empty


def test_transaction_costs_reduce_equity() -> None:
    scores, bars = _manual_backtest_fixture()
    zero_cost = _zero_cost_config()
    with_cost = replace(
        zero_cost,
        statutory_levies_bps_per_side=1.27,
        stamp_duty_bps_per_side=10.0,
        broker_commission_bps_per_side=3.0,
        slippage_bps_per_side=10.0,
    )
    clean = run_backtest(scores, bars, zero_cost)
    charged = run_backtest(scores, bars, with_cost)

    assert charged.metrics["total_cost_hkd"] > 0.0
    assert charged.equity_curve["equity"].iloc[-1] < clean.equity_curve[
        "equity"
    ].iloc[-1]
