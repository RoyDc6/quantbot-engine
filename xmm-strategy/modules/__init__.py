# -*- coding: utf-8 -*-
"""
徐小明策略 modules
"""
from .engine import XMMStrategy
from .trend import calc_dual_trend, get_trend_signals
from .structure import calc_xmm_structure, get_structure_state, calc_macd
from .td_sequence import calc_td_seq, get_td_state, calc_td9

__all__ = [
    'XMMStrategy', 'analyze',
    'calc_dual_trend', 'get_trend_signals',
    'calc_short_trend', 'calc_long_trend',
    'calc_xmm_structure', 'get_structure_state', 'calc_macd',
    'calc_td_seq', 'get_td_state', 'calc_td9',
]
