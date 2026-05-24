# -*- coding: utf-8 -*-
"""Sonnet 三周期回测 — Futu港股数据"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import pandas as pd
import numpy as np
sys.path.insert(0, r'E:\quant\xmm-strategy')
from xmm_sonnet_model import XMMSonnetPrecomputed, TREND_LONG, TD_COMPLETE, TD_REF

# 从 Futu 获取数据
print("连接 Futu OpenD 获取港股数据...")
from futu import OpenQuoteContext

conn = OpenQuoteContext(host='127.0.0.1', port=11111)

STOCKS = [
    ('HK.00700', '腾讯'),
    ('HK.09988', '阿里'),
]

def fetch_and_backtest(code, name):
    print(f"\n{'='*60}")
    print(f"=== {name} ({code}) ===")
    print(f"{'='*60}")
    
    ret, data, _ = conn.request_history_kline(
        code=code,
        start='2000-01-01',
        end='2026-04-23',
        ktype='K_DAY',
        autype='qfq',  # 前复权
        max_count=10000
    )
    
    if ret != 0:
        print(f"获取数据失败: {ret} - {data}")
        return None
    
    # 转换为标准格式
    df = data.rename(columns={
        'time_key': 'datetime',
        'open': 'open',
        'high': 'high',
        'low': 'low',
        'close': 'close',
        'volume': 'volume'
    })
    df['datetime'] = pd.to_datetime(df['datetime'])
    df = df.set_index('datetime').sort_index()
    
    print(f"数据: {len(df)}条, {df.index[0].date()} ~ {df.index[-1].date()}")
    
    # 预计算
    try:
        pc = XMMSonnetPrecomputed(df, verbose=False)
    except ValueError as e:
        print(f"预计算失败: {e}")
        return None
    
    WARMUP = max(TREND_LONG, TD_COMPLETE + TD_REF)
    print(f"预热期: {WARMUP}天, 实际回测: {len(df)-WARMUP}天")
    
    # 信号统计
    sig_counts = {}
    monthly_status = {}
    trend_signals = []
    bottom_signals = []
    limits = []
    
    for i in range(WARMUP, len(df)):
        sig = pc.get(i)
        s = sig['signal']
        sig_counts[s] = sig_counts.get(s, 0) + 1
        m = sig['monthly_trend']
        monthly_status[m] = monthly_status.get(m, 0) + 1
        limits.append(sig['position_limit'])
        
        if s in ('BUY', 'STRONG_BUY'):
            if '趋势跟随' in sig['layer2']:
                trend_signals.append({'date': str(df.index[i].date()), 
                                      'limit': sig['position_limit']})
            elif '底部' in sig['layer2']:
                bottom_signals.append({'date': str(df.index[i].date()), 
                                       'limit': sig['position_limit']})
    
    print(f"\n信号分布: {sig_counts}")
    print(f"月线状态: {monthly_status}")
    print(f"position_limit: mean={np.mean(limits):.3f}, max={max(limits):.3f}")
    print(f"趋势跟随信号: {len(trend_signals)}次, 底部布局: {len(bottom_signals)}次")
    
    # 简易回测
    cash = 100000.0
    shares = 0.0
    entry_price = 0.0
    trades = []
    portfolio = []
    in_pos = False
    
    for i in range(WARMUP, len(df)):
        sig = pc.get(i)
        price = float(df['close'].iloc[i])
        date = df.index[i].date()
        
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
                               'shares': buy_shares, 'limit': limit, 'reason': sig['layer2']})
        
        elif action in ('EXIT',) and in_pos:
            proceeds = shares * price
            pnl_pct = (price - entry_price) / entry_price * 100
            cash += proceeds
            trades.append({'date': str(date), 'action': 'SELL', 'price': price,
                           'shares': shares, 'pnl_pct': round(pnl_pct, 2), 'reason': sig['layer2']})
            shares = 0.0
            in_pos = False
    
    if in_pos and shares > 0:
        price = float(df['close'].iloc[-1])
        date = df.index[-1].date()
        pnl_pct = (price - entry_price) / entry_price * 100
        proceeds = shares * price
        cash += proceeds
        trades.append({'date': str(date), 'action': 'SELL', 'price': price,
                       'shares': shares, 'pnl_pct': round(pnl_pct, 2), 'reason': '期末平仓'})
    
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
    
    entry = float(df['close'].iloc[WARMUP])
    exit_p = float(df['close'].iloc[-1])
    benchmark = (exit_p - entry) / entry * 100
    
    # 夏普
    vals = [p['value'] for p in portfolio]
    if len(vals) > 1:
        daily_rets = np.diff(vals) / vals[:-1]
        sharpe = np.mean(daily_rets) / np.std(daily_rets) * np.sqrt(252) if np.std(daily_rets) > 0 else 0
    else:
        sharpe = 0
    
    print(f"\n{'='*40}")
    print(f"回测结果:")
    print(f"  策略收益: {ret:+.2f}%")
    print(f"  基准收益: {benchmark:+.2f}%")
    print(f"  超额: {ret - benchmark:+.2f}%")
    print(f"  夏普比率: {sharpe:.2f}")
    print(f"  最大回撤: -{max_dd:.2f}%")
    print(f"  交易次数: {len([t for t in trades if t['action']=='BUY'])}")
    
    if len(trades) > 0:
        print(f"\n  交易明细:")
        for t in trades:
            if 'pnl_pct' in t:
                print(f"    {t['date']} {t['action']} {t['shares']}股 @{t['price']:.2f} PnL:{t['pnl_pct']:+.1f}% | {t['reason'][:30]}")
            else:
                print(f"    {t['date']} {t['action']} {t['shares']}股 @{t['price']:.2f} | {t['reason'][:30]}")
    
    return {
        'name': name,
        'return': round(ret, 2),
        'benchmark': round(benchmark, 2),
        'alpha': round(ret - benchmark, 2),
        'max_dd': round(max_dd, 2),
        'sharpe': round(sharpe, 2),
        'trades': len([t for t in trades if t['action']=='BUY']),
    }

# 运行测试
results = []
for code, name in STOCKS:
    r = fetch_and_backtest(code, name)
    if r:
        results.append(r)

conn.close()

print(f"\n{'='*60}")
print(f"=== 港股回测汇总 ===")
print(f"{'='*60}")
print(f"{'股票':<10} {'策略收益':>10} {'基准':>10} {'超额':>10} {'夏普':>8} {'回撤':>8} {'交易':>6}")
for r in results:
    print(f"{r['name']:<10} {r['return']:>+10.1f}% {r['benchmark']:>+10.1f}% {r['alpha']:>+10.1f}% {r['sharpe']:>8.2f} {r['max_dd']:>8.1f}% {r['trades']:>6}")
