# sonnet_backtest.py
# 徐小明策略回测对比：v4.4单周期 vs Sonnet三周期

import sys, io, json, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, 'E:/quant/xmm-strategy')

import pandas as pd
import numpy as np
from xmm_model import XMMModel
from xmm_sonnet_model import XMMSonnetModel

# ══════════════════════════════════════════════════════
# 数据加载
# ══════════════════════════════════════════════════════

def load_spy():
    with open('E:/quant/scanner/cache/SPY_US_1y.json', 'r') as f:
        raw = json.load(f)
    df = pd.DataFrame(raw)
    df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
    df = df.sort_values('datetime').reset_index(drop=True).set_index('datetime')
    for c in ['open','high','low','close','volume']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    return df.dropna()


# ══════════════════════════════════════════════════════
# v4.4 回测（简化版，只统计信号）
# ══════════════════════════════════════════════════════

def backtest_v44(df, model, capital=100000):
    """v4.4单周期回测"""
    warmup = model.long_period + 5
    n_days = len(df) - warmup - 1

    pc = model.precompute(df)
    close_arr = df['close'].values
    open_arr  = df['open'].values

    # 预计算信号
    signals = []
    for i in range(warmup, len(df) - 1):
        sig = pc.get(i)
        sig['date']   = df.index[i].strftime('%Y-%m-%d')
        sig['close']  = float(close_arr[i])
        sig['next_open'] = float(open_arr[i+1])
        signals.append(sig)

    # 回测
    cash, shares, avg_cost = capital, 0, 0.0
    peak = capital
    trades, equity_curve = [], []

    for sig in signals:
        equity = cash + shares * sig['close']
        peak = max(peak, equity)
        dd = (peak - equity) / peak
        equity_curve.append({'date': sig['date'], 'equity': equity, 'dd': dd,
                            'signal': sig['signal'], 'close': sig['close']})

        if sig['signal'] == 'BUY' and shares == 0:
            n = int(cash / sig['next_open'] * 0.95)
            if n > 0:
                cash -= n * sig['next_open']
                shares, avg_cost = n, sig['next_open']
                trades.append({'date': sig['date'], 'action': 'BUY',
                               'price': sig['next_open'], 'shares': n,
                               'net': sig['net_score']})

        elif sig['signal'] == 'SELL' and shares > 0:
            pnl = shares * sig['next_open'] - shares * avg_cost
            pct = pnl / (shares * avg_cost)
            cash += shares * sig['next_open']
            trades.append({'date': sig['date'], 'action': 'SELL',
                           'price': sig['next_open'], 'shares': shares,
                           'pnl': pnl, 'ret': pct*100})
            shares, avg_cost = 0, 0.0

    # 统计
    final = cash + shares * float(close_arr[-1])
    total_ret = (final - capital) / capital
    bench_ret = float(close_arr[-1]) / float(close_arr[warmup]) - 1

    sells = [t for t in trades if t['action']=='SELL']
    wins  = [t for t in sells if t.get('ret',0) > 0]
    wr = len(wins)/len(sells)*100 if sells else 0

    rets = [(equity_curve[i]['equity']-equity_curve[i-1]['equity'])/max(equity_curve[i-1]['equity'],1)
            for i in range(1,len(equity_curve))]
    sharpe = np.mean(rets)/np.std(rets)*np.sqrt(252) if rets and np.std(rets)>0 else 0
    max_dd = max(e['dd'] for e in equity_curve)*100

    return {
        'name': 'v4.4单周期',
        'total_return': total_ret*100,
        'benchmark': bench_ret*100,
        'alpha': (total_ret-bench_ret)*100,
        'sharpe': sharpe,
        'max_dd': max_dd,
        'trades': len(trades),
        'win_rate': wr,
        'final': final,
    }


# ══════════════════════════════════════════════════════
# Sonnet 三周期回测
# ══════════════════════════════════════════════════════

def backtest_sonnet(df, model, capital=100000):
    """Sonnet三周期回测"""
    warmup = 95  # 月线需要更多预热
    if len(df) < warmup + 5:
        return None

    n_days = len(df) - warmup - 1
    print(f'\n  Sonnet预热{warmup}天 | 回测{n_days}天')

    pc = model.precompute(df, verbose=False)
    close_arr = df['close'].values
    open_arr  = df['open'].values

    # 预计算信号
    signals = []
    for i in range(warmup, len(df) - 1):
        sig = pc.get(i)
        sig['date'] = df.index[i].strftime('%Y-%m-%d')
        sig['close'] = float(close_arr[i])
        sig['next_open'] = float(open_arr[i+1])
        signals.append(sig)

    # 回测
    cash, shares, avg_cost = capital, 0, 0.0
    peak, pos_limit = capital, 0.0
    trades, equity_curve = [], []

    for sig in signals:
        equity = cash + shares * sig['close']
        peak = max(peak, equity)
        dd = (peak - equity) / peak
        equity_curve.append({'date': sig['date'], 'equity': equity, 'dd': dd,
                            'signal': sig['signal'],
                            'pos_limit': sig['position_limit'],
                            'close': sig['close']})

        signal = sig['signal']
        pl     = sig['position_limit']

        # EXIT → 清仓
        if signal == 'EXIT' and shares > 0:
            cash += shares * sig['next_open']
            pnl = shares * sig['next_open'] - shares * avg_cost
            trades.append({'date': sig['date'], 'action': 'EXIT',
                           'price': sig['next_open'], 'shares': shares,
                           'ret': pnl/(shares*avg_cost)*100 if avg_cost else 0,
                           'reason': sig['layer1'] + '|' + sig['layer2']})
            shares, avg_cost, pos_limit = 0, 0.0, 0.0

        # REDUCE → 减半
        elif signal == 'REDUCE' and shares > 0:
            half = shares // 2
            if half > 0:
                cash += half * sig['next_open']
                shares -= half
                trades.append({'date': sig['date'], 'action': 'REDUCE',
                               'price': sig['next_open'], 'shares': half,
                               'reason': sig['layer2']})

        # BUY/STRONG_BUY → 按仓位上限建仓
        elif signal in ('BUY','STRONG_BUY') and shares == 0 and pl > 0:
            target_value = capital * pl
            n = int(target_value / sig['next_open'])
            if n > 0:
                cost = n * sig['next_open']
                if cost <= cash:
                    cash -= cost
                    shares, avg_cost = n, sig['next_open']
                    pos_limit = pl
                    trades.append({'date': sig['date'], 'action': signal,
                                   'price': sig['next_open'], 'shares': n,
                                   'pos_limit': pl,
                                   'reason': sig['layer2'] + '|' + sig['layer3']})

        # 加仓逻辑（已有仓位 + 新信号更强）
        elif signal in ('BUY','STRONG_BUY') and shares > 0 and pl > pos_limit:
            add_value = capital * (pl - pos_limit)
            n = int(add_value / sig['next_open'])
            if n > 0:
                cost = n * sig['next_open']
                if cost <= cash:
                    cash -= cost
                    shares += n
                    avg_cost = (avg_cost * (shares-n) + sig['next_open'] * n) / shares
                    pos_limit = pl
                    trades.append({'date': sig['date'], 'action': 'ADD',
                                   'price': sig['next_open'], 'shares': n,
                                   'pos_limit': pl, 'reason': '加仓'})

    # 最终平仓统计
    final = cash + shares * float(close_arr[-1])
    total_ret = (final - capital) / capital
    bench_ret = float(close_arr[-1]) / float(close_arr[warmup]) - 1

    sells = [t for t in trades if t['action'] in ('EXIT','REDUCE')]
    wins  = [t for t in sells if t.get('ret',0) > 0]
    wr = len(wins)/len(sells)*100 if sells else 0

    rets = [(equity_curve[i]['equity']-equity_curve[i-1]['equity'])/max(equity_curve[i-1]['equity'],1)
            for i in range(1,len(equity_curve))]
    sharpe = np.mean(rets)/np.std(rets)*np.sqrt(252) if rets and np.std(rets)>0 else 0
    max_dd = max(e['dd'] for e in equity_curve)*100

    return {
        'name': 'Sonnet三周期',
        'total_return': total_ret*100,
        'benchmark': bench_ret*100,
        'alpha': (total_ret-bench_ret)*100,
        'sharpe': sharpe,
        'max_dd': max_dd,
        'trades': len(trades),
        'win_rate': wr,
        'final': final,
        'signal_dist': {
            'BUY': sum(1 for s in signals if s['signal']=='BUY'),
            'STRONG_BUY': sum(1 for s in signals if s['signal']=='STRONG_BUY'),
            'HOLD': sum(1 for s in signals if s['signal']=='HOLD'),
            'EXIT': sum(1 for s in signals if s['signal']=='EXIT'),
            'REDUCE': sum(1 for s in signals if s['signal']=='REDUCE'),
        },
        'trades_detail': trades,
    }


# ══════════════════════════════════════════════════════
# 主程序
# ══════════════════════════════════════════════════════

def main():
    print('=' * 80)
    print('徐小明策略回测对比：v4.4单周期 vs Sonnet三周期')
    print('=' * 80)

    df = load_spy()
    print(f'\n数据: {len(df)}条  {df.index[0].date()} ~ {df.index[-1].date()}')
    print(f'收盘: ${df["close"].iloc[0]:.2f} → ${df["close"].iloc[-1]:.2f}')

    # v4.4
    print('\n' + '─' * 40)
    print('▶ v4.4 单周期回测')
    model44 = XMMModel(name='v4.4', threshold=2.0)
    r44 = backtest_v44(df, model44)
    print(f'  收益: {r44["total_return"]:+.2f}%  基准: {r44["benchmark"]:+.2f}%  α: {r44["alpha"]:+.2f}%')
    print(f'  夏普: {r44["sharpe"]:.2f}  回撤: {r44["max_dd"]:.2f}%')
    print(f'  交易: {r44["trades"]}笔  胜率: {r44["win_rate"]:.1f}%')

    # Sonnet
    print('\n' + '─' * 40)
    print('▶ Sonnet 三周期回测')
    model_sn = XMMSonnetModel()
    rsn = backtest_sonnet(df, model_sn)

    if rsn:
        print(f'  收益: {rsn["total_return"]:+.2f}%  基准: {rsn["benchmark"]:+.2f}%  α: {rsn["alpha"]:+.2f}%')
        print(f'  夏普: {rsn["sharpe"]:.2f}  回撤: {rsn["max_dd"]:.2f}%')
        print(f'  交易: {rsn["trades"]}笔  胜率: {rsn["win_rate"]:.1f}%')
        print(f'  信号分布: {rsn["signal_dist"]}')

        # 交易明细
        if rsn['trades_detail']:
            print('\n  📋 交易明细:')
            for t in rsn['trades_detail'][:15]:
                pnl = f'({t.get("ret",0):+.2f}%)' if 'ret' in t else ''
                print(f'    {t["date"]} {t["action"]:<6} {t["price"]:>8.2f} x{t["shares"]:<4} {pnl}')

        # 对比表
        print('\n' + '=' * 80)
        print('📊 对比总结')
        print('=' * 80)
        print(f'{"指标":<15} {"v4.4单周期":>15} {"Sonnet三周期":>15} {"差异":>12}')
        print('─' * 60)
        print(f'{"收益":<15} {r44["total_return"]:>+14.2f}% {rsn["total_return"]:>+14.2f}% {rsn["total_return"]-r44["total_return"]:>+11.2f}%')
        print(f'{"超额α":<15} {r44["alpha"]:>+14.2f}% {rsn["alpha"]:>+14.2f}% {rsn["alpha"]-r44["alpha"]:>+11.2f}%')
        print(f'{"夏普":<15} {r44["sharpe"]:>14.2f}  {rsn["sharpe"]:>14.2f}  {rsn["sharpe"]-r44["sharpe"]:>+11.2f}')
        print(f'{"最大回撤":<15} {r44["max_dd"]:>14.2f}% {rsn["max_dd"]:>14.2f}% {rsn["max_dd"]-r44["max_dd"]:>+11.2f}%')
        print(f'{"交易次数":<15} {r44["trades"]:>14}   {rsn["trades"]:>14}   {rsn["trades"]-r44["trades"]:>+11}')
        print(f'{"胜率":<15} {r44["win_rate"]:>13.1f}% {rsn["win_rate"]:>13.1f}% {rsn["win_rate"]-r44["win_rate"]:>+10.1f}%')

    # 保存结果
    out = {'v44': r44, 'sonnet': rsn}
    os.makedirs('E:/quant/output', exist_ok=True)
    with open('E:/quant/output/sonnet_vs_v44.json','w',encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f'\n💾 结果已保存: E:/quant/output/sonnet_vs_v44.json')


if __name__ == '__main__':
    main()
