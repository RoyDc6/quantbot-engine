# -*- coding: utf-8 -*-
"""
融合框架 SPY 真实回测
加载历史数据 → 生成双系统信号 → 融合 → 回测对比
"""

import sys, io, os, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import warnings
import numpy as np
import pandas as pd
warnings.filterwarnings('ignore')

sys.path.insert(0, 'E:/quant')
sys.path.insert(0, 'E:/quant/fusion_framework')
from fusion_engine import FusionEngine
from signal_types import SignalLevel, FusionModelSignal, XMMSignal


# ─── 加载SPY数据 ──────────────────────────────────────────
def load_spy_data():
    cache_path = "E:/quant/scanner/cache/SPY_daily_2500.json"
    if not os.path.exists(cache_path):
        print(f"错误: {cache_path} 不存在")
        return None
    with open(cache_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    df = pd.DataFrame(data)
    df = df.sort_values('trade_date').reset_index(drop=True)
    return df[['trade_date', 'open', 'high', 'low', 'close', 'volume']]


# ─── 技术指标 ─────────────────────────────────────────────
def compute_rsi(close, n=14):
    d = np.diff(close)
    g = np.where(d > 0, d, 0.0)
    l = np.where(d < 0, -d, 0.0)
    r = np.full(len(close), 50.0)
    if len(close) <= n:
        return r
    ag, al = np.mean(g[:n]), np.mean(l[:n])
    r[n] = 50.0 if al < 1e-10 else 100 - 100 / (1 + ag / (al + 1e-10))
    for i in range(n + 1, len(close)):
        ag = (ag * (n - 1) + g[i - 1]) / n
        al = (al * (n - 1) + l[i - 1]) / n
        r[i] = 50.0 if al < 1e-10 else 100 - 100 / (1 + ag / (al + 1e-10))
    return r


def compute_sma(close, n):
    sma = np.full(len(close), np.nan)
    for i in range(n - 1, len(close)):
        sma[i] = np.mean(close[i - n + 1:i + 1])
    return sma


def compute_macd(close):
    s = pd.Series(close)
    ema_f = s.ewm(span=12, adjust=False).mean().values
    ema_s = s.ewm(span=26, adjust=False).mean().values
    macd = ema_f - ema_s
    signal = pd.Series(macd).ewm(span=9, adjust=False).mean().values
    return macd - signal


def compute_weekly_rsi(close, period=4):
    n = len(close)
    weekly = [close[i * 5 + 4] for i in range(n // 5) if i * 5 + 4 < n]
    if len(weekly) < period + 1:
        return np.full(n, 50.0)
    wrsi = compute_rsi(np.array(weekly), period)
    result = np.full(n, 50.0)
    for i in range(n):
        w = i // 5
        if w < len(wrsi):
            result[i] = wrsi[w]
    return result


def compute_resonance(rsi_d, rsi_w):
    res = np.zeros_like(rsi_d, dtype=float)
    res += np.where(rsi_d < 30, 1, 0) + np.where(rsi_d > 70, -1, 0)
    res += np.where(rsi_w < 30, 0.5, 0) + np.where(rsi_w > 70, -0.5, 0)
    return res


# ─── 生成信号 ──────────────────────────────────────────────
def generate_fm_signals(df):
    close = df['close'].values.astype(float)
    n = len(close)
    rsi_d = compute_rsi(close, 14)
    rsi_w = compute_weekly_rsi(close, 14)  # RSI(14) 稳定周线
    sma20 = compute_sma(close, 20)
    resonance = compute_resonance(rsi_d, rsi_w)
    signals = []
    for i in range(60, n):
        date = df['trade_date'].iloc[i]
        c = close[i]
        score = 0.202 * (50 - rsi_w[i]) + 0.152 * resonance[i] * 50
        above_sma20 = not np.isnan(sma20[i]) and c > sma20[i]
        # SPY趋势市场：趋势中用宽松阈值，逆势用严格阈值
        thr = 3 if above_sma20 else 8
        if score >= thr:
            sig, conf = 'BUY', min(0.9, 0.5 + abs(score) / 100)
        elif score <= -thr:
            sig, conf = 'SELL', min(0.9, 0.5 + abs(score) / 100)
        else:
            sig, conf = 'HOLD', 0.5
        signals.append(FusionModelSignal(
            'SPY.US', date, float(score), float(rsi_w[i]), float(rsi_d[i]),
            float(resonance[i]), sig, float(conf)))
    return signals


def generate_xmm_signals(df):
    """XMM系统: 均线交叉 + MACD 趋势策略（适配SPY趋势市场）"""
    close = df['close'].values.astype(float)
    n = len(close)
    rsi_d = compute_rsi(close, 14)
    sma20 = compute_sma(close, 20)
    sma50 = compute_sma(close, 50)
    macd_h = compute_macd(close)
    signals = []
    for i in range(60, n):
        date = df['trade_date'].iloc[i]
        c = close[i]
        rsi = rsi_d[i]
        above_sma20 = not np.isnan(sma20[i]) and c > sma20[i]
        above_sma50 = not np.isnan(sma50[i]) and c > sma50[i]
        macd_pos = macd_h[i] > 0
        # 三重确认趋势买入
        if above_sma20 and above_sma50 and macd_pos:
            action, conf = 'BUY', 0.75
        elif not above_sma20 or rsi >= 75:
            action, conf = 'SELL', 0.70
        else:
            action, conf = 'HOLD', 0.5
        sma_p = c / sma50[i] if not np.isnan(sma50[i]) else 1.0
        signals.append(XMMSignal('SPY.US', date, action, float(conf),
                                float(rsi), float(sma_p), float(macd_h[i])))
    return signals


# ─── 加载VIX数据 ──────────────────────────────────────────
def load_vix_data():
    """加载 VXX 作为 VIX 代理，返回 {date: close} 字典"""
    vix_path = "E:/quant/scanner/cache/VXX_US.json"
    if not os.path.exists(vix_path):
        print(f"⚠️ VXX数据未找到 ({vix_path})，VIX风控将不生效")
        return {}
    with open(vix_path, 'r', encoding='utf-8') as f:
        vxx_data = json.load(f)
    from datetime import datetime as _dt
    vix_map = {}
    for r in vxx_data:
        d = _dt.fromtimestamp(r['date'] / 1000).strftime('%Y-%m-%d')
        vix_map[d] = float(r['close'])
    print(f"VIX数据: {len(vix_map)}天, {min(vix_map.keys())} ~ {max(vix_map.keys())}")
    print(f"VIX范围: {min(vix_map.values()):.1f} ~ {max(vix_map.values()):.1f}")
    return vix_map


# ─── 回测 ──────────────────────────────────────────────────
def run_backtest(fm_signals, xmm_signals, df, vix_map=None):
    """
    运行回测，可选接入VIX风控。
    vix_map=None → 无VIX风控（基线）
    vix_map=dict  → 启用真实VIX分位数风控
    """
    engine = FusionEngine()
    if vix_map:
        engine.set_vix_data(vix_map)
    fm_d = {s.date: s for s in fm_signals}
    xmm_d = {s.date: s for s in xmm_signals}

    capital = 100000.0
    commission = 0.001
    slippage = 0.0005

    strategies = {
        '融合框架': {'cash': capital, 'pos': 0, 'trades': [], 'values': []},
        '融合模型': {'cash': capital, 'pos': 0, 'trades': [], 'values': []},
        'XMM系统': {'cash': capital, 'pos': 0, 'trades': [], 'values': []},
    }

    for i in range(60, len(df)):
        date = str(df['trade_date'].iloc[i])
        close = float(df['close'].iloc[i])
        # ── 融合框架 v3：FM管进出（均值回归闭环），XMM只做持仓安全网
        fm = fm_d.get(date)
        xmm = xmm_d.get(date)
        fm_act = fm.raw_signal if fm else 'HOLD'
        xmm_act = xmm.action if xmm else 'HOLD'
        fm_score = fm.score if fm else 0
        
        s = strategies['融合框架']
        if s['pos'] == 0:
            # 入场：FM BUY
            if fm_act == 'BUY':
                shares = int(s['cash'] * 0.6 / close)
                if shares > 0:
                    s['cash'] -= shares * close * (1 + commission + slippage)
                    s['pos'] = shares
                    s['entry_price'] = close; s['entry_idx'] = i
                    s['trades'].append({'date': date, 'action': 'BUY', 'price': close,
                                        'shares': shares, 'signal': f'FM={fm_act}|XMM={xmm_act}',
                                        'rsi_w': fm.weekly_rsi if fm else 0})
        else:
            days_held = i - s.get('entry_idx', i)
            # 出场1：FM SELL（均值回归完成，RSI回高位）
            # 出场2：TA持续SELL超过10天（趋势确认恶化，止损兜底）
            fm_exit = fm_act == 'SELL'
            xmm_safety = xmm_act == 'SELL' and days_held > 10
            if fm_exit or xmm_safety:
                s['cash'] += s['pos'] * close * (1 - commission - slippage)
                pnl = (close - s['entry_price']) / s['entry_price']
                s['trades'].append({'date': date, 'action': 'SELL', 'price': close,
                                    'signal': f'FM={fm_act}|XMM={xmm_act}',
                                    'days': days_held, 'pnl': pnl})
                s['pos'] = 0
        s['values'].append(s['cash'] + s['pos'] * close)

        # 融合模型
        fm_a = fm.raw_signal if fm else 'HOLD'
        s = strategies['融合模型']
        if fm_a == 'BUY' and s['pos'] == 0:
            shares = int(s['cash'] * 0.6 / close)
            if shares > 0:
                s['cash'] -= shares * close * (1 + commission + slippage)
                s['pos'] = shares
                s['trades'].append({'date': date, 'action': 'BUY', 'price': close,
                                    'signal': fm_a, 'score': fm.score if fm else 0})
        elif fm_a == 'SELL' and s['pos'] > 0:
            s['cash'] += s['pos'] * close * (1 - commission - slippage)
            s['trades'].append({'date': date, 'action': 'SELL', 'price': close})
            s['pos'] = 0
        s['values'].append(s['cash'] + s['pos'] * close)

        # XMM系统
        xmm_a = xmm.action if xmm else 'HOLD'
        s = strategies['XMM系统']
        if xmm_a == 'BUY' and s['pos'] == 0:
            shares = int(s['cash'] * 0.5 / close)
            if shares > 0:
                s['cash'] -= shares * close * (1 + commission + slippage)
                s['pos'] = shares
                s['trades'].append({'date': date, 'action': 'BUY', 'price': close,
                                    'signal': xmm_a, 'rsi': xmm.rsi if xmm else 0})
        elif xmm_a == 'SELL' and s['pos'] > 0:
            s['cash'] += s['pos'] * close * (1 - commission - slippage)
            s['trades'].append({'date': date, 'action': 'SELL', 'price': close})
            s['pos'] = 0
        s['values'].append(s['cash'] + s['pos'] * close)

    return strategies


def calc_stats(trades, values, initial=100000.0):
    if not values:
        return {}
    final = values[-1]
    total_ret = (final / initial - 1) * 100
    vals = np.array(values)
    rets = np.diff(vals) / vals[:-1]
    rets = rets[np.isfinite(rets)]
    if len(rets) > 1 and np.std(rets) > 1e-10:
        sharpe = np.mean(rets) / np.std(rets) * np.sqrt(252)
    else:
        sharpe = 0.0
    peak = np.maximum.accumulate(vals)
    dd = (vals - peak) / peak
    max_dd = np.min(dd) * 100
    return {'total_return': total_ret, 'sharpe': sharpe,
            'max_drawdown': max_dd, 'num_trades': len(trades), 'final_value': final}


if __name__ == '__main__':
    print("=" * 70)
    print("融合框架 SPY 回测 - VIX风控对照实验")
    print("=" * 70)

    print("\n[1/4] 加载数据...")
    df = load_spy_data()
    if df is None:
        exit(1)
    print(f"SPY数据: {df['trade_date'].iloc[0]} ~ {df['trade_date'].iloc[-1]} ({len(df)}天)")

    print("\n[2/4] 加载VIX数据...")
    vix_map = load_vix_data()

    print("\n[3/4] 生成信号...")
    fm_s = generate_fm_signals(df)
    xmm_s = generate_xmm_signals(df)
    print(f"  融合模型: {len(fm_s)} 个信号")
    print(f"  XMM系统: {len(xmm_s)} 个信号")

    print("\n[4/4] 运行对照回测...")
    print("  对照组: 无VIX风控...")
    results_no_vix = run_backtest(fm_s, xmm_s, df, vix_map=None)
    print("  实验组: 真实VIX风控...")
    results_vix = run_backtest(fm_s, xmm_s, df, vix_map=vix_map)

    # ─── 对照报告 ───
    closes = df['close'].values[60:]
    bh = (closes[-1] / closes[0] - 1) * 100

    print("\n" + "=" * 80)
    print("【对照组】无VIX风控（基线）")
    print("=" * 80)
    hdr = f"{'策略':<20} {'收益':>10} {'夏普':>8} {'最大回撤':>10} {'交易数':>8} {'最终市值':>14}"
    print(hdr)
    print("-" * 74)
    for name, s in results_no_vix.items():
        st = calc_stats(s['trades'], s['values'])
        print(f"{name:<20} {st.get('total_return',0):>+9.2f}% {st.get('sharpe',0):>8.2f} "
              f"{st.get('max_drawdown',0):>+9.2f}% {st.get('num_trades',0):>8} "
              f"{st.get('final_value',0):>14,.0f}")
    print("-" * 74)
    print(f"{'买入持有SPY':<20} {bh:>+9.2f}% {'--':>8} {'--':>10} {'--':>8} "
          f"{closes[-1]/closes[0]*100000:>14,.0f}")

    print("\n" + "=" * 80)
    print("【实验组】真实VIX分位数风控")
    print("=" * 80)
    print(hdr)
    print("-" * 74)
    for name, s in results_vix.items():
        st = calc_stats(s['trades'], s['values'])
        print(f"{name:<20} {st.get('total_return',0):>+9.2f}% {st.get('sharpe',0):>8.2f} "
              f"{st.get('max_drawdown',0):>+9.2f}% {st.get('num_trades',0):>8} "
              f"{st.get('final_value',0):>14,.0f}")
    print("-" * 74)
    print(f"{'买入持有SPY':<20} {bh:>+9.2f}% {'--':>8} {'--':>10} {'--':>8} "
          f"{closes[-1]/closes[0]*100000:>14,.0f}")

    # ─── 融合框架 A/B 对比 ───
    print("\n" + "=" * 80)
    print("【核心对比】融合框架：无VIX vs VIX风控")
    print("=" * 80)
    st_no = calc_stats(results_no_vix['融合框架']['trades'], results_no_vix['融合框架']['values'])
    st_vix = calc_stats(results_vix['融合框架']['trades'], results_vix['融合框架']['values'])
    
    metrics = [
        ('收益', 'total_return', '%', 1),
        ('夏普', 'sharpe', '', 1),
        ('最大回撤', 'max_drawdown', '%', -1),
        ('交易数', 'num_trades', '', -1),
        ('最终市值', 'final_value', '$', 1),
    ]
    print(f"{'指标':<12} {'无VIX':>12} {'VIX风控':>12} {'变化':>12} {'评价':>16}")
    print("-" * 68)
    for name, key, unit, better_dir in metrics:
        v0 = st_no.get(key, 0)
        v1 = st_vix.get(key, 0)
        diff = v1 - v0
        if unit == '$':
            s0, s1, sd = f"{v0:,.0f}", f"{v1:,.0f}", f"{diff:+,.0f}"
        elif unit == '%':
            s0, s1, sd = f"{v0:+.2f}%", f"{v1:+.2f}%", f"{diff:+.2f}%"
        else:
            s0, s1, sd = f"{v0:.2f}", f"{v1:.2f}", f"{diff:+.2f}"
        better = '✅ 改善' if diff * better_dir > 0 else ('⚠️ 变差' if diff * better_dir < 0 else '— 持平')
        print(f"{name:<12} {s0:>12} {s1:>12} {sd:>12} {better:>16}")

    # 交易明细
    print("\n" + "=" * 80)
    print("VIX风控组 - 融合框架交易明细 (前20笔)")
    print("=" * 80)
    for t in results_vix['融合框架']['trades'][:20]:
        print(f"  {t['date']}  {t['action']}  ${t['price']:.2f}  "
              f"{t.get('shares',0):>5}股  [{t.get('signal','')}]")
        if 'reasoning' in t:
            print(f"    → {t['reasoning']}")
