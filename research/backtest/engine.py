"""
research/backtest/engine.py — 轻量事件驱动回测引擎

支持:
- 基于因子信号的策略回测
- 多标的组合回测
- 自定义交易成本
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd
import numpy as np


@dataclass
class BacktestResult:
    """回测结果"""
    equity_curve: pd.Series
    returns: pd.Series
    signals: pd.DataFrame
    positions: pd.DataFrame
    metrics: Dict[str, float] = field(default_factory=dict)
    trades: pd.DataFrame = field(default_factory=pd.DataFrame)


class BacktestEngine:
    """轻量回测引擎"""

    def __init__(self, initial_capital: float = 1_000_000,
                 commission_pct: float = 0.0003,
                 slippage_pct: float = 0.0001):
        """
        Args:
            initial_capital: 初始资金
            commission_pct: 手续费比例
            slippage_pct: 滑点比例
        """
        self.initial_capital = initial_capital
        self.commission_pct = commission_pct
        self.slippage_pct = slippage_pct

    def run(self, signals: pd.DataFrame,
            prices: pd.DataFrame,
            max_position: float = 0.2,
            max_total: float = 0.8) -> BacktestResult:
        """运行回测

        Args:
            signals: 信号 DataFrame（-1 ~ 1），列为标的
            prices: 收盘价 DataFrame，列为标的
            max_position: 单个标的最大仓位
            max_total: 总仓位上限

        Returns:
            BacktestResult
        """
        n_assets = len(signals.columns)
        capital = self.initial_capital
        equity = capital
        equity_curve = []
        positions_list = []
        trades = []

        for i in range(len(signals) - 1):
            date = signals.index[i]
            next_date = signals.index[i + 1]

            # 获取当前信号（基于 close[T] 计算，需预测 return[T→T+1]）
            current_signal = signals.loc[date].fillna(0).clip(-1, 1)

            # 仓位计算（等权缩放）
            target_weights = current_signal.values * max_position
            target_weights = target_weights.clip(-max_position, max_position)
            total_exposure = abs(target_weights).sum()
            if total_exposure > max_total:
                target_weights = target_weights * (max_total / total_exposure)

            # 记录仓位
            positions_list.append(target_weights.copy())

            # 计算收益：在 date (T) 收盘建仓，持有到 next_date (T+1)
            if date in prices.index and next_date in prices.index:
                rets = prices.loc[next_date].values / prices.loc[date].values - 1
                rets = np.nan_to_num(rets)

                # 在 date 收盘以 target_weights 建仓，持有到 next_date 收盘
                portfolio_ret = np.dot(target_weights, rets)
                # 成本
                prev_weights = positions_list[-2] if len(positions_list) > 1 else np.zeros(n_assets)
                turnover = np.sum(abs(target_weights - prev_weights))
                cost = turnover * (self.commission_pct + self.slippage_pct)
                net_ret = portfolio_ret - cost

                equity *= (1 + net_ret)
                equity_curve.append(equity)

                # 记录交易
                if abs(turnover) > 0.001:
                    trades.append({
                        "date": date,
                        "turnover": turnover,
                        "net_return": net_ret,
                        "equity": equity,
                    })

        equity_series = pd.Series(equity_curve,
                                  index=signals.index[:len(equity_curve)])
        returns_series = equity_series.pct_change().fillna(0)

        positions_df = pd.DataFrame(positions_list,
                                    index=signals.index[:len(positions_list)],
                                    columns=signals.columns)
        trades_df = pd.DataFrame(trades) if trades else pd.DataFrame()

        return BacktestResult(
            equity_curve=equity_series,
            returns=returns_series,
            signals=signals,
            positions=positions_df,
            trades=trades_df,
        )