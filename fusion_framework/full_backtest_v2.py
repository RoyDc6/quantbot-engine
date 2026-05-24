# -*- coding: utf-8 -*-
"""
融合框架 SPY 生产回测 v2
- 融合模型: HOLD-only（缠论分形评分，生产中仅在分形触发时输出信号）
- XMM系统: 均线多头+MACD+RSI组合
- 融合引擎: 三层信号矩阵（依赖 trend_up + VIX 风控驱动）
"""
import os
os.chdir(r'E:\quant\fusion_framework')
import sys
sys.path.insert(0, 'E:/quant/fusion_framework')
sys.path.insert(0, 'E:/quant')

import warnings
warnings.filterwarnings('ignore')
import numpy as np
import pandas as pd

from fusion_engine import FusionEngine
from fusion_framework.signal_types import SignalLevel, FusionModelSignal, XMMSignal

# ─── 指标 ───────────────────────────────────────────────────
def rsi(close, n=14):
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

def sma(close, n):
    s = np.full(len(close), np.nan)
    for i in range(n - 1, len(close)): s[i] = np.mean(close[i-n+1:i+1])
    return s

def macd_hist(close):
    s = pd.Series(close)
    ef = s.ewm(span=12, adjust=False).mean().values
    es = s.ewm(span=26, adjust=False).mean().values
    macd = ef - es
    sig = pd.Series(macd).ewm(span=9, adjust=False).mean().values
    return macd - sig

# ─── 信号生成器（生产级） ─────────────────────────────────────
def gen_fm_signals(df):
    """
    融合模型信号 — HOLD-only（匹配生产环境）

    生产环境中 FusionModelSignal 由缠论分形计数（bot_cnt×2.0 / -top_cnt×2.0）驱动，
    绝大多数时间输出 HOLD。本函数模拟该行为：始终 HOLD，仅保留 trend_up 标记
    供 FusionEngine 信号矩阵的趋势自适应使用。

    历史背景 (2026-05-18)：此前版本使用 0.202×(50−weekly_rsi) 公式，
    该公式从未部署至生产（daily_runner.py / core/signal_engine.py 使用的
    是缠论分形打分），属于回测伪信号。已剔除。
    """
    close = df['close'].values.astype(float)
    n = len(close)
    sma50 = sma(close, 50)

    signals = []
    for i in range(60, n):
        date = df['trade_date'].iloc[i]
        c = close[i]
        trend_up = not np.isnan(sma50[i]) and c > sma50[i]

        # 始终 HOLD，分数为 0
        signals.append(FusionModelSignal(
            'SPY.US', date, 0.0, 50.0, 50.0, 0.0, 'HOLD', 0.5,
            {'trend_up': trend_up, 'sma50_above': trend_up}))

    return signals


def gen_xmm_signals(df):
    """XMM系统信号 - 均线多头+MACD+RSI组合"""
    close = df['close'].values.astype(float)
    n = len(close)
    rsi_d = rsi(close, 14)
    sma20 = sma(close, 20)
    sma50 = sma(close, 50)
    macd_h = macd_hist(close)

    signals = []
    for i in range(60, n):
        date = df['trade_date'].iloc[i]
        c = close[i]
        d = rsi_d[i]
        mh = macd_h[i]

        above20 = not np.isnan(sma20[i]) and c > sma20[i]
        above50 = not np.isnan(sma50[i]) and c > sma50[i]
        macd_pos = mh > 0

        # 三重确认买入（严格）
        if above20 and above50 and macd_pos and d < 70:
            action, conf = 'BUY', 0.75
        elif d >= 78 or (not above20 and d > 65):
            action, conf = 'SELL', 0.72
        else:
            action, conf = 'HOLD', 0.5

        signals.append(XMMSignal('SPY.US', date, action, float(conf),
                                float(d), float(c/sma20[i] if not np.isnan(sma20[i]) else 1.0),
                                float(mh)))
    return signals

# ─── 回测引擎 ────────────────────────────────────────────────
def backtest(fm_signals, xmm_signals, df, start_idx=60):
    engine = FusionEngine()
    if vix_date_map:
        engine.set_vix_data(vix_date_map)
    fm_d = {s.date: s for s in fm_signals}
    xmm_d = {s.date: s for s in xmm_signals}

    capital = 100000.0
    c_rate = 0.001; slip = 0.0005

    # ── 止损参数 ──
    STOP_LOSS_PCT     = -0.12   # 固定止损 -12%（与纸交易一致）
    TRAILING_STOP_PCT = -0.12   # 移动止损 -12%（与纸交易一致）
    MAX_DRAWDOWN_PCT  = -0.30   # 组合回撤止损 -30%（与纸交易一致）

    def make_state():
        return {'cash': capital, 'pos': 0, 'trades': [], 'values': [],
                'entry_price': 0.0, 'highest': 0.0, 'stop_count': 0, 'peak': capital}

    strats = {
        '融合框架': make_state(),
        '融合模型': make_state(),
        'XMM系统': make_state(),
    }

    for i in range(start_idx, len(df)):
        date = str(df['trade_date'].iloc[i])
        close = float(df['close'].iloc[i])
        fm = fm_d.get(date); xmm = xmm_d.get(date)

        # ── 三个策略各自止损检查 ──
        for name, s in strats.items():
            if s['pos'] > 0:
                s['highest'] = max(s['highest'], close)
                pnl = (close - s['entry_price']) / s['entry_price']
                dd_peak = (close - s['highest']) / s['highest']
                should_stop = False
                reason = ''
                if pnl <= STOP_LOSS_PCT:
                    should_stop = True; reason = f'固定止损 PnL={pnl:.1%}'
                elif dd_peak <= TRAILING_STOP_PCT:
                    should_stop = True; reason = f'移动止损 从高点回撤={dd_peak:.1%}'
                # 组合回撤
                cur_val = s['cash'] + s['pos'] * close
                s['peak'] = max(s['peak'], cur_val)
                dd_port = (cur_val - s['peak']) / s['peak'] if s['peak'] > 0 else 0
                if dd_port <= MAX_DRAWDOWN_PCT:
                    should_stop = True; reason = f'组合回撤止损 DD={dd_port:.1%}'

                if should_stop:
                    s['cash'] += s['pos'] * close * (1 - c_rate - slip)
                    s['trades'].append({'date': date, 'a': 'STOP', 'p': close, 'reason': reason})
                    s['pos'] = 0
                    s['stop_count'] += 1

        # 融合框架
        fusion = engine.fuse(fm, xmm, 'SPX')
        s = strats['融合框架']
        if fusion.level in (SignalLevel.STRONG_BUY, SignalLevel.BUY) and s['pos'] == 0:
            shares = int(s['cash'] * fusion.position_pct / close)
            if shares > 0:
                s['cash'] -= shares * close * (1 + c_rate + slip)
                s['pos'] = shares; s['entry_price'] = close; s['highest'] = close
                s['trades'].append({'date': date, 'a': 'BUY', 'p': close,
                                    'sh': shares, 'sig': fusion.level.value,
                                    'reason': fusion.reasoning[:60]})
        elif fusion.level in (SignalLevel.STRONG_SELL, SignalLevel.SELL) and s['pos'] > 0:
            s['cash'] += s['pos'] * close * (1 - c_rate - slip)
            s['trades'].append({'date': date, 'a': 'SELL', 'p': close}); s['pos'] = 0
        s['values'].append(s['cash'] + s['pos'] * close)

        # 融合模型
        fa = fm.raw_signal if fm else 'HOLD'
        s = strats['融合模型']
        if fa == 'BUY' and s['pos'] == 0:
            sh = int(s['cash'] * 0.6 / close)
            if sh > 0:
                s['cash'] -= sh * close * (1 + c_rate + slip)
                s['pos'] = sh; s['entry_price'] = close; s['highest'] = close
                s['trades'].append({'date': date, 'a': 'BUY', 'p': close})
        elif fa == 'SELL' and s['pos'] > 0:
            s['cash'] += s['pos'] * close * (1 - c_rate - slip)
            s['trades'].append({'date': date, 'a': 'SELL', 'p': close}); s['pos'] = 0
        s['values'].append(s['cash'] + s['pos'] * close)

        # XMM系统
        xmm_a = xmm.action if xmm else 'HOLD'
        s = strats['XMM系统']
        if xmm_a == 'BUY' and s['pos'] == 0:
            sh = int(s['cash'] * 0.5 / close)
            if sh > 0:
                s['cash'] -= sh * close * (1 + c_rate + slip)
                s['pos'] = sh; s['entry_price'] = close; s['highest'] = close
                s['trades'].append({'date': date, 'a': 'BUY', 'p': close})
        elif xmm_a == 'SELL' and s['pos'] > 0:
            s['cash'] += s['pos'] * close * (1 - c_rate - slip)
            s['trades'].append({'date': date, 'a': 'SELL', 'p': close}); s['pos'] = 0
        s['values'].append(s['cash'] + s['pos'] * close)

    return strats


def stats(trades, vals, init=100000.0):
    if not vals: return {}
    v = np.array(vals)
    ret = (v[-1]/init - 1)*100
    rets = np.diff(v)/v[:-1]; rets = rets[np.isfinite(rets)]
    sh = (np.mean(rets)/(np.std(rets)+1e-10)*np.sqrt(252)
          if len(rets)>1 and np.std(rets)>1e-10 else 0.0)
    peak = np.maximum.accumulate(v); dd = (v-peak)/peak
    return {'ret': ret, 'sh': sh, 'dd': np.min(dd)*100, 'n': len(trades), 'final': v[-1]}


# ─── 主程序 ───────────────────────────────────────────────────
print("=" * 72)
print("融合框架 SPY 生产回测 v3（500天数据 + 真实VIX风控）")
print("=" * 72)

# 加载 VXX 数据
import json as _json
vix_date_map = {}
vxx_path = "E:/quant/scanner/cache/VXX_US.json"
if os.path.exists(vxx_path):
    with open(vxx_path) as f:
        vxx_data = _json.load(f)
    from datetime import datetime as _dt
    for r in vxx_data:
        d = _dt.fromtimestamp(r['date']/1000).strftime('%Y-%m-%d')
        vix_date_map[d] = float(r['close'])
    print(f"VIX数据: {len(vix_date_map)} 天, {min(vix_date_map.keys())} ~ {max(vix_date_map.keys())}")
    print(f"VIX范围: {min(vix_date_map.values()):.1f} ~ {max(vix_date_map.values()):.1f}")
else:
    print("\u26a0\ufe0f VXX数据未找到，使用纯RSI风控")

# 加载数据
for path in ["E:/quant/scanner/cache/SPY_US_1y.json",
             "E:/quant/scanner/cache/SPY_US.json"]:
    if os.path.exists(path):
        df = pd.read_json(path)
        if 'datetime' in df.columns:
            df['trade_date'] = pd.to_datetime(df['datetime']).dt.strftime('%Y-%m-%d')
        df = df.sort_values('trade_date').reset_index(drop=True)
        break

close = df['close'].values
n = len(df)
print(f"\n数据: {df['trade_date'].iloc[0]} ~ {df['trade_date'].iloc[-1]} ({n}天)")
print(f"收益: {close[0]:.2f} -> {close[-1]:.2f} (+{(close[-1]/close[0]-1)*100:.1f}%)")

# 信号
fm_s = gen_fm_signals(df)
xmm_s = gen_xmm_signals(df)
n_sig = len(fm_s)

print(f"\n信号分布（{n_sig}天）:")
b_fm = sum(1 for s in fm_s if s.raw_signal=='BUY')
s_fm = sum(1 for s in fm_s if s.raw_signal=='SELL')
b_xmm = sum(1 for s in xmm_s if s.action=='BUY')
s_xmm = sum(1 for s in xmm_s if s.action=='SELL')
print(f"  融合模型 BUY:{b_fm:>3}  SELL:{s_fm:>3}  HOLD:{n_sig-b_fm-s_fm:>3}")
print(f"  XMM系统   BUY:{b_xmm:>3}  SELL:{s_xmm:>3}  HOLD:{n_sig-b_xmm-s_xmm:>3}")

# 融合信号分布
engine = FusionEngine()
if vix_date_map:
    engine.set_vix_data(vix_date_map)
fm_d = {s.date: s for s in fm_s}; xmm_d = {s.date: s for s in xmm_s}
fusion_dist = {}
for i in range(60, n):
    date = str(df['trade_date'].iloc[i])
    fusion = engine.fuse(fm_d.get(date), xmm_d.get(date), 'SPX')
    lv = fusion.level.value
    fusion_dist[lv] = fusion_dist.get(lv, 0) + 1
print(f"  融合信号: {dict(sorted(fusion_dist.items()))}")

# 回测
results = backtest(fm_s, xmm_s, df)
bh = (close[-1]/close[0]-1)*100

print(f"\n{'='*72}")
print(f"回测结果（{n_sig}天）")
print(f"{'='*72}")
print(f"{'策略':<16} {'收益':>10} {'夏普':>8} {'最大回撤':>10} {'交易数':>8} {'最终市值':>14}")
print("-" * 72)
for name, s in results.items():
    st = stats(s['trades'], s['values'])
    print(f"{name:<16} {st.get('ret',0):>+9.2f}% {st.get('sh',0):>8.2f} "
          f"{st.get('dd',0):>+9.2f}% {st.get('n',0):>8} {st.get('final',0):>14,.0f}")
print("-" * 72)
print(f"{'买入持有SPY':<16} {bh:>+9.2f}% {'--':>8} {'--':>10} {'--':>8} {close[-1]/close[0]*100000:>14,.0f}")

# 交易明细
print(f"\n{'='*72}")
print("融合框架交易明细")
print(f"{'='*72}")
for t in results['融合框架']['trades']:
    print(f"  {t['date']}  {t['a']}  ${t['p']:.2f}  {t.get('sh',0):>6}股  [{t.get('sig','')}]")
    if t.get('reason'): print(f"    -> {t['reason']}")

print(f"\n{'='*72}")
print("XMM系统交易明细")
print(f"{'='*72}")
for t in results['XMM系统']['trades']:
    print(f"  {t['date']}  {t['a']}  ${t['p']:.2f}")
