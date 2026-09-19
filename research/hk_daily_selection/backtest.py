"""Next-open, long-only research backtest for the daily selection strategy."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .config import StrategyConfig


@dataclass
class Position:
    symbol: str
    units: float
    entry_date: pd.Timestamp
    entry_price: float
    entry_notional: float
    held_sessions: int = 0
    last_price: float = 0.0


@dataclass(frozen=True)
class BacktestResult:
    equity_curve: pd.DataFrame
    trades: pd.DataFrame
    metrics: Dict[str, float]
    forward_metrics: pd.DataFrame = field(default_factory=pd.DataFrame)
    warnings: tuple[str, ...] = ()


def _cost(notional: float, bps_per_side: float) -> float:
    return abs(float(notional)) * float(bps_per_side) / 10_000.0


def _price_map(bars: pd.DataFrame) -> Dict[tuple[pd.Timestamp, str], pd.Series]:
    return {
        (pd.Timestamp(row.date), str(row.symbol)): row
        for row in bars.itertuples(index=False)
    }


def _compute_metrics(
    equity: pd.DataFrame,
    trades: pd.DataFrame,
    config: StrategyConfig,
) -> Dict[str, float]:
    if equity.empty:
        return {
            "total_return_pct": 0.0,
            "annualized_return_pct": 0.0,
            "annualized_volatility_pct": 0.0,
            "sharpe_ratio": 0.0,
            "max_drawdown_pct": 0.0,
            "positive_day_rate_pct": 0.0,
            "completed_trades": 0,
            "average_holding_days": 0.0,
            "average_exposure_pct": 0.0,
            "total_cost_hkd": 0.0,
        }

    returns = equity["equity"].pct_change(fill_method=None).dropna()
    total_return = equity["equity"].iloc[-1] / equity["equity"].iloc[0] - 1.0
    periods = max(len(returns), 1)
    annualized_return = (1.0 + total_return) ** (
        config.annualization_days / periods
    ) - 1.0
    annualized_volatility = (
        returns.std(ddof=0) * np.sqrt(config.annualization_days)
        if not returns.empty
        else 0.0
    )
    sharpe = (
        returns.mean() / returns.std(ddof=0)
        * np.sqrt(config.annualization_days)
        if len(returns) > 1 and returns.std(ddof=0) > 0
        else 0.0
    )
    running_max = equity["equity"].cummax()
    drawdown = equity["equity"].div(running_max).sub(1.0)
    exits = trades[trades["side"] == "SELL"] if not trades.empty else trades

    return {
        "total_return_pct": float(total_return * 100.0),
        "annualized_return_pct": float(annualized_return * 100.0),
        "annualized_volatility_pct": float(annualized_volatility * 100.0),
        "sharpe_ratio": float(sharpe),
        "max_drawdown_pct": float(drawdown.min() * 100.0),
        "positive_day_rate_pct": float(
            (returns > 0).mean() * 100.0 if not returns.empty else 0.0
        ),
        "completed_trades": int(len(exits)),
        "average_holding_days": float(
            exits["held_sessions"].mean() if not exits.empty else 0.0
        ),
        "average_exposure_pct": float(
            equity["gross_exposure"].mean() * 100.0
        ),
        "total_cost_hkd": float(
            trades["cost_hkd"].sum() if not trades.empty else 0.0
        ),
    }


def evaluate_forward_returns(
    scores: pd.DataFrame,
    bars: pd.DataFrame,
    horizons: tuple[int, ...] = (3, 5, 10),
) -> pd.DataFrame:
    """Evaluate cross-sectional rank IC and selected-vs-universe returns."""

    if scores.empty:
        return pd.DataFrame()
    closes = bars.pivot(index="date", columns="symbol", values="close").sort_index()
    records: List[Dict[str, float]] = []
    for horizon in horizons:
        forward = closes.shift(-horizon).div(closes).sub(1.0)
        daily_ics: List[float] = []
        selected_returns: List[float] = []
        universe_returns: List[float] = []

        for date, group in scores.groupby("date", sort=True):
            eligible = group[group["eligible"] & group["composite_score"].notna()]
            if len(eligible) < 3 or date not in forward.index:
                continue
            returns = forward.loc[date].reindex(eligible["symbol"]).astype(float)
            valid = returns.notna()
            if valid.sum() < 3:
                continue
            factor_rank = eligible.loc[valid.to_numpy(), "composite_score"].rank()
            return_rank = returns[valid].rank()
            ic = factor_rank.reset_index(drop=True).corr(
                return_rank.reset_index(drop=True)
            )
            if pd.notna(ic):
                daily_ics.append(float(ic))

            selected_symbols = group.loc[group["selected"], "symbol"]
            selected_values = forward.loc[date].reindex(selected_symbols).dropna()
            universe_values = returns[valid]
            if not selected_values.empty:
                selected_returns.append(float(selected_values.mean()))
                universe_returns.append(float(universe_values.mean()))

        records.append(
            {
                "horizon_days": int(horizon),
                "mean_rank_ic": float(np.mean(daily_ics)) if daily_ics else np.nan,
                "rank_ic_positive_rate": (
                    float(np.mean(np.asarray(daily_ics) > 0.0))
                    if daily_ics
                    else np.nan
                ),
                "selected_mean_return_pct": (
                    float(np.mean(selected_returns) * 100.0)
                    if selected_returns
                    else np.nan
                ),
                "universe_mean_return_pct": (
                    float(np.mean(universe_returns) * 100.0)
                    if universe_returns
                    else np.nan
                ),
                "selected_excess_return_pct": (
                    float(
                        (np.mean(selected_returns) - np.mean(universe_returns))
                        * 100.0
                    )
                    if selected_returns and universe_returns
                    else np.nan
                ),
                "observations": int(len(selected_returns)),
            }
        )
    return pd.DataFrame(records)


def run_backtest(
    scores: pd.DataFrame,
    bars: pd.DataFrame,
    config: StrategyConfig | None = None,
    *,
    start: Optional[pd.Timestamp] = None,
    end: Optional[pd.Timestamp] = None,
    warnings: tuple[str, ...] = (),
) -> BacktestResult:
    """Run the strategy using T close signals and earliest T+1 open fills.

    Prices are treated as a continuous research price series. Board-lot,
    corporate-action cash-flow, and market-impact simulation are intentionally
    deferred until point-in-time event data are available.
    """

    cfg = config or StrategyConfig()
    if scores.empty:
        return BacktestResult(
            pd.DataFrame(),
            pd.DataFrame(),
            _compute_metrics(pd.DataFrame(), pd.DataFrame(), cfg),
            warnings=warnings,
        )

    trading_dates = pd.DatetimeIndex(
        sorted(bars.loc[bars["symbol"] == cfg.benchmark_symbol, "date"].unique())
    )
    if len(trading_dates) == 0:
        trading_dates = pd.DatetimeIndex(sorted(bars["date"].unique()))
    if start is not None:
        trading_dates = trading_dates[trading_dates >= pd.Timestamp(start)]
    if end is not None:
        trading_dates = trading_dates[trading_dates <= pd.Timestamp(end)]
    if len(trading_dates) < 2:
        raise ValueError("backtest requires at least two trading dates")

    price_lookup = _price_map(bars)
    cash = float(cfg.initial_capital_hkd)
    positions: Dict[str, Position] = {}
    equity_records: List[Dict[str, float]] = []
    trade_records: List[Dict[str, object]] = []
    prior_date: Optional[pd.Timestamp] = None

    for date in trading_dates:
        date = pd.Timestamp(date)
        if prior_date is None:
            equity_records.append(
                {
                    "date": date,
                    "equity": cash,
                    "cash": cash,
                    "gross_exposure": 0.0,
                    "positions": 0,
                }
            )
            prior_date = date
            continue

        prior_scores = scores[scores["date"] == prior_date].copy()
        prior_by_symbol = (
            prior_scores.set_index("symbol") if not prior_scores.empty else pd.DataFrame()
        )
        prior_regime = (
            str(prior_scores["regime"].iloc[0])
            if not prior_scores.empty
            else "UNKNOWN"
        )

        # Exit decisions are based exclusively on the prior completed close.
        exit_symbols: List[tuple[str, str]] = []
        exited_today: set[str] = set()
        for symbol, position in list(positions.items()):
            row = (
                prior_by_symbol.loc[symbol]
                if not prior_by_symbol.empty and symbol in prior_by_symbol.index
                else None
            )
            reason = ""
            if position.held_sessions >= cfg.max_hold_days:
                reason = "max_hold"
            elif position.held_sessions >= cfg.min_hold_days:
                if row is None or not bool(row.get("eligible", False)):
                    reason = "eligibility_exit"
                elif pd.isna(row.get("rank")) or row.get("rank") > cfg.exit_rank:
                    reason = "rank_exit"
                elif prior_regime == "OFF":
                    reason = "regime_exit"
            if reason:
                exit_symbols.append((symbol, reason))

        for symbol, reason in exit_symbols:
            bar = price_lookup.get((date, symbol))
            if bar is None or bool(getattr(bar, "is_suspended", False)):
                continue
            price = float(bar.open)
            if not np.isfinite(price) or price <= 0:
                continue
            position = positions.pop(symbol)
            exited_today.add(symbol)
            notional = position.units * price
            cost_hkd = _cost(notional, cfg.all_in_cost_bps_per_side)
            cash += notional - cost_hkd
            trade_records.append(
                {
                    "signal_date": prior_date,
                    "trade_date": date,
                    "symbol": symbol,
                    "side": "SELL",
                    "price": price,
                    "notional_hkd": notional,
                    "cost_hkd": cost_hkd,
                    "held_sessions": position.held_sessions,
                    "reason": reason,
                }
            )

        if prior_regime == "ON":
            position_limit = cfg.top_k
        elif prior_regime == "CAUTION":
            position_limit = cfg.caution_top_k
        elif prior_regime == "UNKNOWN" and cfg.allow_unknown_regime:
            position_limit = cfg.caution_top_k
        else:
            position_limit = 0

        entry_candidates = prior_scores[prior_scores["selected"]].sort_values(
            ["rank", "symbol"]
        )
        entry_candidates = entry_candidates[
            ~entry_candidates["symbol"].isin(set(positions) | exited_today)
        ]
        available_slots = max(position_limit - len(positions), 0)
        candidates = entry_candidates.head(available_slots)

        open_position_value = 0.0
        for symbol, position in positions.items():
            bar = price_lookup.get((date, symbol))
            mark = (
                float(bar.open)
                if bar is not None and float(bar.open) > 0
                else position.last_price
            )
            open_position_value += position.units * mark
        equity_at_open = cash + open_position_value
        target_slot_value = (
            equity_at_open / position_limit if position_limit > 0 else 0.0
        )

        remaining = len(candidates)
        for row in candidates.itertuples(index=False):
            bar = price_lookup.get((date, row.symbol))
            if (
                bar is None
                or bool(getattr(bar, "is_suspended", False))
                or float(bar.open) <= 0
            ):
                remaining -= 1
                continue
            price = float(bar.open)
            affordable = cash / (
                1.0 + cfg.all_in_cost_bps_per_side / 10_000.0
            )
            equal_cash_share = affordable / max(remaining, 1)
            research_capacity = float(
                getattr(row, "research_capacity_hkd", np.inf)
            )
            if not np.isfinite(research_capacity):
                research_capacity = np.inf
            notional = min(
                target_slot_value,
                equal_cash_share,
                research_capacity,
            )
            if notional <= 0:
                break
            units = notional / price
            cost_hkd = _cost(notional, cfg.all_in_cost_bps_per_side)
            cash -= notional + cost_hkd
            positions[row.symbol] = Position(
                symbol=row.symbol,
                units=units,
                entry_date=date,
                entry_price=price,
                entry_notional=notional,
                held_sessions=0,
                last_price=price,
            )
            trade_records.append(
                {
                    "signal_date": prior_date,
                    "trade_date": date,
                    "symbol": row.symbol,
                    "side": "BUY",
                    "price": price,
                    "notional_hkd": notional,
                    "cost_hkd": cost_hkd,
                    "held_sessions": 0,
                    "reason": "daily_selection",
                }
            )
            remaining -= 1

        close_value = 0.0
        for symbol, position in positions.items():
            bar = price_lookup.get((date, symbol))
            if bar is not None and float(bar.close) > 0:
                position.last_price = float(bar.close)
            close_value += position.units * position.last_price
            position.held_sessions += 1

        equity_value = cash + close_value
        gross_exposure = close_value / equity_value if equity_value > 0 else 0.0
        equity_records.append(
            {
                "date": date,
                "equity": equity_value,
                "cash": cash,
                "gross_exposure": gross_exposure,
                "positions": len(positions),
            }
        )
        prior_date = date

    equity_frame = pd.DataFrame(equity_records).set_index("date")
    trades_frame = pd.DataFrame(trade_records)
    metrics = _compute_metrics(equity_frame, trades_frame, cfg)
    forward = evaluate_forward_returns(scores, bars)
    return BacktestResult(
        equity_curve=equity_frame,
        trades=trades_frame,
        metrics=metrics,
        forward_metrics=forward,
        warnings=warnings,
    )
