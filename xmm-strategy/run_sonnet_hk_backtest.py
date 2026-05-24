# -*- coding: utf-8 -*-
"""Sonnet 三周期回测 — 港股测试"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import json, pandas as pd
import numpy as np
sys.path.insert(0, r'E:\quant\xmm-strategy')
from xmm_sonnet_model import XMMSonnetPrecomputed, TREND_LONG, TD_COMPLETE, TD_REF

CACHE = r'E:\quant\scanner\cache'

# 测试港股列表
STOCKS = [
    ('00700_HK.json', '腾讯'),
    ('09988_HK.json', '阿里'),
    ('03690_HK.json', '美团'),
    ('01810_HK.json', '小米'),
]

def run_backtest(json_file, name):
    print(f"\n{'='*60}")
    print(f"=== {name} ({json_file}) ===")
    print(f"{'='*60}")
    
    try:
        with open(f'{CACHE}/{json_file}', 'r', encoding='utf-8') as f:
            df_daily = pd.DataFrame(json.load(f))
    except Exception as e:
        print(f"加载失败: {e}")
        return None
    
    # 兼容不同数据源列名
    date_col = 'trade_date' if 'trade_date' in df_daily.columns else 'datetime'
    df_daily[date_col] = pd.to_datetime(df_daily[date_col])
    df_daily.set_index(date_col, inplace=True)
    df_daily.sort_index(inplace=True)
    
    for c in ['open', 'high', 'low', 'close', 'volume']:
        df_daily[c] = pd.to_numeric(df_daily[c], errors='coerce')
    df_daily = df_daily.dropna()
    
    print(f"日线: {len(df_daily)}条, {df_daily.index[0].date()} ~ {df_daily.index[-1].date()}")
    
    if len(df_daily) < 500:
        print(f"数据不足，跳过")
        return None
    
    try:
        pc = XMMSonnetPrecomputed(df_daily, verbose=False)
    except ValueError as e:
        print(f"预计算失败: {e}")
        return None
    
    WARMUP = max(TREND_LONG, TD_COMPLETE + TD_REF)
    print(f"预热期: {WARMUP}天, 实际回测: {len(df_daily)-WARMUP}天")
    
    # 信号统计
    sig_counts = {}
    monthly_status = {}
    trend_signals = []
    bottom_signals = []
    
    for i in range(WARMUP, len(df_daily)):
        sig = pc.get(i)
        s = sig['signal']
        sig_counts[s] = sig_counts.get(s, 0) + 1
        m = sig['monthly_trend']
        monthly_status[m] = monthly_status.get(m, 0) + 1
        
        if s in ('BUY', 'STRONG_BUY'):
            if '趋势跟随' in sig['layer2']:
                trend_signals.append({'date': str(df_daily.index[i].date()), 
                                      'limit': sig['position_limit'], 
                                      'layer2': sig['layer2']})
            elif '底部' in sig['layer2']:
                bottom_signals.append({'date': str(df_daily.index[i].date()), 
                                       'limit': sig['position_limit'], 
                                       'layer2': sig['layer2']})
    
    print(f"\n信号分布: {sig_counts}")
    print(f"月线状态: {monthly_status}")
    print(f"趋势跟随信号: {len(trend_signals)}次")
    if trend_signals:
        for s in trend_signals[:3]:
            print(f"  {s['date']} limit={s['limit']:.2f} {s['layer2']}")
    print(f"底部布局信号: {len(bottom_signals)}次")
    if bottom_signals:
        for s in bottom_signals[:3]:
            print(f"  {s['date']} limit={s['limit']:.2f} {s['layer2']}")
    
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
        portfolio.append({'date': str(date), 'value': total})
        
        action = sig['signal']
        limit = sig['position_limit']
        
        if action in ('BUY', 'STRONG_BUY') and not in_pos and limit >= 0.05:
            budget = total * limit
            buy_shares = int(budget / price)
            if buy_shares >= 1:
                cost = buy_shares * price
                cash -= cost
                shares += buy_shares
                entry_price = price
                in_pos = True
                trades.append({'date': str(date), 'action': 'BUY', 'price': price, 
                               'shares': buy_shares, 'limit': limit})
        
        elif action in ('EXIT',) and in_pos:
            proceeds = shares * price
            pnl_pct = (price - entry_price) / entry_price * 100
            cash += proceeds
            trades.append({'date': str(date), 'action': 'SELL', 'price': price,
                           'shares': shares, 'pnl_pct': round(pnl_pct, 2)})
            shares = 0.0
            in_pos = False
    
    if in_pos and shares > 0:
        price = float(df_daily['close'].iloc[-1])
        date = df_daily.index[-1].date()
        pnl_pct = (price - entry_price) / entry_price * 100
        proceeds = shares * price
        cash += proceeds
        trades.append({'date': str(date), 'action': 'SELL', 'price': price,
                       'shares': shares, 'pnl_pct': round(pnl_pct, 2)})
    
    final = cash
    ret = (final - 100000) / 100000 * 100
    
    max_dd = 0.0
    peak = 100000.0
    for p in portfolio:
        if p['value'] > peak:
            peak = p['value']
        dd = (peak - p['value']) / peak * 100
        if dd > max_dd:
            max_dd = dd
    
    entry = float(df_daily['close'].iloc[WARMUP])
    exit_p = float(df_daily['close'].iloc[-1])
    benchmark = (exit_p - entry) / entry * 100
    
    print(f"\n回测结果:")
    print(f"  策略收益: {ret:+.2f}%")
    print(f"  基准收益: {benchmark:+.2f}%")
    print(f"  超额: {ret - benchmark:+.2f}%")
    print(f"  最大回撤: -{max_dd:.2f}%")
    print(f"  交易次数: {len([t for t in trades if t['action']=='BUY'])}")
    
    if len(trades) > 0:
        print(f"\n  交易明细:")
        for t in trades[:5]:
            if 'pnl_pct' in t:
                print(f"    {t['date']} {t['action']} {t['shares']}股 @{t['price']:.2f} PnL:{t['pnl_pct']:+.1f}%")
            else:
                print(f"    {t['date']} {t['action']} {t['shares']}股 @{t['price']:.2f}")
    
    return {
        'name': name,
        'return': round(ret, 2),
        'benchmark': round(benchmark, 2),
        'alpha': round(ret - benchmark, 2),
        'max_dd': round(max_dd, 2),
        'trades': len([t for t in trades if t['action']=='BUY']),
        'trend_signals': len(trend_signals),
        'bottom_signals': len(bottom_signals),
    }

# 运行所有测试
results = []
for fn, name in STOCKS:
    r = run_backtest(fn, name)
    if r:
        results.append(r)

print(f"\n{'='*60}")
print(f"=== 港股回测汇总 ===")
print(f"{'='*60}")
print(f"{'股票':<10} {'策略收益':>10} {'基准收益':>10} {'超额':>10} {'回撤':>8} {'交易':>6} {'趋势':>6} {'底部':>6}")
for r in results:
    print(f"{r['name']:<10} {r['return']:>+10.1f}% {r['benchmark']:>+10.1f}% {r['alpha']:>+10.1f}% {r['max_dd']:>8.1f}% {r['trades']:>6} {r['trend_signals']:>6} {r['bottom_signals']:>6}")
