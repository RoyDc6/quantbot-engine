# -*- coding: utf-8 -*-
"""
双系统融合框架 v2.0
融合融合模型 + XMM（徐小明三周期策略）+ LLM因子打分

市场分工：
  港股 → 融合模型主导 (Alpha挖掘)
  美股ETF → XMM主导 (风控优先)
  A股 → 融合模型主导
"""

from .signal_types import SignalLevel, FusionSignal, FusionModelSignal, XMMSignal, DecisionSignal
from .volume_profile import VolumeProfileBoxStrategy, quick_vp_signal, VPBoxRaw

__all__ = [
    'SignalLevel', 'FusionSignal', 'FusionModelSignal', 'XMMSignal',
    'DecisionSignal',
    'VolumeProfileBoxStrategy', 'quick_vp_signal', 'VPBoxRaw',
]

# FusionEngine 和 FusionBacktest 延迟导入（避免循环依赖）
# 使用时直接: from fusion_framework import FusionEngine
def __getattr__(name):
    if name == 'FusionEngine':
        from .fusion_engine import FusionEngine
        return FusionEngine
    if name == 'FusionBacktest':
        from .backtest import FusionBacktest
        return FusionBacktest
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
