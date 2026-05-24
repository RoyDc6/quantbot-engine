# -*- coding: utf-8 -*-
"""
港股融合模型回测
- 数据: hk_features_full.csv (66列, 105只股票, 2014-2026)
- 信号: 融合模型(SMA20+RSI+resonance) + XMM系统(SMA+MACD+均线多头)
- 回测: 等权组合，每日再平衡
- 基准: 等权持有所有股票
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
from scipy.stats import spearmanr

# ─── 加载数据 ────────────────────────────────────────────────
DATA_PATH = 'E:/quant/ml_alpha/hk_features_full.csv'
df = pd.read_csv(DATA_PATH)
df = df.sort_values(['symbol', 'trade_date']).reset_index(drop=True)
print(f"加载: {len(df)}行 x {len(df.columns)}列, {df['symbol'].nunique()}只股票")
print(f"日期: {df['trade_date'].min()} ~ {df['trade_date'].max()}")

# 过滤：有完整因子的股票
required = ['rsi_14', 'macd_hist', 'ma20_ratio', 'resonance', 'close', 'future_ret_5d']
missing = [c for c in required if c not in df.columns]
if missing:
    print(f"缺少列: {missing}"); exit(1)

# 过滤价格太低的股票（避免壳股干扰）
df = df[df['close'] > 0.5].copy()
# 过滤停牌（volume=0）
df = df[df['volume'] > 0].copy()
print(f"过滤后: {len(df)}行, {df['symbol'].nunique()}只股票")

# 转换日期
df['trade_date'] = pd.to_datetime(df['trade_date'])
ALL_SYMBOLS = sorted(df['symbol'].unique())
ALL_DATES = sorted(df['trade_date'].unique())
print(f"股票: {len(ALL_SYMBOLS)}只, 日期: {len(ALL_DATES)}天 ({ALL_DATES[0].date()} ~ {ALL_DATES[-1].date()})")

# ─── 因子计算（周RSI）─────────────────────────────────────────
def compute_weekly_rsi_for_symbols(df):
    """为每只股票计算周RSI"""
    weekly_rsi_list = []
    for sym, g in df.groupby('symbol'):
        g = g.sort_values('trade_date')
        close = g['close'].values
        rsi_d = g['rsi_14'].values
        n = len(close)
        wrsi = np.full(n, np.nan)
        # 周线：每5天取一个收盘价
        weekly_close = [close[i*5+4] for i in range(n//5) if i*5+4 < n]
        weekly_rsi = np.full(len(weekly_close), 50.0)
        if len(weekly_close) >= 15:
            d = np.diff(weekly_close)
            g2 = np.where(d > 0, d, 0.0)
            l2 = np.where(d < 0, -d, 0.0)
            period = 4  # 4周RSI
            ag, al = np.mean(g2[:period]), np.mean(l2[:period])
            weekly_rsi[period] = 50.0 if al < 1e-10 else 100 - 100 / (1 + ag / (al + 1e-10))
            for i in range(period+1, len(weekly_close)):
                ag = (ag * (period-1) + g2[i-1]) / period
                al = (al * (period-1) + l2[i-1]) / period
                weekly_rsi[i] = 100 - 100 / (1 + ag / (al + 1e-10)) if al > 1e-10 else 100.0
        # 映射回日线
        for i in range(n):
            w = i // 5
            if w < len(weekly_rsi): wrsi[i] = weekly_rsi[w]
        tmp = g.copy()
        tmp['weekly_rsi'] = wrsi
        weekly_rsi_list.append(tmp)
    return pd.concat(weekly_rsi_list, ignore_index=True)

print("\n计算周RSI...")
df = compute_weekly_rsi_for_symbols(df)
print(f"周RSI计算完成，样本: {df['weekly_rsi'].notna().sum()}条")

# ─── 信号生成器 ───────────────────────────────────────────────
def generate_signals_for_date_symbols(df, date, lookback=60):
    """为某一天的多个股票生成信号"""
    signals = []
    # 当日数据
    today_df = df[df['trade_date'] == date].copy()
    if len(today_df) < 5: return signals

    for _, row in today_df.iterrows():
        sym = row['symbol']
        # 检查历史数据足够
        hist = df[(df['symbol'] == sym) & (df['trade_date'] <= date)].tail(lookback)
        if len(hist) < lookback: continue

        rsi_w = row.get('weekly_rsi', 50.0)
        rsi_d = row.get('rsi_14', 50.0)
        macd_h = row.get('macd_hist', 0.0)
        ma20r = row.get('ma20_ratio', 1.0)
        ma60r = row.get('ma60_ratio', 1.0)
        resonance = row.get('resonance', 0.0)
        close = row['close']

        if np.isnan(rsi_w): rsi_w = 50.0
        if np.isnan(rsi_d): rsi_d = 50.0
        if np.isnan(macd_h): macd_h = 0.0
        if np.isnan(ma20r): ma20r = 1.0
        if np.isnan(resonance): resonance = 0.0

        # ── 融合模型信号 ──
        # 因子打分：周RSI权重20.2%（历史验证）
        score = 0.202 * (50 - rsi_w) + (-0.152) * resonance
        trend_up = ma20r > 1.0 and ma60r > 1.0
        # 趋势中降低卖出敏感度
        if trend_up and rsi_w > 65:
            score = score * 0.5 if rsi_w < 75 else score
        elif not trend_up and rsi_w < 35:
            score = score * 0.5 if rsi_w > 25 else score

        fm_thr = 5
        if score >= fm_thr:
            fm_sig = 'BUY'
        elif score <= -fm_thr:
            fm_sig = 'SELL'
        else:
            fm_sig = 'HOLD'
        fm_conf = min(0.88, 0.5 + abs(score) / 50)

        # ── XMM系统信号 ──
        above20 = ma20r > 1.0
        above60 = ma60r > 1.0
        macd_pos = macd_h > 0

        if above20 and above60 and macd_pos and rsi_d < 70:
            xmm_sig = 'BUY'
        elif rsi_d >= 78 or (not above20 and rsi_d > 65):
            xmm_sig = 'SELL'
        else:
            xmm_sig = 'HOLD'
        xmm_conf = 0.75 if xmm_sig == 'BUY' else (0.72 if xmm_sig == 'SELL' else 0.5)

        # ── 融合 ──
        # 趋势自适应矩阵
        if trend_up:
            matrix = {
                ('BUY', 'BUY'): ('STRONG_BUY', 0.80),
                ('BUY', 'HOLD'): ('BUY', 0.60),
                ('BUY', 'SELL'): ('BUY', 0.60),
                ('HOLD', 'BUY'): ('BUY', 0.60),
                ('HOLD', 'HOLD'): ('HOLD', 0.0),
                ('HOLD', 'SELL'): ('HOLD', 0.0),
                ('SELL', 'BUY'): ('BUY', 0.60),   # 核心修复
                ('SELL', 'HOLD'): ('HOLD', 0.0),
                ('SELL', 'SELL'): ('SELL', 0.80),
            }
        else:
            matrix = {
                ('BUY', 'BUY'): ('STRONG_BUY', 0.80),
                ('BUY', 'HOLD'): ('BUY', 0.60),
                ('BUY', 'SELL'): ('REDUCED', 0.20),
                ('HOLD', 'BUY'): ('HOLD', 0.0),
                ('HOLD', 'HOLD'): ('HOLD', 0.0),
                ('HOLD', 'SELL'): ('SELL', 0.60),
                ('SELL', 'BUY'): ('REDUCED', 0.20),
                ('SELL', 'HELL'): ('SELL', 0.60),
                ('SELL', 'SELL'): ('STRONG_SELL', 0.80),
            }

        level_str, pos = matrix.get((fm_sig, xmm_sig), ('HOLD', 0.0))

        signals.append({
            'symbol': sym,
            'date': date,
            'close': close,
            'future_ret_5d': row.get('future_ret_5d', np.nan),
            'label_5d_up': row.get('label_5d_up', -1),
            'fm_sig': fm_sig, 'xmm_sig': xmm_sig,
            'score': score, 'rsi_w': rsi_w, 'rsi_d': rsi_d,
            'trend_up': trend_up,
            'level': level_str, 'position': pos,
        })
    return signals

# ─── 因子IC验证 ──────────────────────────────────────────────
print("\n" + "="*72)
print("因子IC验证（过去500天）")
print("="*72)

recent_dates = ALL_DATES[-500:]
ic_results = {}

for factor in ['weekly_rsi', 'rsi_14', 'resonance', 'macd_hist', 'ma20_ratio']:
    vals = df[df['trade_date'].isin(recent_dates)].dropna(subset=[factor, 'future_ret_5d'])
    if len(vals) > 100:
        ic, _ = spearmanr(vals[factor], vals['future_ret_5d'], nan_policy='omit')
        if not np.isnan(ic):
            stars = '★' * min(5, max(1, int(abs(ic) * 20)))
            sign = '+' if ic > 0 else ''
            print(f"  {factor:<18} IC={sign}{ic:.4f}{stars}  n={len(vals)}")
            ic_results[factor] = {'rank_ic': ic, 'n': len(vals)}
    else:
        print(f"  {factor:<18} 数据不足({len(vals)})")

# 融合打分IC（用全部信号数据）
print("\n融合信号IC验证（500天）...")
all_signals = []
for date in recent_dates:
    sigs = generate_signals_for_date_symbols(df, date, lookback=60)
    all_signals.extend(sigs)

sig_df_all = pd.DataFrame(all_signals)
sig_df = sig_df_all[sig_df_all['position'] > 0].dropna(subset=['future_ret_5d'])
print(f"有效信号: {len(sig_df)}条")

# 融合打分IC
if len(sig_df) > 100:
    ic, _ = spearmanr(sig_df['score'], sig_df['future_ret_5d'], nan_policy='omit')
    win = (sig_df['future_ret_5d'] > 0).mean() * 100
    if not np.isnan(ic):
        print(f"  {'score':<15} IC={ic:+.4f}  胜率={win:.1f}%  n={len(sig_df)}")

# 信号层级IC
for level in ['BUY', 'STRONG_BUY', 'SELL', 'STRONG_SELL']:
    sub = sig_df[sig_df['level'] == level]
    if len(sub) > 20:
        ic2, _ = spearmanr(sub['position'], sub['future_ret_5d'], nan_policy='omit')
        win2 = (sub['future_ret_5d'] > 0).mean() * 100
        if not np.isnan(ic2):
            print(f"  {level:<15} IC={ic2:+.4f}  胜率={win2:.1f}%  n={len(sub)}")

# ─── 回测 ────────────────────────────────────────────────────
print("\n" + "="*72)
print("融合框架回测（过去500天）")
print("="*72)

# 回测：每日等权持有BUY/STRONG_BUY信号股票
backtest_dates = recent_dates  # 500天

portfolio_returns = []  # 每日组合收益
benchmark_returns = []  # 每日基准收益
daily_details = []

for date in backtest_dates:
    sigs = generate_signals_for_date_symbols(df, date, lookback=60)
    sig_df_t = pd.DataFrame(sigs)
    if len(sig_df_t) == 0: continue

    # 融合框架：持有BUY + STRONG_BUY，等权
    buy_df = sig_df_t[sig_df_t['level'].isin(['BUY', 'STRONG_BUY'])]
    if len(buy_df) > 0:
        port_ret = buy_df['future_ret_5d'].mean() / 5  # 日均收益
        port_n = len(buy_df)
    else:
        port_ret = 0.0
        port_n = 0

    # 基准：等权持有所有股票
    bench_ret = sig_df_t['future_ret_5d'].mean() / 5 if len(sig_df_t) > 0 else 0.0
    benchmark_returns.append(bench_ret)
    portfolio_returns.append(port_ret)

    # 月度统计
    monthly_win = buy_df['future_ret_5d'].mean() if len(buy_df) > 0 else 0
    daily_details.append({
        'date': date, 'port_ret': port_ret, 'bench_ret': bench_ret,
        'n_hold': port_n, 'total': len(sig_df_t),
        'monthly_win': monthly_win,
        'fm_buy': (sig_df_t['fm_sig'] == 'BUY').sum(),
        'xmm_buy': (sig_df_t['xmm_sig'] == 'BUY').sum(),
    })

# 计算指标
port_rets = np.array(portfolio_returns)
bench_rets = np.array(benchmark_returns)
port_rets = port_rets[np.isfinite(port_rets)]
bench_rets = bench_rets[np.isfinite(bench_rets)]

# 累积收益
port_cum = (1 + port_rets).cumprod()
bench_cum = (1 + bench_rets).cumprod()

port_total = (port_cum[-1] - 1) * 100 if len(port_cum) > 0 else 0
bench_total = (bench_cum[-1] - 1) * 100 if len(bench_cum) > 0 else 0

# 夏普
def sharpe(r):
    r = r[np.isfinite(r)]
    return (np.mean(r) / (np.std(r) + 1e-10)) * np.sqrt(252) if len(r) > 1 else 0.0

# 最大回撤
def max_dd(cum):
    peak = np.maximum.accumulate(cum)
    dd = (cum - peak) / peak
    return np.min(dd) * 100

port_sh = sharpe(port_rets)
bench_sh = sharpe(bench_rets)
port_dd = max_dd(port_cum)
bench_dd = max_dd(bench_cum)

# 月度胜率
monthly = pd.DataFrame(daily_details)
monthly['month'] = pd.to_datetime(monthly['date']).dt.to_period('M')
monthly_grp = monthly.groupby('month').agg(
    port_ret=('port_ret', lambda x: (1+x).prod()-1),
    bench_ret=('bench_ret', lambda x: (1+x).prod()-1),
    n_days=('date', 'count')
)
monthly_grp = monthly_grp[monthly_grp['n_days'] >= 15]  # 至少15个交易日
monthly_win_rate = (monthly_grp['port_ret'] > monthly_grp['bench_ret']).mean() * 100
n_months = len(monthly_grp)

print(f"\n回测区间: {backtest_dates[0].date()} ~ {backtest_dates[-1].date()} ({len(backtest_dates)}天, {n_months}个月)")
print(f"\n{'策略':<18} {'总收益':>10} {'夏普':>8} {'最大回撤':>10} {'月度胜率':>10} {'持仓股票':>10}")
print("-" * 70)
print(f"{'融合框架':<18} {port_total:>+9.2f}% {port_sh:>8.2f} {port_dd:>+9.2f}% {monthly_win_rate:>9.0f}% {int(monthly['n_hold'].mean()):>10.0f}")
print(f"{'等权持有(基准)':<18} {bench_total:>+9.2f}% {bench_sh:>8.2f} {bench_dd:>+9.2f}% {'--':>10} {int(monthly['total'].mean()):>10.0f}")
alpha = port_total - bench_total
print(f"\nAlpha: {alpha:>+.2f}%")

# 平均持仓
print(f"\n平均持仓: {monthly['n_hold'].mean():.1f}只股票")
print(f"FM BUY平均: {monthly['fm_buy'].mean():.1f}只/天")
print(f"XMM BUY平均: {monthly['xmm_buy'].mean():.1f}只/天")

# 展示月度收益
print(f"\n月度收益:")
print(f"{'月份':<10} {'组合':>10} {'基准':>10} {'超额':>10} {'持仓数':>8}")
for _, row in monthly_grp.tail(12).iterrows():
    m = str(row.name)
    pa = row['port_ret'] * 100
    ba = row['bench_ret'] * 100
    al = pa - ba
    print(f"{m:<10} {pa:>+9.2f}% {ba:>+9.2f}% {al:>+9.2f}% {int(row['n_days']):>8}天")
