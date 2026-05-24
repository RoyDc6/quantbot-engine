# -*- coding: utf-8 -*-
"""
core/position_manager.py - QuantBot 统一仓位管理模块
功能:
  1. 分批建仓（3批，首批 1/3）
  2. 信号反转清仓
  3. Futu 持仓同步（唯一持仓真相源）
  4. 建仓/平仓决策逻辑

参数来源: config.py
"""
import os
import json
from datetime import datetime
from pathlib import Path

from .utils import dict_json_safe


# === 分批建仓管理 ================================================
class StagedEntryManager:
    """分批建仓管理器。
    
    流程: 首批建 1/3 → 确认条件满足 → 加仓第2批 → 加仓第3批 → 完成
    确认条件: 浮亏 ≤ 阈值 + 信号仍为 BUY
    """

    def __init__(self, total_tranches=3, first_pct=0.34, min_gap_days=1,
                 pnl_above=-0.02, signal_still_buy=True):
        self.total_tranches = total_tranches
        self.first_pct = first_pct
        self.min_gap_days = min_gap_days
        self.pnl_above = pnl_above
        self.signal_still_buy = signal_still_buy
        self.plans = {}  # {symbol: plan_dict}

    def load_from_dict(self, staged_dict):
        """从 portfolio 的 staged_entries 字段加载。"""
        self.plans = dict(staged_dict) if staged_dict else {}

    def to_dict(self):
        """导出为 dict（可直接写入 portfolio.json）。"""
        return dict(self.plans)

    def register(self, symbol, total_shares, today):
        """注册新的分批建仓计划。返回首批股数。"""
        first_shares = int(total_shares * self.first_pct / 100) * 100
        if first_shares < 100:
            first_shares = min(100, total_shares)

        self.plans[symbol] = {
            'target_shares': total_shares,
            'filled_shares': first_shares,
            'remaining_shares': total_shares - first_shares,
            'tranches_filled': 1,
            'total_tranches': self.total_tranches,
            'last_fill_date': today,
            'avg_price': 0,
        }
        return first_shares

    def check_topup(self, symbol, current_price, pnl_pct, current_signal, today):
        """检查某标的是否需要加仓。
        
        Args:
            symbol: 标的代码
            current_price: 当前价格
            pnl_pct: 当前浮盈（如 0.05 = 5%）
            current_signal: 当前信号级别 ('BUY', 'STRONG_BUY', ...)
            today: 今天日期字符串
        
        Returns:
            dict: {'should_topup': bool, 'shares': int, 'reason': str}
        """
        plan = self.plans.get(symbol)
        if not plan:
            return {'should_topup': False, 'shares': 0, 'reason': '无分批计划'}

        # 已全部建完
        if plan['remaining_shares'] <= 0:
            return {'should_topup': False, 'shares': 0, 'reason': '已完成'}

        # 批次间隔检查
        last_fill = datetime.strptime(plan['last_fill_date'], '%Y-%m-%d')
        days_since = (datetime.strptime(today, '%Y-%m-%d') - last_fill).days
        if days_since < self.min_gap_days:
            return {'should_topup': False, 'shares': 0, 'reason': f'间隔不足({days_since}<{self.min_gap_days}天)'}

        # 浮亏检查
        if pnl_pct < self.pnl_above:
            return {'should_topup': False, 'shares': 0, 'reason': f'浮亏{pnl_pct:.1%}超阈值{self.pnl_above:.0%}'}

        # 信号仍为买入
        if self.signal_still_buy and current_signal not in ('STRONG_BUY', 'BUY'):
            return {'should_topup': False, 'shares': 0, 'reason': f'信号已非BUY({current_signal})'}

        # 计算本次加仓股数
        tranches_left = plan['total_tranches'] - plan['tranches_filled']
        if tranches_left <= 0:
            return {'should_topup': False, 'shares': 0, 'reason': '批次已满'}

        next_shares = int(plan['remaining_shares'] / tranches_left / 100) * 100
        if next_shares < 100:
            next_shares = plan['remaining_shares']
        if next_shares <= 0:
            return {'should_topup': False, 'shares': 0, 'reason': '股数为0'}

        return {'should_topup': True, 'shares': next_shares, 'reason': f'加仓批次{plan["tranches_filled"]+1}/{plan["total_tranches"]}'}

    def record_topup(self, symbol, shares, price, today):
        """记录一次加仓完成。"""
        plan = self.plans.get(symbol)
        if not plan:
            return
        plan['filled_shares'] += shares
        plan['remaining_shares'] -= shares
        plan['tranches_filled'] += 1
        plan['last_fill_date'] = today
        # 清理已完成的计划
        if plan['remaining_shares'] <= 0:
            pass  # 保留状态供查询，后续可清理

    def remove(self, symbol):
        """移除分批计划（清仓时调用）。"""
        self.plans.pop(symbol, None)


# === 信号反转清仓 ================================================
class SignalReversalChecker:
    """信号反转清仓检查器。"""

    def __init__(self, trigger_levels=None, exclude_if_pnl_above=0.05):
        self.trigger_levels = trigger_levels or ['SELL', 'STRONG_SELL']
        self.exclude_if_pnl_above = exclude_if_pnl_above

    def should_exit(self, current_signal, pnl_pct):
        """检查是否应该信号反转清仓。
        
        Args:
            current_signal: 当前信号级别
            pnl_pct: 浮盈比例（如 0.05 = 5%）
        
        Returns:
            (should_exit: bool, reason: str)
        """
        if current_signal not in self.trigger_levels:
            return False, ''

        # 浮盈超过阈值，让利润跑
        if pnl_pct > self.exclude_if_pnl_above:
            return False, f'信号{current_signal}但浮盈{pnl_pct:+.1%}，让利润跑'

        return True, f'信号反转清仓: {current_signal}'


# === 信号质量过滤 ================================================
class SignalFilter:
    """信号质量过滤器。"""

    # 默认阈值（从 config.py）
    DEFAULT_THRESHOLDS = {
        'STRONG_BUY':  {'min_score': 50, 'min_confidence': 0.75},
        'BUY':         {'min_score': 40, 'min_confidence': 0.65},
        'SELL':        {'min_score': -40, 'min_confidence': 0.65},
        'STRONG_SELL': {'min_score': -50, 'min_confidence': 0.75},
    }

    def __init__(self, thresholds=None):
        self.thresholds = thresholds or self.DEFAULT_THRESHOLDS

    def check(self, signal_level, score, confidence):
        """检查信号是否达到质量门槛。
        
        Returns:
            (passed: bool, reason: str)
        """
        threshold = self.thresholds.get(signal_level)
        if not threshold:
            return True, '未知信号类型，默认通过'

        if abs(score) < threshold['min_score']:
            return False, f'分数不足: {abs(score):.0f} < {threshold["min_score"]}'

        if confidence < threshold['min_confidence']:
            return False, f'置信度不足: {confidence:.2f} < {threshold["min_confidence"]}'

        return True, 'OK'


# === 建仓金额计算 ================================================
def calc_buy_shares(price, budget, lot_size=100, min_shares=100):
    """计算建仓股数（整手对齐）。
    
    Args:
        price: 当前价格
        budget: 预算金额
        lot_size: 每手股数（港股100，美股1）
        min_shares: 最低买入股数
    
    Returns:
        int: 股数（已整手对齐），0 表示买不起
    """
    if price <= 0 or budget <= 0:
        return 0
    shares = int(budget / price / lot_size) * lot_size
    if shares < min_shares:
        shares = int(budget / price) * lot_size  # 再试一次
        if shares < min_shares:
            return 0
    return max(shares, 0)


def calc_signal_budget(signal_level, fusion_score, total_assets, held_count, max_positions=5):
    """根据信号级别和持仓情况计算建仓预算。
    
    Args:
        signal_level: 'STRONG_BUY' / 'BUY'
        fusion_score: 融合信号分数
        total_assets: 总资产
        held_count: 当前持仓数量
        max_positions: 最大持仓数
    
    Returns:
        float: 建仓预算金额
    """
    if held_count >= max_positions:
        return 0.0

    if signal_level == 'STRONG_BUY':
        # STRONG_BUY: 固定分配总资产的 20%
        return total_assets * 0.20
    elif signal_level == 'BUY':
        # BUY: 按分数权重，保守分配
        return total_assets * 0.10
    else:
        return 0.0
