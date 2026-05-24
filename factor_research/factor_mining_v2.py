"""
因子挖掘 v2.0 - RSI变种 + 缠论增强 + 趋势质量
在现有因子基础上深挖，提高RankIC
"""
import sys, io, warnings, os
from datetime import datetime
warnings.filterwarnings('ignore')

def _setup_stdout():
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    except:
        pass

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

# ═══════════════════════════════════════════════════════════
# 基础技术指标
# ═══════════════════════════════════════════════════════════

def _ma(p, n):
    r = np.full(len(p), np.nan)
    if len(p) >= n:
        r[n-1:] = np.convolve(p, np.ones(n)/n, mode='valid')
    return r

def _ema(p, n):
    r = np.full(len(p), np.nan)
    if len(p) < n: return r
    r[n-1] = np.mean(p[:n])
    k = 2/(n+1)
    for i in range(n, len(p)):
        r[i] = p[i]*k + r[i-1]*(1-k)
    return r

def _atr(h, l, c, n=14):
    tr = np.maximum(h[1:]-l[1:], np.maximum(np.abs(h[1:]-c[:-1]), np.abs(l[1:]-c[:-1])))
    tr = np.concatenate([[0], tr])
    r = np.full(len(c), np.nan)
    if len(c) >= n:
        r[n-1] = np.mean(tr[:n])
        for i in range(n, len(c)):
            r[i] = (r[i-1]*(n-1)+tr[i])/n
    return r

def _rsi(p, n=14):
    d = np.diff(p)
    g = np.where(d>0, d, 0.0)
    l_ = np.where(d<0, -d, 0.0)
    r = np.full(len(p), 50.0)
    if len(p) <= n: return r
    ag, al = np.mean(g[:n]), np.mean(l_[:n])
    r[n-1] = 50.0 if al<1e-10 else 100-100/(1+ag/(al+1e-10))
    for i in range(n, len(p)-1):
        ag = (ag*(n-1)+g[i])/n
        al = (al*(n-1)+l_[i])/n
        r[i] = 50.0 if al<1e-10 else 100-100/(1+ag/(al+1e-10))
    return r

def _williams_r(h, l, c, n=14):
    rh = np.full(len(c), np.nan)
    rl = np.full(len(c), np.nan)
    for i in range(n-1, len(c)):
        rh[i] = np.max(h[i-n+1:i+1])
        rl[i] = np.min(l[i-n+1:i+1])
    with np.errstate(divide='ignore', invalid='ignore'):
        wr = (c - rh) / (rh - rl + 1e-10) * -100
    return np.nan_to_num(wr, 50)


# ═══════════════════════════════════════════════════════════
# 核心新因子：RSI变种族
# ═══════════════════════════════════════════════════════════

def extract_rsi_variants(o, h, l, c):
    """
    RSI变种因子族 - 挖掘RSI的先行信号和变体
    """
    f = {}
    n = len(o)
    
    # ── 标准RSI ──────────────────────────────
    for period in [6, 9, 14, 21]:
        f[f'rsi_{period}'] = _rsi(o, period)
    
    # ── RSI斜率（变化速度）──先行信号 ──────────
    for period in [14, 28]:
        rsi_vals = _rsi(o, period)  # always compute fresh
        f[f'rsi_{period}_slope'] = np.full(n, 0.0)
        for i in range(period+5, n):
            recent = rsi_vals[i-5:i+1]
            valid = recent[~np.isnan(recent)]
            if len(valid) >= 3:
                f[f'rsi_{period}_slope'][i] = (valid[-1] - valid[0]) / len(valid)
    
    # ── RSI偏离（RSI vs MA）──提前预警 ─────────
    for period in [14, 28]:
        rsi_vals = f.get(f'rsi_{period}', _rsi(o, period))
        rsi_ma = _ma(rsi_vals, 10)
        with np.errstate(divide='ignore', invalid='ignore'):
            f[f'rsi_{period}_dev'] = np.nan_to_num(rsi_vals - rsi_ma, 0)
    
    # ── RSI创新高/新低 ─────────────────────────
    for period in [14, 28]:
        rsi_vals = f.get(f'rsi_{period}', _rsi(o, period))
        f[f'rsi_{period}_hh'] = np.full(n, 0.0)  # 创N日新高
        f[f'rsi_{period}_ll'] = np.full(n, 0.0)  # 创N日新低
        for i in range(period-1, n):
            window = rsi_vals[max(0,i-period+1):i+1]
            if len(window) >= period//2:
                mn, mx = np.nanmin(window), np.nanmax(window)
                if not np.isnan(rsi_vals[i]):
                    f[f'rsi_{period}_hh'][i] = 1 if rsi_vals[i] >= mx else 0
                    f[f'rsi_{period}_ll'][i] = 1 if rsi_vals[i] <= mn else 0
    
    # ── RSI极值次数（超买超卖持续性）───────────
    for period, lookback in [(14, 20), (28, 40)]:
        rsi_vals = f.get(f'rsi_{period}', _rsi(o, period))
        f[f'rsi_{period}_oversold_count'] = np.full(n, 0.0)
        f[f'rsi_{period}_overbought_count'] = np.full(n, 0.0)
        for i in range(lookback, n):
            window = rsi_vals[i-lookback:i+1]
            valid = window[~np.isnan(window)]
            if len(valid) > 5:
                f[f'rsi_{period}_oversold_count'][i] = np.sum(valid < 30) / len(valid)
                f[f'rsi_{period}_overbought_count'][i] = np.sum(valid > 70) / len(valid)
    
    # ── RSI与价格背离 ──────────────────────────
    for period in [14, 28]:
        rsi_vals = f.get(f'rsi_{period}', _rsi(o, period))
        price_slope = np.full(n, 0.0)
        rsi_slope = np.full(n, 0.0)
        for i in range(period, n):
            p1, p2 = o[i-period], o[i]
            rsi_slope[i] = rsi_vals[i] - rsi_vals[i-period] if not np.isnan(rsi_vals[i]) and not np.isnan(rsi_vals[i-period]) else 0
            price_slope[i] = (p2 - p1) / (p1 + 1e-10) if abs(p1) > 1e-10 else 0
        
        # 背离：价格创新低但RSI未创新低 = 底背离（ bullish）
        f[f'rsi_price_div_{period}'] = rsi_slope - price_slope * 100  # 缩放对齐
    
    # ── 双RSI（金叉死叉）──────────────────────
    rsi6 = _rsi(o, 6)
    rsi14 = _rsi(o, 14)
    rsi28 = _rsi(o, 28)
    
    f['rsi_6_14_cross'] = np.where(
        (rsi6 > rsi14) & (np.roll(rsi6, 1) <= np.roll(rsi14, 1)),
        1, np.where(
            (rsi6 < rsi14) & (np.roll(rsi6, 1) >= np.roll(rsi14, 1)),
            -1, 0
        )
    ).astype(float)
    
    f['rsi_14_28_cross'] = np.where(
        (rsi14 > rsi28) & (np.roll(rsi14, 1) <= np.roll(rsi28, 1)),
        1, np.where(
            (rsi14 < rsi28) & (np.roll(rsi14, 1) >= np.roll(rsi28, 1)),
            -1, 0
        )
    ).astype(float)
    
    # ── RSI区域（0-20/20-40/40-60/60-80/80-100）───
    rsi14_vals = _rsi(o, 14)
    for zone in [(0, 20, 'zone_0_20'), (20, 40, 'zone_20_40'), (40, 60, 'zone_40_60'), 
                  (60, 80, 'zone_60_80'), (80, 100, 'zone_80_100')]:
        lo, hi, name = zone
        f[f'rsi14_{name}'] = np.where((rsi14_vals >= lo) & (rsi14_vals <= hi), 1.0, 0.0)
    
    # ── Williams%R 与 RSI 的组合 ─────────────────
    wr14 = _williams_r(h, l, c, 14)
    rsi14_v = _rsi(o, 14)
    f['wr_rsi_combined'] = (wr14 + rsi14_v) / 2  # 两者结合
    
    # ── 快速RSI（更敏感）────────────────────────
    f['rsi_fast'] = _rsi(o, 5)
    f['rsi_fast_slope'] = np.full(n, 0.0)
    for i in range(10, n):
        r = f['rsi_fast']
        if not np.isnan(r[i]) and not np.isnan(r[i-5]):
            f['rsi_fast_slope'][i] = r[i] - r[i-5]
    
    return f


# ═══════════════════════════════════════════════════════════
# 核心新因子：缠论增强因子族
# ═══════════════════════════════════════════════════════════

def _identify_fractals(h, l, n=5):
    """
    识别顶底分型
    返回: top[i]=1表示顶分型，bottom[i]=1表示底分型
    """
    tops = np.zeros(len(h))
    bottoms = np.zeros(len(h))
    
    if len(h) < n+1: return tops, bottoms
    
    for i in range(n, len(h)-n):
        # 顶分型：中间最高
        if h[i] > h[i-1] and h[i] > h[i-2] and h[i] > h[i+1] and h[i] > h[i+2]:
            if n > 2:
                if all(h[i] >= h[i-j] for j in range(3, n+1)) and all(h[i] >= h[i+j] for j in range(3, n+1)):
                    tops[i] = 1
            else:
                tops[i] = 1
        
        # 底分型：中间最低
        if l[i] < l[i-1] and l[i] < l[i-2] and l[i] < l[i+1] and l[i] < l[i+2]:
            if n > 2:
                if all(l[i] <= l[i-j] for j in range(3, n+1)) and all(l[i] <= l[i+j] for j in range(3, n+1)):
                    bottoms[i] = 1
            else:
                bottoms[i] = 1
    
    return tops, bottoms


def extract_chan_enhanced(h, l, c, o):
    """
    缠论增强因子 - 三买三卖 + 笔力度 + 中枢
    """
    f = {}
    n = len(h)
    
    # ── 分型（3/5/10日）──────────────────────────
    for period in [3, 5, 10]:
        tops, bottoms = _identify_fractals(h, l, period)
        f[f'chan_top_{period}d'] = tops
        f[f'chan_bottom_{period}d'] = bottoms
        
        # 分型计数
        top_count = np.full(n, 0.0)
        bottom_count = np.full(n, 0.0)
        for i in range(period*2, n):
            top_count[i] = np.sum(tops[max(0,i-period*3):i+1])
            bottom_count[i] = np.sum(bottoms[max(0,i-period*3):i+1])
        
        f[f'chan_top_count_{period}d'] = top_count
        f[f'chan_bottom_count_{period}d'] = bottom_count
    
    # ── 分型平衡度 ───────────────────────────────
    for period in [5, 10, 20]:
        tops, bottoms = _identify_fractals(h, l, 5)
        tc = np.full(n, 0.0)
        bc = np.full(n, 0.0)
        for i in range(20, n):
            ws = max(0, i-period)
            ts = np.sum(tops[ws:i+1])
            bs = np.sum(bottoms[ws:i+1])
            denom = ts + bs
            tc[i] = ts/denom if denom > 0 else 0.5
            bc[i] = bs/denom if denom > 0 else 0.5
        f[f'chan_frac_balance_{period}d'] = tc - bc  # 正=顶多，负=底多
    
    # ── 最近分型距今天数（分型新鲜度）─────────────
    tops5, bottoms5 = _identify_fractals(h, l, 5)
    f['chan_days_since_top'] = np.full(n, float('inf'))
    f['chan_days_since_bottom'] = np.full(n, float('inf'))
    
    last_top = -100
    last_bottom = -100
    for i in range(n):
        if tops5[i] == 1: last_top = i
        if bottoms5[i] == 1: last_bottom = i
        f['chan_days_since_top'][i] = i - last_top if last_top >= 0 else 100
        f['chan_days_since_bottom'][i] = i - last_bottom if last_bottom >= 0 else 100
    
    # ── 笔的初步识别（简化版）────────────────────
    # 笔：连续同向分型之间的K线数
    # 简化：计算从最近底分型到当前的价格变化
    for period in [5, 10, 20]:
        bottoms_p = f.get(f'chan_bottom_{period}d', np.zeros(n))
        f[f'chan_pullback_{period}d'] = np.full(n, 0.0)
        
        for i in range(period, n):
            # 找最近的底分型
            recent_bottoms = np.where(bottoms_p[max(0,i-period):i+1] == 1)[0]
            if len(recent_bottoms) > 0:
                last_b_idx = recent_bottoms[-1]
                last_b_price = l[max(0, last_b_idx)]
                if abs(last_b_price) > 1e-10:
                    f[f'chan_pullback_{period}d'][i] = (o[i] - last_b_price) / last_b_price
    
    # ── 中枢初步识别（简化版）────────────────────
    # 使用最近20日的高低区间
    for period in [10, 20]:
        f[f'chan_range_pos_{period}d'] = np.full(n, 0.5)
        f[f'chan_range_width_{period}d'] = np.full(n, 0.0)
        
        for i in range(period, n):
            ws = max(0, i-period)
            range_h = np.max(h[ws:i+1])
            range_l = np.min(l[ws:i+1])
            range_w = (range_h - range_l) / (range_l + 1e-10)
            if range_w > 1e-10:
                pos = (o[i] - range_l) / (range_h - range_l)
                f[f'chan_range_pos_{period}d'][i] = pos
                f[f'chan_range_width_{period}d'][i] = range_w
            else:
                f[f'chan_range_pos_{period}d'][i] = 0.5
                f[f'chan_range_width_{period}d'][i] = 0.0
    
    return f


# ═══════════════════════════════════════════════════════════
# 核心新因子：趋势质量因子族
# ═══════════════════════════════════════════════════════════

def extract_trend_quality(o, h, l, c):
    """
    趋势质量因子 - 识别真假突破
    """
    f = {}
    n = len(o)
    
    # ── ADX（趋势强度）── 已有但重新优化 ─────────
    # ADX持续性：ADX是否连续上升
    atr14 = _atr(h, l, c, 14)
    
    # ── 趋势持续性指标 ───────────────────────────
    # 计算趋势方向的一致性（类似Hurst但更快）
    for period in [5, 10, 20]:
        f[f'trend_consistency_{period}d'] = np.full(n, 0.0)
        for i in range(period, n):
            ws = max(0, i-period)
            rets = np.diff(o[ws:i+1]) / o[ws:i]
            valid = rets[~np.isnan(rets)]
            if len(valid) > 3:
                # 一致性 = 符号相同的比例
                pos = np.sum(valid > 0) / len(valid)
                f[f'trend_consistency_{period}d'][i] = pos * 2 - 1  # 归一化到 -1~1
    
    # ── 趋势加速度（动量变化率）──────────────────
    for period in [5, 10]:
        f[f'trend_accel_{period}d'] = np.full(n, 0.0)
        for i in range(period*2, n):
            m1 = (o[i-period] - o[i-period*2]) / (o[i-period*2] + 1e-10) if abs(o[i-period*2]) > 1e-10 else 0
            m2 = (o[i] - o[i-period]) / (o[i-period] + 1e-10) if abs(o[i-period]) > 1e-10 else 0
            f[f'trend_accel_{period}d'][i] = m2 - m1
    
    # ── 均线排列 ─────────────────────────────────
    for ma_combo in [(5,20,'5_20'), (10,20,'10_20'), (20,60,'20_60')]:
        ma1_p, ma2_p, name = ma_combo
        ma1 = _ma(o, ma1_p)
        ma2 = _ma(o, ma2_p)
        f[f'ma排列_{name}'] = np.where(
            (ma1 > ma2) & (np.roll(ma1, 1) <= np.roll(ma2, 1)),
            1.0, np.where(
                (ma1 < ma2) & (np.roll(ma1, 1) >= np.roll(ma2, 1)),
                -1.0, 0.0
            )
        ).astype(float)
    
    # ── 价格vs均线的位置（均线乖离率增强）────────
    for period in [5, 10, 20]:
        ma = _ma(o, period)
        f[f'ma_deviation_{period}d'] = (o - ma) / (ma + 1e-10)
        
        # 乖离率历史分位
        f[f'ma_dev_pct_{period}d'] = np.full(n, 0.5)
        for i in range(period*3, n):
            ws = max(0, i-period*3)
            window = f[f'ma_deviation_{period}d'][ws:i+1]
            valid = window[~np.isnan(window)]
            if len(valid) > 5:
                mn, mx = np.min(valid), np.max(valid)
                cur = f[f'ma_deviation_{period}d'][i]
                denom = mx - mn
                f[f'ma_dev_pct_{period}d'][i] = (cur - mn) / (denom + 1e-10) if abs(denom) > 1e-10 else 0.5
    
    return f


# ═══════════════════════════════════════════════════════════
# 核心新因子：波动率异常因子族
# ═══════════════════════════════════════════════════════════

def extract_volatility_anomaly(o, h, l, c, v):
    """
    波动率异常因子 - 突破和盘整的识别
    """
    f = {}
    n = len(o)
    atr14 = _atr(h, l, c, 14)
    
    # ── ATR相对历史 ───────────────────────────────
    f['atr_pct_rank'] = np.full(n, 0.5)
    for i in range(60, n):
        ws = max(0, i-60)
        window = atr14[ws:i+1]
        valid = window[~np.isnan(window)]
        if len(valid) > 10:
            mn, mx = np.min(valid), np.max(valid)
            if mx > mn:
                f['atr_pct_rank'][i] = (atr14[i] - mn) / (mx - mn)
    
    # ── 布林带变窄（盘整突破预警）───────────────
    for period in [10, 20]:
        ma = _ma(o, period)
        std = np.full(n, 0.0)
        for i in range(period-1, n):
            std[i] = np.std(o[i-period+1:i+1])
        
        # 布林带宽度（标准化）
        bb_width = np.full(n, 0.0)
        for i in range(period-1, n):
            w = std[i] * 2
            denom = ma[i] + 1e-10
            bb_width[i] = w / denom if abs(denom) > 1e-10 else 0
        
        # 布林带宽度相对历史
        f[f'bb_width_{period}d'] = bb_width
        f[f'bb_width_rank_{period}d'] = np.full(n, 0.5)
        for i in range(period*5, n):
            ws = max(0, i-period*3)
            window = bb_width[ws:i+1]
            mn, mx = np.min(window), np.max(window)
            if mx > mn:
                f[f'bb_width_rank_{period}d'][i] = (bb_width[i] - mn) / (mx - mn)
    
    # ── 波动率爆发 ───────────────────────────────
    vol20 = np.full(n, np.nan)
    for i in range(19, n):
        rets = np.diff(o[max(0,i-19):i+1]) / o[max(0,i-19):i]
        vol20[i] = np.nanstd(rets) * np.sqrt(252) if len(rets) > 2 else np.nan
    
    f['vol_surge_20d'] = np.full(n, 1.0)
    for i in range(40, n):
        ws = max(0, i-40)
        hist_vol = vol20[ws:i]
        valid = hist_vol[~np.isnan(hist_vol)]
        if len(valid) > 10:
            hist_mean = np.mean(valid)
            f['vol_surge_20d'][i] = vol20[i] / (hist_mean + 1e-10) if not np.isnan(vol20[i]) else 1.0
    
    # ── 低波动率积累（突破前兆）──────────────────
    f['low_vol_accum_60d'] = np.full(n, 0.0)
    for i in range(60, n):
        ws = max(0, i-60)
        window = vol20[ws:i+1]
        valid = window[~np.isnan(window)]
        if len(valid) > 20:
            hist_median = np.median(valid)
            recent_mean = np.mean(valid[-10:]) if len(valid) >= 10 else hist_median
            f['low_vol_accum_60d'][i] = recent_mean / (hist_median + 1e-10) if not np.isnan(recent_mean) else 1.0
    
    return f


# ═══════════════════════════════════════════════════════════
# 综合提取
# ═══════════════════════════════════════════════════════════

def extract_all_new_factors(df, max_bars=1500):
    """提取所有新因子"""
    c = df['close'].values.astype(np.float64)
    h = df['high'].values.astype(np.float64)
    l = df['low'].values.astype(np.float64)
    v = df['volume'].values.astype(np.float64)
    o = c  # 使用收盘价简化
    
    n = min(len(df), max_bars)
    
    f = {}
    dates = df['trade_date'].values[-n:] if 'trade_date' in df.columns else ['']*n
    
    # RSI变种
    rsi_factors = extract_rsi_variants(o, h, l, c)
    f.update(rsi_factors)
    
    # 缠论增强
    chan_factors = extract_chan_enhanced(h, l, c, o)
    f.update(chan_factors)
    
    # 趋势质量
    trend_factors = extract_trend_quality(o, h, l, c)
    f.update(trend_factors)
    
    # 波动率异常
    vol_factors = extract_volatility_anomaly(o, h, l, c, v)
    f.update(vol_factors)
    
    # 标签
    f['fwd_ret_5d'] = np.array([(c[i+5]-c[i])/c[i] if i+5<n else np.nan for i in range(n)])
    f['fwd_ret_20d'] = np.array([(c[i+20]-c[i])/c[i] if i+20<n else np.nan for i in range(n)])
    
    return f, dates


# ═══════════════════════════════════════════════════════════
# IC计算
# ═══════════════════════════════════════════════════════════

def calc_ic(df, col, label='fwd_ret_5d', min_n=60):
    df2 = df.dropna(subset=[col, label])
    if len(df2) < min_n: return np.nan, np.nan
    ic = df2[col].corr(df2[label])
    ric = spearmanr(df2[col], df2[label])[0]
    return ic, ric

def calc_monthly_ic(df, col, label='fwd_ret_5d'):
    df2 = df.dropna(subset=[col, label]).copy()
    if len(df2) < 20: return []
    df2['month'] = df2['trade_date'].astype(str).str[:7]
    monthly = []
    for m, g in df2.groupby('month'):
        if len(g) > 5:
            c = g[col].corr(g[label])
            if not np.isnan(c): monthly.append(c)
    return monthly


# ═══════════════════════════════════════════════════════════
# 主程序
# ═══════════════════════════════════════════════════════════

if __name__ == '__main__':
    pass  # stdout already set at module level

    OUT_DIR = r'E:\quant\factor_research'
    os.makedirs(OUT_DIR, exist_ok=True)

    # 数据源
    CSI300_DIR = r'E:\quant\ml_alpha\csi300_cache'
    SPX_DIR = r'E:\quant\ml_alpha\spx_cache'

    # 检查数据
    csi_files = sorted([f for f in os.listdir(CSI300_DIR) if f.endswith('.csv')]) if os.path.exists(CSI300_DIR) else []
    spx_files = sorted([f for f in os.listdir(SPX_DIR) if f.endswith('.csv')]) if os.path.exists(SPX_DIR) else []

    MARKET_FILES = [
        ('CSI300', CSI300_DIR, csi_files[:30]),
        ('SPX', SPX_DIR, spx_files[:30]),
    ]

    all_factors_found = set()

    for mname, cache_dir, files in MARKET_FILES:
        if not files: continue
        all_rows = []
        print(f'提取 {mname} 新因子 ({len(files)} 只)...')

        for i, fname in enumerate(files):
            try:
                df = pd.read_csv(os.path.join(cache_dir, fname))
                if len(df) < 60: continue
                
                if 'volume' in df.columns:
                    first_valid = df[df['volume'] > 0].index.min()
                    if pd.isna(first_valid): continue
                    df = df.loc[first_valid:].reset_index(drop=True)
                
                sym = fname.replace('.csv', '')
                feats, dates = extract_all_new_factors(df, max_bars=2000)
                n = len(feats.get('rsi_14', []))
                
                for name in feats.keys():
                    all_factors_found.add(name)
                
                for j in range(30, n):
                    row = {'symbol': sym, 'trade_date': str(dates[j])[:10] if j < len(dates) else ''}
                    for name, vals in feats.items():
                        if j < len(vals):
                            v = vals[j]
                            if not (np.isnan(v) or np.isinf(v)): row[name] = v
                    all_rows.append(row)
                
                if (i+1) % 10 == 0:
                    print(f'  {i+1}/{len(files)} ... {len(all_rows)} rows')
                    
            except Exception as e:
                print(f'  Error {fname}: {e}')

        if all_rows:
            mdf = pd.DataFrame(all_rows)
            mdf['trade_date'] = pd.to_datetime(mdf['trade_date'], errors='coerce')
            mdf = mdf.dropna(subset=['trade_date'])
            
            out_path = os.path.join(OUT_DIR, f'new_factors_{mname.lower()}.csv')
            mdf.to_csv(out_path, index=False)
            print(f'  {mname}: {len(mdf)} rows, {mdf["symbol"].nunique()} stocks')
            print(f'  输出: {out_path}')
            print(f'  因子总数: {len([c for c in mdf.columns if c not in ["symbol","trade_date","fwd_ret_5d","fwd_ret_20d"]])}')
        print()

    # IC分析
    print('='*70)
    print('新因子 IC 分析')
    print('='*70)

    for mname, cache_dir, files in MARKET_FILES:
        csv_path = os.path.join(OUT_DIR, f'new_factors_{mname.lower()}.csv')
        if not os.path.exists(csv_path): continue
        
        print(f'\n{mname}:')
        df = pd.read_csv(csv_path)
        
        cutoff = df['trade_date'].max() - pd.Timedelta(days=730)
        df = df[df['trade_date'] >= cutoff]
        
        if len(df) < 100:
            print(f'  数据不足: {len(df)} rows')
            continue
        
        print(f'  {len(df)} rows, {df["symbol"].nunique()} stocks')
        
        factor_cols = [c for c in df.columns if c not in ['symbol','trade_date','fwd_ret_5d','fwd_ret_20d']]
        
        results = []
        for col in factor_cols:
            ic, ric = calc_ic(df, col)
            monthly = calc_monthly_ic(df, col)
            wr = np.mean([1 for m in monthly if m > 0]) if monthly else 0
            
            if not np.isnan(ric) and abs(ric) > 0.03:
                results.append({
                    'factor': col, 'ic': ic, 'rank_ic': ric,
                    'win_rate': wr, 'n_months': len(monthly), 'abs_ric': abs(ric)
                })
        
        if results:
            results = sorted(results, key=lambda x: x['abs_ric'], reverse=True)
            print(f'\n  {"因子":35s} {"IC":>8s} {"RankIC":>8s} {"月胜":>6s} {"月数":>4s}')
            print(f'  {"-"*70}')
            for r in results[:30]:
                stars = '⭐⭐⭐' if r['abs_ric'] > 0.08 else ('⭐⭐' if r['abs_ric'] > 0.05 else '⭐')
                print(f'  {r["factor"]:35s} {r["ic"]:+8.4f} {r["rank_ic"]:+8.4f} {r["win_rate"]:6.1%} {r["n_months"]:4d} {stars}')
            
            ic_df = pd.DataFrame(results)
            ic_df.to_csv(os.path.join(OUT_DIR, f'new_factor_ic_{mname.lower()}.csv'), index=False)
        else:
            print(f'  无显著因子 (|RankIC| > 0.03)')

    print(f'\n完成! {datetime.now().strftime("%H:%M:%S")}')
