"""
research/backtest/metrics.py — 回测绩效指标
"""

from __future__ import annotations

from typing import Dict

import pandas as pd
import numpy as np

from ..models.evaluation import calc_sharpe, calc_max_drawdown


def compute_backtest_metrics(result: "BacktestResult",
                             annual_factor: int = 252) -> Dict[str, float]:
    """计算回测综合绩效指标"""
    from .engine import BacktestResult

    rets = result.returns
    eq = result.equity_curve
    trades = result.trades

    total_return = (eq.iloc[-1] / eq.iloc[0]) - 1 if len(eq) > 1 else 0.0
    sharpe = calc_sharpe(rets, annual_factor)
    max_dd = calc_max_drawdown(eq)
    hit_rate = (rets > 0).sum() / max(len(rets), 1)
    volatility = rets.std() * np.sqrt(annual_factor)

    # Calmar Ratio
    calmar = total_return / max_dd if max_dd > 0 else 0.0

    # 交易统计
    n_trades = len(trades) if not trades.empty else 0
    avg_turnover = trades["turnover"].mean() if n_trades > 0 else 0.0

    return {
        "total_return_pct": round(total_return * 100, 2),
        "sharpe_ratio": round(sharpe, 3),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "calmar_ratio": round(calmar, 3),
        "annual_volatility_pct": round(volatility * 100, 2),
        "hit_rate": round(hit_rate, 4),
        "n_trades": n_trades,
        "avg_turnover": round(avg_turnover, 4),
    }