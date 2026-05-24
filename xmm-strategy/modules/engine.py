# -*- coding: utf-8 -*-
"""
徐小明策略信号融合引擎 v4.0
【三层独立信号 + TD放大器】

  信号层（各自独立，都可以触发交易）：
    趋势层  — EMA均线对上穿/下破
    结构层  — MACD底部/顶部结构形成
    钝化层  — MACD底/顶背离钝化预警

  确认层：
    TD序列  — 计数在 6~9 区间时，所有信号 ×1.5 倍放大

  融合逻辑：
    多头信号分（做多类相加）vs 空头信号分（做空类相加）
    NET = 多头分 - 空头分
    |NET| ≥ 2  且 TD6-9 → 触发交易
    方向由 NET 的符号决定
"""
import pandas as pd
import numpy as np
from typing import Dict, Tuple, Optional

from .trend import calc_dual_trend
from .structure import calc_xmm_structure, get_structure_state
from .td_sequence import calc_td_seq, get_td_state


class XMMStrategy:
    """徐小明量化策略引擎 v5.0 — 趋势为王，结构修边，序列为辅

    决策矩阵：
      趋势方向（长顶/长底）→ 唯一权重
      MACD 结构 → 修边/定时点
      TD9 序列 → 辅证/置信度加分

    三层独立计算，纯规则决策（不混合打分）
    """

    def __init__(self,
                 short_period: int = 25,
                 long_period: int = 90,
                 macd_fast: int = 12,
                 macd_slow: int = 26,
                 macd_signal: int = 9,
                 td_period: int = 4,
                 plateau_confirm_bars: int = 3,
                 struct_threshold: float = 1.01):
        self.short_period = short_period
        self.long_period = long_period
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal = macd_signal
        self.td_period = td_period
        self.plateau_confirm_bars = plateau_confirm_bars  # 连续钝化N根才触发override
        self.struct_threshold = struct_threshold          # MACD结构确认倍数阈值

    def analyze(self, df: pd.DataFrame, position_track: Optional[Dict] = None) -> Dict:
        """
        完整分析：趋势层 + 结构层 + 钝化层 + TD序列 → 融合
        """
        if df is None or len(df) < 60:
            return self._default_result('数据不足')

        df = df.copy()

        # 1. 趋势层
        trend_df = calc_dual_trend(df, self.short_period, self.long_period)

        # NaN检查：趋势线必须有效（前期数据不足时跳过）
        last_t = trend_df.iloc[-1]
        if (pd.isna(last_t['短顶']) or pd.isna(last_t['短底']) or
            pd.isna(last_t['长顶']) or pd.isna(last_t['长底'])):
            return self._default_result('趋势线数据不足(NaN)')

        t = self._extract_trend_layer(trend_df)

        # 2. MACD结构层 + 钝化层
        macd_df = calc_xmm_structure(df, self.macd_fast, self.macd_slow, self.macd_signal, threshold=self.struct_threshold)
        s = get_structure_state(macd_df)

        # 3. TD序列层
        td_df = calc_td_seq(df['close'], period=self.td_period)
        td = get_td_state(td_df)

        # 4. 三层信号评分 + 融合
        result = self._fuse(t, s, td, position_track)

        # 5. 补充额外信息
        result['close'] = float(df['close'].iloc[-1])
        result['short_top'] = float(trend_df['短顶'].iloc[-1])
        result['short_bot'] = float(trend_df['短底'].iloc[-1])
        result['long_top'] = float(trend_df['长顶'].iloc[-1])
        result['long_bot'] = float(trend_df['长底'].iloc[-1])
        result['trend_layer'] = t
        result['structure_layer'] = s
        result['td_layer'] = td
        result['date'] = str(df.index[-1])

        return result

    # ============================================================
    # 趋势层提取
    # ============================================================
    def _extract_trend_layer(self, trend_df: pd.DataFrame) -> Dict:
        """从趋势DataFrame提取各信号值"""
        last = trend_df.iloc[-1]
        c = float(last['close'])
        st = float(last['短顶'])
        sb = float(last['短底'])
        lt = float(last['长顶'])
        lb = float(last['长底'])

        # 今日交叉信号
        cross_short_up   = bool(last['cross_short_up'])
        cross_short_down = bool(last['cross_short_down'])
        cross_long_up    = bool(last['cross_long_up'])
        cross_long_down  = bool(last['cross_long_down'])

        # 市场状态（用于叠加方向判断）
        if c > st and c > lt:
            market = 'UP'
        elif c < sb and c < lb:
            market = 'DOWN'
        else:
            market = 'SIDEWAYS'

        return {
            'market': market,          # UP / DOWN / SIDEWAYS
            'close': c,
            # 趋势信号
            'cross_short_up': cross_short_up,
            'cross_short_down': cross_short_down,
            'cross_long_up': cross_long_up,
            'cross_long_down': cross_long_down,
            # 多头信号分
            'bull_score': int(cross_short_up) + int(cross_long_up),
            # 空头信号分
            'bear_score': int(cross_short_down) + int(cross_long_down),
            # 描述
            'signal_type': self._trend_signal_type(cross_short_up, cross_short_down,
                                                    cross_long_up, cross_long_down),
        }

    def _trend_signal_type(self, csu, csd, clu, cld) -> str:
        if csu and clu: return '双突破(强做多)'
        if csu and not clu: return '短顶突破(弱做多)'
        if clu and not csu: return '长顶突破(弱做多)'
        if csd and cld: return '双跌破(强做空)'
        if csd and not cld: return '短底跌破(弱做空)'
        if cld and not csd: return '长底跌破(弱做空)'
        return 'none'

    # ============================================================
    # 融合核心 — 纯规则决策矩阵 v5.0
    # ============================================================
    def _fuse(self,
              t: Dict,      # 趋势层
              s: Dict,      # 结构层
              td: Dict,     # TD层
              position_track: Optional[Dict]) -> Dict:

        trend = t['market']  # 'UP', 'DOWN', 'SIDEWAYS'
        td_count = td['td_count']
        td_reached = td['td_reached']
        is_buy_seq = td.get('is_buy_seq', False)   # TD 买入计数（低9）
        is_sell_seq = td.get('is_sell_seq', False)  # TD 卖出计数（高9）

        has_bottom_structure = s.get('底部结构', False)
        has_top_structure = s.get('顶部结构', False)
        has_bottom_plateau = s.get('底部钝化', False)
        has_top_plateau = s.get('顶部钝化', False)

        # 用于 override 的连续钝化计数
        top_plateau_days = s.get('连续顶部钝化天数', 0)
        bottom_plateau_days = s.get('连续底部钝化天数', 0)

        # ================================================================
        # 1️⃣ 趋势向上（价 > 长顶）
        # ================================================================
        if trend == 'UP':
            # 底结构 + TD低9 → 加仓 80-100%
            if has_bottom_structure and td_reached and is_buy_seq:
                return self._make_xmm_result(
                    'BUY', 0.90,
                    '趋势向上·底结构+低9',
                    '顺势加仓，低位确认增加信心',
                    td_count, td_reached)

            # 顶结构 + 无TD → 常规止盈 20-30%
            if has_top_structure and not td_reached:
                return self._make_xmm_result(
                    'SELL', 0.25,
                    '趋势向上·顶结构止盈',
                    '常规止盈，减仓25%，保留趋势核心仓位',
                    td_count, td_reached)

            # 顶部钝化 + TD高9 + 连续N根钝化 → override减仓40%
            if has_top_plateau and td_reached and is_sell_seq \
                    and top_plateau_days >= self.plateau_confirm_bars:
                return self._make_xmm_result(
                    'SELL', 0.40,
                    '趋势向上·顶钝化+高9(override)',
                    f'条件性减仓40%，连续钝化{top_plateau_days}天触发',
                    td_count, td_reached)

            # 无结构 → 持有
            return self._make_xmm_result(
                'HOLD', 0.0,
                '趋势向上·持有',
                '缺乏结构信号，保持仓位',
                td_count, td_reached)

        # ================================================================
        # 2️⃣ 趋势向下（价 < 长底）
        # ================================================================
        if trend == 'DOWN':
            # 顶结构 + TD高9 → 做空 80-100%
            if has_top_structure and td_reached and is_sell_seq:
                return self._make_xmm_result(
                    'SELL', 0.90,
                    '趋势向下·顶结构+高9',
                    '顺势做空，主力仓位90%',
                    td_count, td_reached)

            # 底结构 + 无TD → 不动（不抄底）
            if has_bottom_structure and not td_reached:
                return self._make_xmm_result(
                    'HOLD', 0.0,
                    '趋势向下·底结构不抄底',
                    '底结构不做多，等待趋势反转确认',
                    td_count, td_reached)

            # 底部钝化 + TD低9 → 轻仓观测 10-20%
            if has_bottom_plateau and td_reached and is_buy_seq:
                return self._make_xmm_result(
                    'BUY', 0.15,
                    '趋势向下·底钝化+低9观测',
                    '轻仓观测15%，设置小止损',
                    td_count, td_reached)

            # 无结构 → 空仓观望
            return self._make_xmm_result(
                'HOLD', 0.0,
                '趋势向下·空仓观望',
                '缺乏信号，保持空仓',
                td_count, td_reached)

        # ================================================================
        # 3️⃣ 趋势震荡（长底 < 价 < 长顶）
        # ================================================================
        # 有结构 + TD共振 → 轻仓试探 20-40%
        has_any_structure = has_bottom_structure or has_top_structure
        has_td_resonance = (has_bottom_plateau and td_reached and is_buy_seq) or \
                           (has_top_plateau and td_reached and is_sell_seq)

        if has_any_structure and has_td_resonance:
            direction = 'BUY' if has_bottom_structure else 'SELL'
            pos = 0.30
            dir_desc = '做多' if direction == 'BUY' else '做空'
            return self._make_xmm_result(
                direction, pos,
                f'震荡·结构+TD共振{dir_desc}',
                f'轻仓试探{int(pos*100)}%，测试市场反应',
                td_count, td_reached)

        # 有结构 + 无TD → 观望等待确认
        if has_any_structure:
            return self._make_xmm_result(
                'HOLD', 0.0,
                '震荡·结构无共振观望',
                '有结构但无TD共振，等待突破信号',
                td_count, td_reached)

        # 无结构 → 空仓观望
        return self._make_xmm_result(
            'HOLD', 0.0,
            '震荡·空仓观望',
            '无明确信号，保持观望',
            td_count, td_reached)

    def _make_xmm_result(self, signal: str, position_pct: float,
                         sig_type: str, reason: str,
                         td_count: int, td_reached: bool) -> Dict:
        """徐小明策略的纯净输出格式 — 不混合其他信号系统"""
        return {
            'signal': signal,
            'position_size': position_pct,
            'signal_type': sig_type,
            'reason': reason,
            # 各层原始状态（供上层使用，不参与融合）
            'market_trend': 'INFERRED',
            'trend_type': 'INFERRED',
            # 向下兼容字段（保留但标记为deprecated）
            'bull_score': 1.0 if signal == 'BUY' else 0.0,
            'bear_score': 1.0 if signal == 'SELL' else 0.0,
            'net_score': 1.0 if signal == 'BUY' else (-1.0 if signal == 'SELL' else 0.0),
            'strength': 3.0 if signal in ('BUY', 'SELL') else 0.0,
            'structure': {},
            'td': {'td_count': td_count, 'td_near': abs(td_count) >= 7,
                   'td_reached': td_reached},
            'td_count': td_count,
            'td_amplified': abs(td_count) >= 7,
        }

    def _default_result(self, reason: str) -> Dict:
        return {
            'signal': 'HOLD', 'bull_score': 0, 'bear_score': 0, 'net_score': 0,
            'strength': 0, 'position_size': 0.0, 'signal_type': 'none',
            'reason': reason, 'market_trend': 'UNKNOWN',
            'trend_type': 'none', 'structure': {}, 'td': {}, 'td_count': 0,
            'td_amplified': False,
            'close': 0, 'short_top': 0, 'short_bot': 0, 'long_top': 0, 'long_bot': 0,
            'trend_layer': {}, 'structure_layer': {}, 'td_layer': {},
            'date': 'N/A',
        }
