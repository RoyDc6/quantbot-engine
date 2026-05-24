# -*- coding: utf-8 -*-
"""Sonnet 三周期回测 - 使用完整10年月线数据"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import json, pandas as pd
import numpy as np
sys.path.insert(0, r'E:\quant\xmm-strategy')
from xmm_sonnet_model import XMMSonnetPrecomputed, TREND_LONG, TD_COMPLETE, TD_REF

CACHE = r'E:\quant\scanner\cache'

# 加载日线（周/月由内部重采样）
with open(f'{CACHE}/SPY_daily_2500.json', 'r', encoding='utf-8') as f:
    df_daily = pd.DataFrame(json.load(f))
df_daily['trade_date'] = pd.to_datetime(df_daily['trade_date'])
df_daily.set_index('trade_date', inplace=True)
df_daily.sort_index(inplace=True)
print(f"日线: {len(df_daily)}条, {df_daily.index[0].date()} ~ {df_daily.index[-1].date()}")

# 预计算 Sonnet
pc = XMMSonnetPrecomputed(df_daily, verbose=True)

WARMUP = max(TREND_LONG, TD_COMPLETE + TD_REF)
print(f"\n预热期: {WARMUP}天, 实际可用: {len(df_daily)-WARMUP}天")

# 简易回测
cash = 100000.0
shares = 0.0
entry_price = 0.0
trades = []
portfolio = []
in_pos = False

for i in range(WARMUP, len(df_daily)):
    sig = pc.get(i)
    price = float(df_daily['close'].iloc[i])
    date = df_daily.index[i].date()

    pos_value = shares * price
    total = cash + pos_value
    portfolio.append({'date': date, 'value': total, 'signal': sig['signal'], 'limit': sig['position_limit']})

    action = sig['signal']
    limit = sig['position_limit']

    if action == 'BUY' and not in_pos and limit >= 0.05:
        budget = total * limit
        buy_shares = int(budget / price / 100) * 100
        if buy_shares >= 100:
            cost = buy_shares * price
            cash -= cost
            shares += buy_shares
            entry_price = price
            in_pos = True
            trades.append({'date': date, 'action': 'BUY', 'price': price, 'shares': buy_shares})
        else:
            buy_shares = max(int(budget / price), 1)
            cost = buy_shares * price
            cash -= cost
            shares += buy_shares
            entry_price = price
            in_pos = True
            trades.append({'date': date, 'action': 'BUY', 'price': price, 'shares': buy_shares})

    elif action == 'EXIT' and in_pos:
        proceeds = shares * price
        pnl_pct = (price - entry_price) / entry_price * 100
        cash += proceeds
        trades.append({'date': date, 'action': 'SELL', 'price': price, 'shares': shares, 'pnl_pct': pnl_pct})
        shares = 0.0
        in_pos = False

# 最终平仓
if in_pos:
    price = float(df_daily['close'].iloc[-1])
    date = df_daily.index[-1].date()
    pnl_pct = (price - entry_price) / entry_price * 100
    proceeds = shares * price
    cash += proceeds
    trades.append({'date': date, 'action': 'SELL', 'price': price, 'shares': shares, 'pnl_pct': pnl_pct})
    shares = 0.0

final = cash
ret = (final - 100000) / 100000 * 100
max_dd = 0.0
peak = 100000.0
for p in portfolio:
    if p['value'] > peak: peak = p['value']
    dd = (peak - p['value']) / peak * 100
    if dd > max_dd: max_dd = dd

# 基准（买入持有）
entry = float(df_daily['close'].iloc[WARMUP])
exit_p = float(df_daily['close'].iloc[-1])
benchmark = (exit_p - entry) / entry * 100

print(f'\n=== Sonnet 三周期回测结果 (10年月线) ===')
print(f'初始: 10万  最终: {final:.0f}  收益: {ret:.2f}%')
print(f'基准: {entry:.2f}→{exit_p:.2f} = {benchmark:.2f}%')
print(f'超额α: {ret-benchmark:.2f}%')
print(f'最大回撤: -{max_dd:.2f}%')
print(f'交易次数: {len(trades)}')
for t in trades:
    if 'pnl_pct' in t:
        print(f"  {t['date']} {t['action']} {t['shares']}股@{t['price']:.2f}  浮亏:{t['pnl_pct']:+.2f}%")
    else:
        print(f"  {t['date']} {t['action']} {t['shares']}股@{t['price']:.2f}")