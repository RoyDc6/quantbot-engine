# -*- coding: utf-8 -*-
"""
XMM 策略信号 API — Agent 安全接口层
===================================
核心策略文件 (engine.py / structure.py / td_sequence.py) 均已设为只读。
Agent 只能通过此 API 获取信号，无法修改策略逻辑或新增因子。

用法:
    from signal_api import get_signal
    result = get_signal(df)
    # result = {'signal': 'BUY'|'SELL'|'HOLD', 'position_size': float, ...}
"""
import sys
import os
import logging
from datetime import datetime

# 确保能找到模块路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from modules.engine import XMMStrategy

# ── 日志 ──────────────────────────────────────────────────────
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
log_file = os.path.join(LOG_DIR, f'signal_api_{datetime.now().strftime("%Y%m%d")}.log')
logging.basicConfig(
    filename=log_file,
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)

# ── 单例策略引擎（阈值在 __init__ 中固定，Agent 不可改） ──────
_strategy = XMMStrategy(
    short_period=25,
    long_period=90,
    macd_fast=12,
    macd_slow=26,
    macd_signal=9,
    td_period=4,
    plateau_confirm_bars=3,
    struct_threshold=1.01,
)


def get_signal(df) -> dict:
    """
    获取 XMM 策略最新信号。

    Args:
        df: pd.DataFrame，必须含 open/high/low/close/volume 列

    Returns:
        dict: signal / position_size / reason / ... （与 XMMStrategy.analyze 一致）

    注意:
        - Agent 可自由调用此函数，但无法修改内部策略逻辑
        - 传入 NaN 数据会触发保护，返回 HOLD
        - 所有调用记录到审计日志
    """
    logging.info(f"get_signal() called | df_shape={df.shape} | "
                 f"columns={list(df.columns)}")

    # NaN 保护
    if df is None or df.empty:
        logging.warning("get_signal() | df is None or empty → HOLD")
        return _hold_result("数据为空")

    if df.isna().any().any():
        logging.warning("get_signal() | NaN detected → HOLD")
        return _hold_result("数据缺失(NaN)")

    required_cols = {'open', 'high', 'low', 'close', 'volume'}
    missing = required_cols - set(df.columns)
    if missing:
        logging.warning(f"get_signal() | missing columns: {missing} → HOLD")
        return _hold_result(f"缺少列: {missing}")

    try:
        result = _strategy.analyze(df)
        logging.info(f"get_signal() | signal={result.get('signal')} "
                     f"pos={result.get('position_size'):.2f} "
                     f"reason={result.get('reason')}")
        return result
    except Exception as e:
        logging.error(f"get_signal() | ERROR: {e}")
        return _hold_result(f"策略执行异常: {str(e)}")


def get_strategy_params() -> dict:
    """
    返回当前策略参数（只读，不可写）。

    Agent 可以读取参数值，但不能修改。
    """
    return {
        'short_period': _strategy.short_period,
        'long_period': _strategy.long_period,
        'macd_fast': _strategy.macd_fast,
        'macd_slow': _strategy.macd_slow,
        'macd_signal': _strategy.macd_signal,
        'td_period': _strategy.td_period,
        'plateau_confirm_bars': _strategy.plateau_confirm_bars,
        'struct_threshold': _strategy.struct_threshold,
    }


def _hold_result(reason: str) -> dict:
    return {
        'signal': 'HOLD',
        'position_size': 0.0,
        'signal_type': 'none',
        'reason': reason,
        'bull_score': 0,
        'bear_score': 0,
        'net_score': 0,
        'strength': 0,
    }