# -*- coding: utf-8 -*-
"""
融合框架回测引擎
测试双系统融合 vs 单一系统的效果差异
"""

import sys, io, os, json
from datetime import datetime
from typing import Dict, List
from dataclasses import dataclass, asdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import warnings
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd

if 'fusion_framework.fusion_engine' in sys.modules:
    from .fusion_engine import FusionEngine, fuse_batch
    from .signal_types import SignalLevel, FusionModelSignal, XMMSignal
else:
    from fusion_framework.fusion_engine import FusionEngine, fuse_batch
    from fusion_framework.signal_types import SignalLevel, FusionModelSignal, XMMSignal


@dataclass
class BacktestTrade:
    """交易记录"""
    date: str
    symbol: str
    action: str      # BUY / SELL
    price: float
    shares: int
    amount: float
    position_pct: float
    signal_level: str
    reasoning: str


class FusionBacktest:
    """
    融合框架回测
    
    比较三种策略：
    1. 融合模型（因子打分）
    2. XMM系统（RSI超卖）
    3. 融合框架（双系统融合）
    """
    
    def __init__(self, initial_capital: float = 1000000.0,
                 commission: float = 0.001, slippage: float = 0.0005):
        self.initial_capital = initial_capital
        self.commission = commission
        self.slippage = slippage
        
        # 三种策略的资金分配
        self.capital_per_strategy = initial_capital / 3
        
        # 状态
        self.fusion_state = self._init_state(self.capital_per_strategy)
        self.fm_state = self._init_state(self.capital_per_strategy)
        self.xmm_state = self._init_state(self.capital_per_strategy)
        
        # 交易记录
        self.fusion_trades = []
        self.fm_trades = []
        self.xmm_trades = []
        
        self.engine = FusionEngine()
    
    def _init_state(self, capital: float) -> dict:
        return {
            'cash': capital,
            'position': 0.0,   # 持仓数量
            'position_value': 0.0,
            'total_value': capital,
        }
    
    def _apply_trade(self, state: dict, trade: BacktestTrade, price_data: Dict) -> dict:
        """执行一笔交易"""
        if trade.action == 'BUY':
            # 扣除手续费和滑点
            cost = trade.amount * (1 + self.commission + self.slippage)
            if cost <= state['cash']:
                state['cash'] -= cost
                state['position'] += trade.shares
                state['position_value'] = state['position'] * price_data['close']
                state['total_value'] = state['cash'] + state['position_value']
        
        elif trade.action == 'SELL':
            revenue = trade.amount * (1 - self.commission - self.slippage)
            state['cash'] += revenue
            state['position'] = 0
            state['position_value'] = 0
            state['total_value'] = state['cash']
        
        return state
    
    def run_backtest(self,
                     signals: List,  # FusionSignal列表
                     price_data: pd.DataFrame,
                     strategy: str = 'fusion') -> Dict:
        """
        运行回测
        
        Args:
            signals: 融合信号列表
            price_data: 价格数据
            strategy: 'fusion' / 'fusion_model' / 'xmm_system'
            
        Returns:
            回测结果
        """
        if strategy == 'fusion':
            state = self.fusion_state
            trades = self.fusion_trades
        elif strategy == 'fusion_model':
            state = self.fm_state
            trades = self.fm_trades
        else:
            state = self.xmm_state
            trades = self.xmm_trades
        
        # 每日更新
        daily_values = []
        for _, row in price_data.iterrows():
            date = row['trade_date']
            close = row['close']
            
            # 更新持仓市值
            if state['position'] > 0:
                state['position_value'] = state['position'] * close
                state['total_value'] = state['cash'] + state['position_value']
            
            daily_values.append({
                'date': date,
                'value': state['total_value'],
                'position': state['position'],
            })
            
            # 检查是否有信号需要执行
            signal = self._get_signal(signals, date, strategy)
            if signal and signal.level != SignalLevel.HOLD:
                if state['position'] == 0 and signal.level in (
                    SignalLevel.STRONG_BUY, SignalLevel.BUY):
                    # 买入
                    position_value = state['total_value'] * signal.position_pct
                    shares = int(position_value / close)
                    amount = shares * close
                    if shares > 0:
                        trade = BacktestTrade(
                            date=date, symbol=signal.symbol,
                            action='BUY', price=close, shares=shares,
                            amount=amount, position_pct=signal.position_pct,
                            signal_level=signal.level.value,
                            reasoning=signal.reasoning
                        )
                        state = self._apply_trade(state, trade, row.to_dict())
                        trades.append(trade)
                
                elif state['position'] > 0 and signal.level in (
                    SignalLevel.STRONG_SELL, SignalLevel.SELL):
                    # 卖出
                    shares = state['position']
                    amount = shares * close
                    trade = BacktestTrade(
                        date=date, symbol=signal.symbol,
                        action='SELL', price=close, shares=shares,
                        amount=amount, position_pct=0,
                        signal_level=signal.level.value,
                        reasoning=signal.reasoning
                    )
                    state = self._apply_trade(state, trade, row.to_dict())
                    trades.append(trade)
        
        # 计算指标
        df = pd.DataFrame(daily_values)
        df['returns'] = df['value'].pct_change()
        
        total_return = (df['value'].iloc[-1] / self.initial_capital - 1) * 100
        sharpe = df['returns'].mean() / df['returns'].std() * np.sqrt(252) if df['returns'].std() > 0 else 0
        max_dd = self._max_drawdown(df['value'])
        
        return {
            'strategy': strategy,
            'total_return': total_return,
            'sharpe': sharpe,
            'max_drawdown': max_dd,
            'num_trades': len(trades),
            'final_value': df['value'].iloc[-1],
            'daily_values': daily_values,
        }
    
    def _get_signal(self, signals: List, date: str, strategy: str):
        """获取当日信号"""
        for s in signals:
            if s.date == date:
                return s
        return None
    
    def _max_drawdown(self, values: pd.Series) -> float:
        """计算最大回撤"""
        peak = values.expanding(min_periods=1).max()
        drawdown = (values - peak) / peak
        return drawdown.min() * 100


def run_comparison_report(signals: List, spy_data: pd.DataFrame):
    """
    运行三策略对比报告
    """
    print("=" * 70)
    print("融合框架 vs 单一系统 - 对比报告")
    print("=" * 70)
    
    bt = FusionBacktest(initial_capital=300000)  # 三等分30万
    
    results = {}
    
    # 融合框架回测
    if any(s.level != SignalLevel.HOLD for s in signals):
        results['融合框架'] = bt.run_backtest(signals, spy_data, 'fusion')
    
    # 生成对比表
    print(f"\n{'策略':<15} {'收益':>10} {'夏普':>8} {'最大回撤':>10} {'交易数':>8}")
    print("-" * 55)
    
    for name, r in results.items():
        print(f"{name:<15} {r['total_return']:>+9.2f}% {r['sharpe']:>8.2f} "
              f"{r['max_drawdown']:>+9.2f}% {r['num_trades']:>8}")
    
    return results
