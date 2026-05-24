# -*- coding: utf-8 -*-
"""徐小明策略两年回测 - 使用XMMPrecomputed预计算器"""
import sys, io, os, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, 'E:/quant/xmm-strategy')

import pandas as pd
import numpy as np
from xmm_model import XMMModel, XMMPrecomputed


def load_data():
    with open('E:/quant/scanner/cache/SPY_US_1y.json', 'r') as f:
        raw = json.load(f)
    df = pd.DataFrame(raw)
    df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
    df = df.sort_values('datetime').reset_index(drop=True).set_index('datetime')
    for c in ['open','high','low','close','volume']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.dropna()
    return df


def run_backtest(df, model, capital=100000.0):
    warmup = model.long_period + 5
    n_days = len(df) - warmup - 1
    print(f'\n{"="*115}')
    print(f'预热{warmup}天 | 回测: {df.index[warmup].strftime("%Y-%m-%d")} ~ {df.index[-1].strftime("%Y-%m-%d")} ({n_days}天)')
    print(f'{"="*115}')

    # 预计算全量指标
    print('📊 预计算指标（全量一次性）...')
    pc = model.precompute(df)
    close_arr = df['close'].values
    open_arr  = df['open'].values

    # 预计算信号
    signals = []
    for i in range(warmup, len(df) - 1):
        sig = pc.get(i)
        sig['date'] = df.index[i].strftime('%Y-%m-%d')
        sig['close'] = float(close_arr[i])
        sig['next_open'] = float(open_arr[i+1])
        sig['equity'] = 0  # 后续填充
        signals.append(sig)

    buys = sum(1 for s in signals if s['signal']=='BUY')
    sells = sum(1 for s in signals if s['signal']=='SELL')
    print(f'信号统计: BUY={buys} SELL={sells} HOLD={n_days-buys-sells}')

    # 逐日回测
    cash, shares, avg_cost = capital, 0, 0.0
    peak, equity_curve, trades = capital, [], []
    mkt_counts = {'UP':0,'DOWN':0,'SIDEWAYS':0}

    for sig in signals:
        mkt_counts[sig['market']] = mkt_counts.get(sig['market'],0) + 1
        equity = cash + shares * sig['close']
        peak = max(peak, equity)
        dd = (peak - equity) / peak
        equity_curve.append({'date': sig['date'], 'equity': equity, 'dd': dd,
                            'signal': sig['signal'], 'net': sig['net_score'], 'close': sig['close']})

        if sig['signal'] == 'BUY' and shares == 0:
            n = int(cash / sig['next_open'] * 0.95)
            if n > 0:
                cash -= n * sig['next_open']
                shares, avg_cost = n, sig['next_open']
                trades.append({'date': sig['date'], 'action': 'BUY', 'price': sig['next_open'],
                               'shares': n, 'net': sig['net_score'], 'bull': sig['bull_score'],
                               'bear': sig['bear_score'], 'market': sig['market'],
                               'td': sig['td_count'], 'signal_type': sig['signal_type'], 'reason': sig['reason']})

        elif sig['signal'] == 'SELL' and shares > 0:
            proceeds = shares * sig['next_open']
            pnl = proceeds - shares * avg_cost
            pct = pnl / (shares * avg_cost)
            cash += proceeds
            trades.append({'date': sig['date'], 'action': 'SELL', 'price': sig['next_open'],
                           'shares': shares, 'pnl': round(pnl,2), 'ret': round(pct*100,2),
                           'net': sig['net_score'], 'bull': sig['bull_score'],
                           'bear': sig['bear_score'], 'market': sig['market'],
                           'td': sig['td_count'], 'signal_type': sig['signal_type'], 'reason': sig['reason']})
            shares, avg_cost = 0, 0.0

    final = cash + shares * float(close_arr[-1])
    total_ret = (final - capital) / capital
    bench_ret = float(close_arr[-1]) / float(close_arr[warmup]) - 1
    s_t = [t for t in trades if t['action']=='SELL']
    wins = [t for t in s_t if t['ret'] > 0]
    wr = len(wins)/len(s_t)*100 if s_t else 0
    avg_r = np.mean([t['ret'] for t in s_t]) if s_t else 0
    avg_w = np.mean([t['ret'] for t in wins]) if wins else 0
    avg_l = np.mean([t['ret'] for t in s_t if t['ret']<=0]) if s_t else 0
    rets = [(equity_curve[i]['equity']-equity_curve[i-1]['equity'])/max(equity_curve[i-1]['equity'],1)
            for i in range(1,len(equity_curve))]
    sharpe = np.mean(rets)/np.std(rets)*np.sqrt(252) if rets and np.std(rets)>0 else 0
    max_dd = max(e['dd'] for e in equity_curve)*100

    print(f'\n{"="*100}')
    print(f'📊 回测结果')
    print(f'{"="*100}')
    print(f'  策略: {total_ret*100:+.2f}%  基准: {bench_ret*100:+.2f}%  超额α: {(total_ret-bench_ret)*100:+.2f}%')
    print(f'  夏普: {sharpe:.2f}  最大回撤: {max_dd:.2f}%')
    print(f'  交易: {len(trades)}笔 ({len(wins)}胜/{len(s_t)}卖, 胜率{wr:.1f}%)')
    print(f'  均收益: {avg_r:+.2f}%  均胜: {avg_w:+.2f}%  均亏: {avg_l:+.2f}%')
    if avg_l != 0: print(f'  盈亏比: {abs(avg_w/avg_l):.2f}')
    print(f'  市场: UP={mkt_counts["UP"]} DOWN={mkt_counts["DOWN"]} SIDE={mkt_counts["SIDEWAYS"]}')

    if trades:
        print(f'\n{"="*115}')
        print(f'📋 交易明细 ({len(trades)}笔)')
        print(f'{"日期":<12}{"方向":<6}{"价格":>8}{"股数":>8}{"金额":>10}  {"净分":>5}  {"市场":<10}TD  {"类型":<25} 原因')
        print("-"*115)
        for t in trades:
            icon = '🟢BUY' if t['action']=='BUY' else '🔴SELL'
            pnl_s = f'({t.get("ret",0):+.2f}%)' if 'ret' in t else ''
            print(f'{t["date"]:<12}{icon:<6}{t["price"]:>8.3f}{t["shares"]:>8}{t["shares"]*t["price"]:>10.2f}  {t["net"]:>+5.1f}  {t["market"]:<10}{t["td"]:2}  {t["signal_type"]:<25} {t["reason"][:35]} {pnl_s}')

    key = [s for s in signals if s['signal'] != 'HOLD']
    if key:
        print(f'\n{"="*115}')
        print(f'📋 关键信号日 ({len(key)}次)')
        print(f'{"日期":<12}{"收盘":>8}{"市场":<10}{"TD":>3}  {"多头":>5}{"空头":>5}{"净分":>6}  {"信号":<8}  {"类型":<25}  原因')
        print("-"*115)
        for s in key:
            icon = {'BUY':'🟢BUY','SELL':'🔴SELL'}.get(s['signal'], s['signal'])
            print(f'{s["date"]:<12}{s["close"]:>8.3f}{s["market"]:<10}{s["td_count"]:>3}  {s["bull_score"]:>5.1f}{s["bear_score"]:>5.1f}{s["net_score"]:>+6.1f}  {icon:<8}  {s["signal_type"]:<25}  {s["reason"][:35]}')

    eq_df = pd.DataFrame(equity_curve)
    eq_df['date'] = pd.to_datetime(eq_df['date'])
    eq_df['month'] = eq_df['date'].dt.to_period('M')
    monthly = eq_df.groupby('month')['equity'].agg(['first','last'])
    monthly['ret'] = (monthly['last'] - monthly['first']) / monthly['first']
    print(f'\n{"="*55}')
    print(f'📅 月度收益 ({len(monthly)}个月)')
    wm = len(monthly[monthly['ret']>0])
    print(f'胜月率: {wm}/{len(monthly)} ({wm/len(monthly)*100:.0f}%)')
    for p, r in monthly.iterrows():
        flag = '📈' if r['ret'] > 0 else '📉'
        print(f'{str(p):<10}{r["first"]:>12,.0f}{r["last"]:>12,.0f}{flag}{r["ret"]*100:>+6.2f}%')

    out = {
        'symbol': 'SPY (SPX 500 ETF)',
        'period': f'{df.index[warmup].strftime("%Y-%m-%d")}~{df.index[-1].strftime("%Y-%m-%d")}',
        'days': n_days,
        'final_equity': round(final,2), 'total_return': round(total_ret*100,2),
        'benchmark_return': round(bench_ret*100,2), 'alpha': round((total_ret-bench_ret)*100,2),
        'sharpe': round(sharpe,2), 'max_drawdown': round(max_dd,2),
        'total_trades': len(trades), 'win_rate': round(wr,1),
        'avg_return': round(avg_r,2), 'avg_win': round(avg_w,2), 'avg_loss': round(avg_l,2),
        'model': model.to_json(), 'trades': trades,
    }
    os.makedirs('E:/quant/output', exist_ok=True)
    with open('E:/quant/output/xmm_backtest_result.json','w',encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f'\n💾 已保存: E:/quant/output/xmm_backtest_result.json')
    return out


def main():
    print('='*100)
    print('徐小明策略 · 两年回测 (2024-04 ~ 2026-04)')
    print('='*100)

    df = load_data()
    print(f'数据: {len(df)}条  收盘={float(df["close"].iloc[0]):.3f}→{float(df["close"].iloc[-1]):.3f}')

    model = XMMModel(name='徐小明三层信号_v4.4', short_period=25, long_period=90, threshold=2.0)
    print(f'\n策略: {model.name}')
    print(f'  趋势: EMA(H,L)25 + EMA(H,L)90')
    print(f'  结构: MACD(12,26,9) 底/顶背离+钝化+结构')
    print(f'  序列: TD计数 ×1.5放大')
    print(f'  阈值: |NET| >= {model.threshold}')
    run_backtest(df, model, 100000.0)


if __name__ == '__main__':
    main()
