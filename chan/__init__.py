# -*- coding: utf-8 -*-
"""
缠论量化分析模块
Chan Theory Quantitative Analysis Module
"""
from .fractal import find_fractals, mark_fractals
from .merge import merge_klines, process_inclusion
from .stroke import find_strokes, Stroke
from .segment import find_segments, Segment
from .pivot import find_pivots, Pivot
from .signals import find_chan_signals, ChanSignal, generate_trading_signal
from .visualize import plot_chan_structure

__all__ = [
    'find_fractals', 'mark_fractals',
    'merge_klines', 'process_inclusion',
    'find_strokes', 'Stroke',
    'find_segments', 'Segment',
    'find_pivots', 'Pivot',
    'compute_chan_signals', 'ChanSignal',
    'plot_chan_structure',
]

__version__ = '0.1.0'
