# -*- coding: utf-8 -*-
"""
core/stop_loss.py - QuantBot 统一止损/风控模块
三重止损: 固定止损 + 移动止损 + ATR自适应
风控: 仓位限制、组合回撤、冷却期

参数来源: config.py（单一真相源）
"""
import os
import json
from datetime import datetime
from pathlib import Path

from .utils import calc_atr


# === 默认参数（从 config.py 复制，运行时由 unified_runner 传入）===
_DEFAULTS = {
    'fixed_stop_pct': -0.12,
    'trailing_stop_pct': -0.12,
    'portfolio_dd_pct': -0.30,
    'take_profit_pct_hk': 0.15,
    'take_profit_pct_us': 0.20,
    'max_position_pct': 0.20,
    'max_total_pct': 0.80,
    'atr_low_vol_thresh': 0.025,
    'atr_low_stop_pct': -0.15,
    'stop_cooldown_days': 10,
}


# === 风控管理器 ==================================================
class RiskManager:
    """统一风控管理器 — 管理持仓状态、止损检测、仓位限制。
    
    持久化: 通过 state_file 保存/恢复风控状态（最高价、冷却期等）
    """

    def __init__(self, state_file=None, **kwargs):
        """
        Args:
            state_file: 状态持久化文件路径（可选）
            **kwargs: 覆盖默认风控参数
        """
        # 合并参数
        self.cfg = {**_DEFAULTS, **kwargs}

        # 状态文件
        self.state_file = state_file
        self._state = self._load_state()

        # 运行时状态
        self.highest_prices = dict(self._state.get('highest_prices', {}))
        self.entry_prices = dict(self._state.get('entry_prices', {}))
        self.portfolio_peak = self._state.get('portfolio_peak', 0)
        self.stop_timestamps = dict(self._state.get('stop_timestamps', {}))
        self.latest_atr = dict(self._state.get('latest_atr', {}))

    # === 状态持久化 ==============================================
    def _load_state(self):
        if self.state_file and os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def save_state(self):
        """保存风控状态到文件。"""
        if not self.state_file:
            return
        self._state.update({
            'highest_prices': self.highest_prices,
            'entry_prices': self.entry_prices,
            'portfolio_peak': self.portfolio_peak,
            'stop_timestamps': self.stop_timestamps,
            'latest_atr': self.latest_atr,
            'last_update': datetime.now().isoformat(),
        })
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        with open(self.state_file, 'w', encoding='utf-8') as f:
            json.dump(self._state, f, indent=2, ensure_ascii=False, default=str)

    # === 持仓状态更新 ============================================
    def init_position(self, code, cost_price, current_price):
        """初始化新持仓的风控追踪。"""
        if code not in self.entry_prices:
            self.entry_prices[code] = cost_price
        if code not in self.highest_prices:
            self.highest_prices[code] = current_price

    def update_highest(self, code, price):
        """更新持仓最高价（移动止损用）。"""
        if price > self.highest_prices.get(code, 0):
            self.highest_prices[code] = price

    def update_portfolio_peak(self, total_value):
        """更新组合净值峰值。"""
        if total_value > self.portfolio_peak:
            self.portfolio_peak = total_value

    def set_atr_pct(self, code, atr_pct):
        """记录标的 ATR 占比（自适应止损用）。"""
        self.latest_atr[code] = atr_pct

    def cleanup_closed(self, active_codes):
        """清理已平仓标的的风控状态。"""
        for code in list(self.entry_prices.keys()):
            if code not in active_codes:
                self.entry_prices.pop(code, None)
                self.highest_prices.pop(code, None)
                self.latest_atr.pop(code, None)
        self.save_state()

    # === 三重止损检测 ============================================
    def check_fixed_stop(self, code, current_price):
        """固定止损: 从成本价下跌超过阈值。
        
        Returns:
            (triggered: bool, pnl_pct: float)
        """
        cost = self.entry_prices.get(code, 0)
        if cost <= 0:
            return False, 0.0
        pnl = (current_price / cost - 1)
        stop_pct = self._get_effective_stop(code)
        return pnl <= stop_pct, pnl

    def check_trailing_stop(self, code, current_price):
        """移动止损: 从持仓最高价回撤超过阈值。
        
        Returns:
            (triggered: bool, drawdown_pct: float)
        """
        high = self.highest_prices.get(code, current_price)
        if high <= 0:
            return False, 0.0
        dd = (current_price / high - 1)
        return dd <= self.cfg['trailing_stop_pct'], dd

    def check_portfolio_drawdown(self, current_total_value):
        """组合回撤: 从净值峰值回撤超过阈值。
        
        Returns:
            (triggered: bool, drawdown_pct: float)
        """
        if self.portfolio_peak <= 0:
            return False, 0.0
        dd = (current_total_value / self.portfolio_peak - 1)
        return dd <= self.cfg['portfolio_dd_pct'], dd

    def check_take_profit(self, code, current_price, market='HK'):
        """止盈检查。
        
        Returns:
            (triggered: bool, pnl_pct: float)
        """
        cost = self.entry_prices.get(code, 0)
        if cost <= 0:
            return False, 0.0
        pnl = (current_price / cost - 1)
        tp = self.cfg['take_profit_pct_hk'] if market == 'HK' else self.cfg['take_profit_pct_us']
        return pnl >= tp, pnl

    def check_all_stops(self, code, current_price, market='HK'):
        """一站式止损检查（按优先级: 固定 > 移动 > 止盈）。
        
        Returns:
            dict: {triggered, action, reason, pnl}
                  action: 'FIXED_STOP' / 'TRAILING_STOP' / 'TAKE_PROFIT' / None
        """
        # 1. 固定止损
        is_fixed, fixed_pnl = self.check_fixed_stop(code, current_price)
        if is_fixed:
            return {
                'triggered': True,
                'action': 'FIXED_STOP',
                'reason': f'固定止损 pnl={fixed_pnl:+.1%}',
                'pnl': fixed_pnl,
            }

        # 2. 移动止损
        is_trail, trail_dd = self.check_trailing_stop(code, current_price)
        if is_trail:
            high = self.highest_prices.get(code, current_price)
            return {
                'triggered': True,
                'action': 'TRAILING_STOP',
                'reason': f'移动止损 dd={trail_dd:+.1%} from high={high:.2f}',
                'pnl': trail_dd,
            }

        # 3. 止盈
        is_tp, tp_pnl = self.check_take_profit(code, current_price, market)
        if is_tp:
            return {
                'triggered': True,
                'action': 'TAKE_PROFIT',
                'reason': f'止盈 pnl={tp_pnl:+.1%}',
                'pnl': tp_pnl,
            }

        return {'triggered': False, 'action': None, 'reason': 'OK', 'pnl': 0.0}

    # === 冷却期管理 ==============================================
    def confirm_stop(self, code, filled_at=None):
        """成交确认后记录止损事件，启动冷却期。

        filled_at is persisted when available so crash-recovery retries are
        idempotent and keep the broker-confirmed timestamp.
        """
        if code in self.stop_timestamps:
            return
        self.stop_timestamps[code] = filled_at or datetime.now().isoformat()
        self.save_state()

    def is_in_cooldown(self, code):
        """检查标的是否在止损冷却期内。"""
        ts_str = self.stop_timestamps.get(code)
        if not ts_str:
            return False
        try:
            stop_time = datetime.fromisoformat(ts_str)
            days_since = (datetime.now() - stop_time).total_seconds() / 86400
            return days_since < self.cfg['stop_cooldown_days']
        except Exception:
            return False

    # === 仓位限制检查 ============================================
    def check_position_limit(self, code, trade_value, total_assets, is_buy=True):
        """检查单笔交易是否违反仓位限制。
        
        Args:
            code: 标的代码
            trade_value: 交易金额
            total_assets: 总资产
            is_buy: 是否为买入
        
        Returns:
            (ok: bool, reason: str)
        """
        if not is_buy:
            return True, 'SELL always allowed'
        if total_assets <= 0:
            return False, '总资产为 0'

        # 单只股票仓位（这里只检查本次新增，已持仓的需要外部传入）
        if trade_value > total_assets * self.cfg['max_position_pct']:
            return False, f'单只标的超限 {trade_value/total_assets:.1%} > {self.cfg["max_position_pct"]:.0%}'

        return True, 'OK'

    def check_total_exposure(self, current_exposure, new_trade_value, total_assets):
        """检查总仓位是否超限。
        
        Args:
            current_exposure: 当前持仓总市值
            new_trade_value: 新增交易金额
            total_assets: 总资产
        
        Returns:
            (ok: bool, reason: str)
        """
        new_total = current_exposure + new_trade_value
        if new_total > total_assets * self.cfg['max_total_pct']:
            return False, f'总仓位超限 {new_total/total_assets:.1%} > {self.cfg["max_total_pct"]:.0%}'
        return True, 'OK'

    # === 私有方法 ================================================
    def _get_effective_stop(self, code):
        """根据 ATR 自适应止损。低波动放宽，高波动收紧。"""
        atr_pct = self.latest_atr.get(code, 0.04)  # 默认 4%
        if atr_pct < self.cfg['atr_low_vol_thresh']:
            return self.cfg['atr_low_stop_pct']  # 低波动: -15%
        return self.cfg['fixed_stop_pct']         # 正常: -12%
