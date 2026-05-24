# -*- coding: utf-8 -*-
"""
core/utils.py - QuantBot 通用工具函数
来源: 从 daily_runner.py / us_pipeline.py / signal_engine.py 提取并统一
"""
import numpy as np


# === RSI 计算 ===================================================
def calc_rsi(close, n=14):
    """标准 RSI 计算（Wilder 平滑法）。
    
    Args:
        close: 收盘价序列（array-like）
        n: RSI 周期，默认 14
    
    Returns:
        numpy array，长度与 close 相同，前 n 个值为 50.0
    """
    d = np.diff(close)
    g = np.where(d > 0, d, 0.0)
    lo = np.where(d < 0, -d, 0.0)
    r = np.full(len(close), 50.0)
    if len(close) <= n:
        return r
    ag, al = np.mean(g[:n]), np.mean(lo[:n])
    r[n] = 50.0 if al < 1e-10 else 100 - 100 / (1 + ag / (al + 1e-10))
    for i in range(n + 1, len(close)):
        ag = (ag * (n - 1) + g[i - 1]) / n
        al = (al * (n - 1) + lo[i - 1]) / n
        r[i] = 50.0 if al < 1e-10 else 100 - 100 / (1 + ag / (al + 1e-10))
    return r


def calc_weekly_rsi(close, p=4):
    """周线级别 RSI（从日线采样）。
    
    Args:
        close: 日线收盘价序列
        p: 周线 RSI 周期，默认 4
    
    Returns:
        float，最新的周线 RSI 值
    """
    n = len(close)
    if n < 30:
        return 50.0
    # 每 5 天取一个点，模拟周线
    w = [close[i] for i in range(4, n, 5)]
    if len(w) < p + 1:
        return 50.0
    return float(calc_rsi(np.array(w, dtype=float), p)[-1])


def calc_monthly_rsi(close, p=20):
    """月线级别 RSI（从日线采样）。
    
    Args:
        close: 日线收盘价序列
        p: 月线 RSI 周期，默认 20
    
    Returns:
        float，最新的月线 RSI 值
    """
    n = len(close)
    if n < 30:
        return 50.0
    # 每 21 天取一个点，模拟月线
    w = [close[i] for i in range(20, n, 21)]
    if len(w) < p + 1:
        return 50.0
    return float(calc_rsi(np.array(w, dtype=float), p)[-1])


# === MACD 计算 =================================================
def calc_macd(close, fast=12, slow=26, signal=9):
    """MACD 指标计算。
    
    Args:
        close: 收盘价序列
        fast: 快线周期，默认 12
        slow: 慢线周期，默认 26
        signal: 信号线周期，默认 9
    
    Returns:
        tuple: (dif, dea, histogram, macd_bull)
    """
    import pandas as pd
    ema_f = pd.Series(close).ewm(span=fast, adjust=False).mean().values
    ema_s = pd.Series(close).ewm(span=slow, adjust=False).mean().values
    dif = ema_f - ema_s
    dea = pd.Series(dif).ewm(span=signal, adjust=False).mean().values
    hist = 2 * (dif - dea)
    macd_bull = 1 if dif[-1] > dea[-1] else 0
    return dif[-1], dea[-1], hist[-1], macd_bull


# === ATR 计算 ===================================================
def calc_atr(high, low, close, n=14):
    """ATR（平均真实波幅）计算，返回 ATR 占价格的百分比。
    
    Args:
        high, low, close: 最高价、最低价、收盘价序列
        n: ATR 周期，默认 14
    
    Returns:
        float，ATR / 最新收盘价
    """
    import pandas as pd
    tr = np.maximum(high[1:] - low[1:],
                    np.maximum(np.abs(high[1:] - close[:-1]),
                               np.abs(low[1:] - close[:-1])))
    atr = pd.Series(tr).rolling(n).mean().values
    if len(atr) > 0 and not np.isnan(atr[-1]) and atr[-1] > 0:
        return atr[-1] / close[-1]
    return 0.0


# === 布林带 =====================================================
def calc_bollinger_ratio(close, n=20):
    """布林带位置比率。
    
    Returns:
        float，(close - ma) / (2 * std)，0.5 表示在中轨
    """
    if len(close) < n:
        return 0.5
    ma = np.mean(close[-n:])
    std = np.std(close[-n:])
    if std < 1e-10:
        return 0.5
    return (close[-1] - ma) / (2 * std)


# === JSON 安全转换 ==============================================
import numpy as _np


def json_safe(val):
    """单个值的 JSON 安全转换。"""
    if isinstance(val, (_np.integer,)):
        return int(val)
    if isinstance(val, (_np.floating,)):
        return float(val)
    if isinstance(val, (_np.ndarray,)):
        return val.tolist()
    if isinstance(val, (_np.bool_,)):
        return bool(val)
    return val


def dict_json_safe(d):
    """字典所有值的 JSON 安全转换。"""
    return {k: json_safe(v) for k, v in d.items()}


# === 符号格式转换 ================================================
def to_futu_code(symbol):
    """标准符号 → Futu 代码。00700.HK → HK.00700, SPY.US → US.SPY"""
    parts = symbol.split('.')
    if len(parts) == 2:
        return f'{parts[1]}.{parts[0]}'
    return symbol


def to_standard_symbol(futu_code):
    """Futu 代码 → 标准符号。HK.00700 → 00700.HK, US.SPY → SPY.US"""
    parts = futu_code.split('.')
    if len(parts) == 2:
        return f'{parts[1]}.{parts[0]}'
    return futu_code
