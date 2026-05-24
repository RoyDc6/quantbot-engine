# -*- coding: utf-8 -*-
"""
LLM因子打分器
给定一只股票的最新数据，计算有效因子值（v2.3 — 全标的覆盖）

变更日志:
- v2.3 (2026-05-09): valid_for 从 ticker-specific 扩展为 ALL 通配
  原因: 82.7% 零值率，非 SPY/00700/03690/09618 的标的全部返回 0
  所有因子均为纯技术指标（SMA/ATR/RSI/成交量/收益率），对所有标的有效
  新增 RSI 极端值 + 放量反转两个因子
"""
import numpy as np
import pandas as pd

# ─── 因子定义 ───────────────────────────────────────────
# valid_for: ['ALL'] = 适用于所有标的 | ['SPY', '00700'] = 仅限特定标的
# ric_avg: 在验证集上的平均 Rank IC（用于因子加权，非适用性判断）

FACTORS = [
    {
        'name': 'mean_reversion_adj',
        'formula': '((sma_50 - close) / atr_14) * (1 + atr_pct)',
        'description': '价格低于SMA50乖离+高ATR → 超卖买入信号',
        'category': '均值回归',
        'valid_for': ['ALL'],
        'ric_avg': 0.065,
    },
    {
        'name': 'momentum_cross',
        'formula': 'np.where(((ret_1d < 0) & (ret_5d < 0) & (ret_20d < 0)), 1.0, 0.0)',
        'description': '多周期全跌=超卖买入',
        'category': '反向动量',
        'valid_for': ['ALL'],
        'ric_avg': 0.092,
    },
    {
        'name': 'vol_price_convergence',
        'formula': 'volume_ratio * (sma_20 - close)',
        'description': '放量跌破SMA20=量价背离确认超卖',
        'category': '量价背离',
        'valid_for': ['ALL'],
        'ric_avg': 0.069,
    },
    {
        'name': 'mean_reversion_atr',
        'formula': '(sma_20 - close) / atr_14',
        'description': '跌破SMA20+ATR高=超卖',
        'category': '均值回归',
        'valid_for': ['ALL'],
        'ric_avg': 0.054,
    },
    {
        'name': 'momentum_cross_short',
        'formula': 'np.where(((ret_1d < 0) & (ret_5d < 0)), 1.0, 0.0)',
        'description': '短线1日+5日全跌=超卖买入',
        'category': '反向动量',
        'valid_for': ['ALL'],
        'ric_avg': 0.071,
    },
    {
        'name': 'rsi_extreme',
        'formula': 'np.where(rsi_14 < 30, (30 - rsi_14) / 30, np.where(rsi_14 > 70, -(rsi_14 - 70) / 30, 0.0))',
        'description': 'RSI<30 超卖(正) / RSI>70 超买(负)',
        'category': 'RSI极端',
        'valid_for': ['ALL'],
        'ric_avg': 0.058,
    },
    {
        'name': 'volume_spike_reversal',
        'formula': 'np.where((volume_ratio > 2.0) & (ret_1d < -0.02), volume_ratio * abs(ret_1d), 0.0)',
        'description': '放量大跌=恐慌抛售→潜在反转',
        'category': '量价反转',
        'valid_for': ['ALL'],
        'ric_avg': 0.062,
    },
]

# ─── 指标计算 ──────────────────────────────────────────
def sma(arr: np.ndarray, n: int) -> np.ndarray:
    s = np.full(len(arr), np.nan)
    for i in range(n-1, len(arr)):
        s[i] = float(np.mean(arr[i-n+1:i+1]))
    return s

def ema(arr: np.ndarray, n: int) -> np.ndarray:
    a = 2/(n+1); s = np.full(len(arr), np.nan)
    if len(arr) < n: return s
    s[n-1] = float(np.mean(arr[:n]))
    for i in range(n, len(arr)):
        s[i] = a*float(arr[i]) + (1-a)*s[i-1]
    return s

def compute_rsi(arr: np.ndarray, n: int = 14) -> np.ndarray:
    d = np.diff(arr); d = np.insert(d, 0, 0)
    g = np.where(d > 0, d, 0.0)
    l = np.where(d < 0, -d, 0.0)
    r = np.full(len(arr), 50.0)
    ag, al = float(np.mean(g[:n])), float(np.mean(l[:n]))
    r[n] = 50.0 if al < 1e-10 else 100-100/(1+ag/(al+1e-10))
    for i in range(n+1, len(arr)):
        ag = (ag*(n-1)+g[i-1])/n; al = (al*(n-1)+l[i-1])/n
        r[i] = 50.0 if al < 1e-10 else 100-100/(1+ag/(al+1e-10))
    return r

def compute_features(df: pd.DataFrame) -> dict:
    """从DataFrame计算所有特征"""
    c = df['close'].values.astype(float)
    h = df['high'].values.astype(float)
    l_ = df['low'].values.astype(float)
    v = df['volume'].values.astype(float)
    n = len(c)

    tr = [max(h[0]-l_[0], abs(h[0]-c[0]), abs(l_[0]-c[0]))]
    for i in range(1, n):
        tr.append(max(h[i]-l_[i], abs(h[i]-c[i-1]), abs(l_[i]-c[i-1])))
    atr14 = np.full(n, np.nan)
    for i in range(14, n): atr14[i] = float(np.mean(tr[max(0,i-14):i+1]))

    ret1 = np.diff(c, prepend=c[0]) / (np.roll(c, 1)+1e-10); ret1[0] = 0
    ret5 = np.array([c[i]/c[max(0,i-5)]-1 for i in range(n)])
    ret20 = np.array([c[i]/c[max(0,i-20)]-1 for i in range(n)])
    vol_ma20 = sma(v, 20)

    return {
        'close': c, 'high': h, 'low': l_, 'volume': v,
        'atr_14': atr14, 'atr_pct': atr14 / (c + 1e-10),
        'sma_20': sma(c, 20), 'sma_50': sma(c, 50),
        'ret_1d': ret1, 'ret_5d': ret5, 'ret_20d': ret20,
        'volume_ratio': v / (vol_ma20 + 1e-10),
        'rsi_14': compute_rsi(c, 14),
    }

# ─── 因子求值 ─────────────────────────────────────────
def eval_factor(formula: str, feats: dict) -> float:
    """对最新一天求因子值"""
    def _sma(arr, n=20): return sma(np.array(arr, dtype=float), n)
    def _ema(arr, n=20): return ema(np.array(arr, dtype=float), n)
    try:
        ns = {'__builtins__': {}, 'np': np, 'sma_arr': _sma, 'ema_arr': _ema}
        ns.update({k: np.array(v, dtype=float) for k, v in feats.items()})
        vals = eval(formula, ns)
        vals = np.array(vals, dtype=float)
        if vals.ndim > 1: vals = vals.flatten()
        return float(vals[-1]) if not np.isnan(vals[-1]) else float(np.nanmedian(vals))
    except Exception:
        return 0.0

# ─── 因子打分 ─────────────────────────────────────────
def score_factors(df: pd.DataFrame, market_label: str = '') -> dict:
    """
    对一只股票计算全部有效因子值
    """
    feats = compute_features(df)
    results = {}

    for fac in FACTORS:
        valid = fac.get('valid_for', [])
        is_valid = 'ALL' in valid or (not market_label) or (market_label in valid)
        if not is_valid:
            results[fac['name']] = {'score': 0, 'raw': None, 'note': f'not valid for {market_label}'}
            continue

        # 对最近60天求值用于归一化
        raw_vals = []
        for i in range(60, len(feats['close'])):
            sub = {k: v[:i+1] if isinstance(v, np.ndarray) else v for k, v in feats.items()}
            v = eval_factor(fac['formula'], sub)
            raw_vals.append(v)
        raw_vals = np.array(raw_vals, dtype=float)
        raw_vals = raw_vals[~np.isnan(raw_vals)]

        if len(raw_vals) < 20:
            results[fac['name']] = {'score': 0, 'raw': None, 'note': 'insufficient data'}
            continue

        current = eval_factor(fac['formula'], feats)
        pct = float(np.sum(raw_vals <= current) / len(raw_vals)) * 100
        pct = max(1, min(99, pct))

        results[fac['name']] = {
            'raw': round(current, 6),
            'percentile': round(pct, 1),
            'note': fac['description'],
        }

    return results

def factor_to_signal_score(factor_scores: dict) -> float:
    """
    把因子打分转换为信号分 [-50, +50]
    pct 低（乖离大）→ 分高；pct 高（接近均线）→ 分低
    pct=50 → 0（中性）；pct=0 → +50（极度超卖）；pct=100 → -50（极度超买）
    """
    active = {k: v for k, v in factor_scores.items()
              if v.get('raw') is not None and v.get('percentile', 0) > 0}

    if not active:
        return 0.0

    ric_weights = {f['name']: max(abs(f['ric_avg']), 0.03) for f in FACTORS}

    total = 0.0
    total_w = 0.0
    for name, info in active.items():
        pct = info['percentile']     # 0-100
        ric = ric_weights.get(name, 0.05)
        # pct=0  → score=+50（极度超卖）
        # pct=50 → score=  0（中性）
        # pct=100→ score=-50（极度超买）
        score = 50 - pct
        # 信号越极端（pct越接近0或100）权重越高
        extremeness = abs(50 - pct) / 50
        w = ric * (0.5 + extremeness * 0.5)
        total += score * w
        total_w += w

    return round(total / total_w, 1) if total_w > 0 else 0.0

def get_factor_summary(factor_scores: dict) -> str:
    """生成因子打分摘要文本"""
    lines = []
    for name, info in factor_scores.items():
        if info.get('raw') is None:
            continue
        pct = info['percentile']
        flag = 'LOW' if pct < 30 else ('HIGH' if pct > 70 else 'MID')
        lines.append(f"{name}[{flag}={pct:.0f}%]")
    return '; '.join(lines) if lines else '无有效因子'
