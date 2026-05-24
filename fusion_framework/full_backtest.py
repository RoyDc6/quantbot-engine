# -*- coding: utf-8 -*-
import os
os.chdir(r'E:\quant\fusion_framework')
import sys
sys.path.insert(0, 'E:/quant/fusion_framework')
sys.path.insert(0, 'E:/quant')

import warnings
warnings.filterwarnings('ignore')
import numpy as np
import pandas as pd

# ─── 加载SPY 1年数据 ─────────────────────────────────────────
def load_spy_data():
    # 优先用1年数据
    for path in [
        "E:/quant/scanner/cache/SPY_US_1y.json",
        "E:/quant/scanner/cache/SPY_US.json",
    ]:
        if os.path.exists(path):
            df = pd.read_json(path)
            if 'datetime' in df.columns:
                df['trade_date'] = pd.to_datetime(df['datetime']).dt.strftime('%Y-%m-%d')
            df = df.sort_values('trade_date').reset_index(drop=True)
            return df
    return None

# ─── 指标 ───────────────────────────────────────────────────
def compute_rsi(close, n=14):
    d = np.diff(close)
    g = np.where(d > 0, d, 0.0)
    l = np.where(d < 0, -d, 0.0)
    r = np.full(len(close), 50.0)
    if len(close) <= n: return r
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
    ef = s.ewm(span=12, adjust=False).mean().values
    es = s.ewm(span=26, adjust=False).mean().values
    macd = ef - es
    sig = pd.Series(macd).ewm(span=9, adjust=False).mean().values
    return macd - sig

def compute_weekly_rsi(close, period=4):
    n = len(close)
    weekly = [close[i * 5 + 4] for i in range(n // 5) if i * 5 + 4 < n]
    if len(weekly) < period + 1: return np.full(n, 50.0)
    wrsi = compute_rsi(np.array(weekly), period)
    result = np.full(n, 50.0)
    for i in range(n):
        w = i // 5
        if w < len(wrsi): result[i] = wrsi[w]
    return result

# ─── 信号生成（适配SPY趋势市场） ──────────────────────────────
def generate_fm_signals(df):
    close = df['close'].values.astype(float)
    n = len(close)
    rsi_d = compute_rsi(close, 14)
    rsi_w = compute_weekly_rsi(close, 4)
    sma20 = compute_sma(close, 20)
    signals = []
    for i in range(60, n):
        date = df['trade_date'].iloc[i]
        c = close[i]
        score = 0.202 * (50 - rsi_w[i])  # 简化：只用周RSI因子
        above = not np.isnan(sma20[i]) and c > sma20[i]
        thr = 3 if above else 6  # 趋势中更灵敏
        if score >= thr:
            sig, conf = 'BUY', min(0.9, 0.5 + score / 50)
        elif score <= -thr:
            sig, conf = 'SELL', min(0.9, 0.5 + abs(score) / 50)
        else:
            sig, conf = 'HOLD', 0.5
        from signal_types import FusionModelSignal
        signals.append(FusionModelSignal(
            'SPY.US', date, float(score), float(rsi_w[i]), float(rsi_d[i]),
            0.0, sig, float(conf)))
    return signals

def generate_xmm_signals(df):
    close = df['close'].values.astype(float)
    n = len(close)
    rsi_d = compute_rsi(close, 14)
    sma20 = compute_sma(close, 20)
    macd_h = compute_macd(close)
    signals = []
    for i in range(60, n):
        date = df['trade_date'].iloc[i]
        c = close[i]
        rsi = rsi_d[i]
        above = not np.isnan(sma20[i]) and c > sma20[i]
        macd_pos = macd_h[i] > 0
        # SPY趋势市场：SMA20多头 + MACD正向 → BUY
        if above and macd_pos:
            action, conf = 'BUY', 0.72
        elif rsi >= 75 or (not above and rsi > 65):
            action, conf = 'SELL', 0.70
        else:
            action, conf = 'HOLD', 0.5
        sma_p = c / sma20[i] if not np.isnan(sma20[i]) else 1.0
        from signal_types import XMMSignal
        signals.append(XMMSignal('SPY.US', date, action, float(conf),
                                float(rsi), float(sma_p), float(macd_h[i])))
    return signals

# ─── 回测 ────────────────────────────────────────────────────
def run_backtest(fm_signals, xmm_signals, df):
    from fusion_engine import FusionEngine
    from signal_types import SignalLevel
    engine = FusionEngine()
    fm_d = {s.date: s for s in fm_signals}
    xmm_d = {s.date: s for s in xmm_signals}
    capital = 100000.0
    commission = 0.001; slippage = 0.0005
    strategies = {
        '融合框架': {'cash': capital, 'pos': 0, 'trades': [], 'values': []},
        '融合模型': {'cash': capital, 'pos': 0, 'trades': [], 'values': []},
        'XMM系统':  {'cash': capital, 'pos': 0, 'trades': [], 'values': []},
    }
    for i in range(60, len(df)):
        date = str(df['trade_date'].iloc[i])
        close = float(df['close'].iloc[i])
        fm = fm_d.get(date); xmm_sig = xmm_d.get(date)
        # 融合框架
        fusion = engine.fuse(fm, xmm_sig, 'SPX')
        s = strategies['融合框架']
        if fusion.level == SignalLevel.STRONG_BUY and s['pos'] == 0:
            shares = int(s['cash'] * fusion.position_pct / close)
            if shares > 0:
                s['cash'] -= shares * close * (1 + commission + slippage)
                s['pos'] = shares
                s['trades'].append({'date': date, 'action': 'BUY', 'price': close,
                                    'signal': fusion.level.value, 'reasoning': fusion.reasoning})
        elif fusion.level in (SignalLevel.STRONG_SELL, SignalLevel.SELL) and s['pos'] > 0:
            s['cash'] += s['pos'] * close * (1 - commission - slippage)
            s['trades'].append({'date': date, 'action': 'SELL', 'price': close})
            s['pos'] = 0
        s['values'].append(s['cash'] + s['pos'] * close)
        # 融合模型
        fm_a = fm.raw_signal if fm else 'HOLD'
        s = strategies['融合模型']
        if fm_a == 'BUY' and s['pos'] == 0:
            shares = int(s['cash'] * 0.6 / close)
            if shares > 0:
                s['cash'] -= shares * close * (1 + commission + slippage)
                s['pos'] = shares; s['trades'].append(
                    {'date': date, 'action': 'BUY', 'price': close, 'score': fm.score if fm else 0})
        elif fm_a == 'SELL' and s['pos'] > 0:
            s['cash'] += s['pos'] * close * (1 - commission - slippage)
            s['trades'].append({'date': date, 'action': 'SELL', 'price': close}); s['pos'] = 0
        s['values'].append(s['cash'] + s['pos'] * close)
        # XMM系统
        xmm_a = xmm_sig.action if xmm_sig else 'HOLD'
        s = strategies['XMM系统']
        if xmm_a == 'BUY' and s['pos'] == 0:
            shares = int(s['cash'] * 0.5 / close)
            if shares > 0:
                s['cash'] -= shares * close * (1 + commission + slippage)
                s['pos'] = shares; s['trades'].append(
                    {'date': date, 'action': 'BUY', 'price': close, 'rsi': xmm_sig.rsi if xmm_sig else 0})
        elif xmm_a == 'SELL' and s['pos'] > 0:
            s['cash'] += s['pos'] * close * (1 - commission - slippage)
            s['trades'].append({'date': date, 'action': 'SELL', 'price': close}); s['pos'] = 0
        s['values'].append(s['cash'] + s['pos'] * close)
    return strategies

def calc_stats(trades, values, initial=100000.0):
    if not values: return {}
    final = values[-1]
    vals = np.array(values)
    ret = (final / initial - 1) * 100
    rets = np.diff(vals) / vals[:-1]; rets = rets[np.isfinite(rets)]
    sharpe = (np.mean(rets) / (np.std(rets) + 1e-10) * np.sqrt(252)) if len(rets) > 1 and np.std(rets) > 1e-10 else 0.0
    peak = np.maximum.accumulate(vals); dd = (vals - peak) / peak
    return {'return': ret, 'sharpe': sharpe, 'max_dd': np.min(dd)*100,
            'trades': len(trades), 'final': final}

# ─── 主程序 ───────────────────────────────────────────────────
print("=" * 70)
print("融合框架 SPY 完整回测（500天数据）")
print("=" * 70)

df = load_spy_data()
if df is None:
    print("数据加载失败"); exit(1)

close = df['close'].values
n = len(df)
print(f"\n数据: {df['trade_date'].iloc[0]} ~ {df['trade_date'].iloc[-1]} ({n}天)")
print(f"收益: {close[0]:.2f} -> {close[-1]:.2f} (+{(close[-1]/close[0]-1)*100:.1f}%)")

# 生成信号
fm_s = generate_fm_signals(df)
xmm_s = generate_xmm_signals(df)
print(f"\n信号（{len(fm_s)}天）:")
print(f"  融合模型 BUY:{sum(1 for s in fm_s if s.raw_signal=='BUY'):>3}  "
      f"SELL:{sum(1 for s in fm_s if s.raw_signal=='SELL'):>3}")
print(f"  XMM系统   BUY:{sum(1 for s in xmm_s if s.action=='BUY'):>3}  "
      f"SELL:{sum(1 for s in xmm_s if s.action=='SELL'):>3}")

# 回测
results = run_backtest(fm_s, xmm_s, df)

# 基准
bh = (close[-1] / close[0] - 1) * 100

print("\n" + "=" * 70)
print("回测结果对比（500天）")
print("=" * 70)
hdr = f"{'策略':<18} {'收益':>10} {'夏普':>8} {'最大回撤':>10} {'交易数':>8} {'最终市值':>14}"
print(hdr)
print("-" * 72)
for name, s in results.items():
    st = calc_stats(s['trades'], s['values'])
    bar = '█' * int(st.get('return', 0) / bh * 10) if st.get('return', 0) > 0 else '░' * int(abs(st.get('return', 0)) / bh * 10)
    print(f"{name:<18} {st.get('return',0):>+9.2f}% {st.get('sharpe',0):>8.2f} "
          f"{st.get('max_dd',0):>+9.2f}% {st.get('trades',0):>8} {st.get('final',0):>14,.0f}  {bar}")
print("-" * 72)
bh_bar = '█' * 10
print(f"{'买入持有SPY':<18} {bh:>+9.2f}% {'--':>8} {'--':>10} {'--':>8} {close[-1]/close[0]*100000:>14,.0f}  {bh_bar}")

# 融合框架交易记录
print("\n" + "=" * 70)
print("融合框架交易明细")
print("=" * 70)
trades = results['融合框架']['trades']
if trades:
    for t in trades[:15]:
        print(f"  {t['date']}  {t['action']}  ${t['price']:.2f}  [{t.get('signal','')}]")
        if 'reasoning' in t and t['reasoning']:
            print(f"    -> {t['reasoning'][:80]}")
else:
    print("  无交易")
