# -*- coding: utf-8 -*-
"""
Sonnet 三周期回?v3 ?多笔持仓 + 动态仓位管?
支持：加仓、减仓、部分平仓、冷却期仅限制全新建?

Author: AiGobot
Date: 2026-04-24
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import json, os
import pandas as pd
import numpy as np
sys.path.insert(0, r'E:\quant\xmm-strategy')
from xmm_sonnet_model import XMMSonnetModel, XMMSonnetPrecomputed, TREND_LONG, TD_COMPLETE, TD_REF

# ══?配置 ══?
INITIAL_CASH = 100000.0
COMMISSION_RATE = 0.001    # 0.1% 佣金
SLIPPAGE = 0.001           # 0.1% 滑点
COOLDOWN_DAYS = 10         # EXIT 后冷却天数（仅限全新建仓?
MIN_TRADE_VALUE = 500      # 最小交易金额（USD/HKD?
MIN_REBALANCE_DAYS = 3     # 最小调仓间隔（TRIM/ADD之间至少?天）
MIN_POSITION_CHANGE = 0.10 # 最小仓位变动才触发调仓?0%以上?

CACHE = r'E:\quant\scanner\cache'
OUTPUT_DIR = r'E:\quant\output'

# ══?数据加载 ══?
def load_spy_daily():
    """加载SPY 10年日线数?""
    fpath = os.path.join(CACHE, 'SPY_daily_2500.json')
    if not os.path.exists(fpath):
        raise FileNotFoundError(f'SPY数据不存? {fpath}')
    with open(fpath, 'r', encoding='utf-8') as f:
        raw = json.load(f)
    df = pd.DataFrame(raw)
    if 'trade_date' in df.columns:
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df.set_index('trade_date', inplace=True)
    elif 'timestamp' in df.columns:
        df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('datetime', inplace=True)
    df.sort_index(inplace=True)
    for c in ['open', 'high', 'low', 'close', 'volume']:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.dropna(subset=['close'])
    return df


def load_futu_stock(code, name, days_back=7300):
    """加载Futu港股日线数据"""
    from futu import OpenQuoteContext, KLType, AuType, RET_OK
    from datetime import datetime, timedelta
    conn = OpenQuoteContext(host='127.0.0.1', port=11111)
    ret, data, _ = conn.request_history_kline(
        code=code,
        start=(datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d'),
        end=datetime.now().strftime('%Y-%m-%d'),
        ktype=KLType.K_DAY,
        autype=AuType.QFQ,
        max_count=10000
    )
    conn.close()
    if ret != RET_OK:
        raise ValueError(f'Futu数据获取失败: {data}')
    df = data.rename(columns={'time_key': 'datetime'})
    df['datetime'] = pd.to_datetime(df['datetime'])
    df = df.set_index('datetime').sort_index()
    for c in ['open', 'high', 'low', 'close', 'volume']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.dropna(subset=['close'])
    return df


def load_tickflow_stock(tf_code, days=2500):
    """加载TickFlow港股数据"""
    sys.path.insert(0, r'C:\Users\RoyGoode\.workbuddy\skills\tickflow\python')
    import tickflow_env  # noqa: auto-inject TICKFLOW_API_KEY
from tickflow import TickFlow
    tf = TickFlow.free()  #  API keyʹѲ
    df = tf.klines.get(tf_code, period='1d', count=days, as_dataframe=True)
    if df is None or len(df) < 100:
        raise ValueError(f'TickFlow数据不足: {len(df) if df is not None else 0}?)
    # 标准?
    if 'trade_date' in df.columns:
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df.set_index('trade_date', inplace=True)
    elif 'datetime' in df.columns:
        df['datetime'] = pd.to_datetime(df['datetime'])
        df.set_index('datetime', inplace=True)
    elif 'timestamp' in df.columns:
        df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('datetime', inplace=True)
    df.sort_index(inplace=True)
    for c in ['open', 'high', 'low', 'close', 'volume']:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.dropna(subset=['close'])
    return df


# ══?回测引擎 ══?
class SonnetBacktester:
    """多笔持仓回测引擎"""

    def __init__(self, df_daily, label='SPY', initial_cash=INITIAL_CASH):
        self.df = df_daily
        self.label = label
        self.initial_cash = initial_cash

        # 预计算信?
        print(f'  [{label}] 预计算中... ({len(df_daily)}条日?', flush=True)
        try:
            self.pc = XMMSonnetPrecomputed(df_daily, verbose=False)
        except ValueError as e:
            print(f'  [{label}] 预计算失? {e}')
            self.pc = None
            return

        self.warmup = max(TREND_LONG, TD_COMPLETE + TD_REF)

    def run(self):
        if self.pc is None:
            return None

        df = self.df
        pc = self.pc
        cash = self.initial_cash
        shares = 0.0
        avg_cost = 0.0       # 加权平均成本
        total_cost = 0.0     # 累计买入金额（用于算均成本）
        total_shares_bought = 0.0

        trades = []
        portfolio = []
        last_exit_idx = -COOLDOWN_DAYS - 1  # 冷却期跟?
        last_rebalance_idx = -MIN_REBALANCE_DAYS - 1  # 最小调仓间隔跟?
        daily_signals = []

        for i in range(self.warmup, len(df)):
            sig = pc.get(i)
            price = float(df['close'].iloc[i])
            date = df.index[i].date()

            pos_value = shares * price
            total = cash + pos_value
            current_pct = pos_value / total if total > 0 else 0.0

            portfolio.append({
                'date': str(date), 'value': total,
                'signal': sig['signal'], 'limit': sig['position_limit'],
                'price': price, 'shares': shares, 'pct': current_pct,
            })
            daily_signals.append(sig)

            action = sig['signal']
            limit = sig['position_limit']
            reason = sig['layer2']

            # ══?EXIT: 全部平仓 ══?
            if action == 'EXIT' and shares > 0:
                exec_price = price * (1 - SLIPPAGE)
                proceeds = shares * exec_price * (1 - COMMISSION_RATE)
                pnl_pct = (exec_price - avg_cost) / avg_cost * 100 if avg_cost > 0 else 0
                cash += proceeds
                trades.append({
                    'date': str(date), 'action': 'EXIT',
                    'price': round(exec_price, 2), 'shares': int(shares),
                    'pnl_pct': round(pnl_pct, 2),
                    'reason': reason[:40],
                })
                shares = 0.0
                avg_cost = 0.0
                total_cost = 0.0
                total_shares_bought = 0.0
                last_exit_idx = i
                continue

            # ══?REDUCE: 减仓到目标比?══?
            if action == 'REDUCE' and shares > 0 and current_pct > 0.10:
                target_pct = 0.5 * current_pct  # 减半
                target_value = total * target_pct
                sell_value = pos_value - target_value
                sell_shares = int(sell_value / price)
                if sell_shares >= 1 and sell_value >= MIN_TRADE_VALUE:
                    exec_price = price * (1 - SLIPPAGE)
                    proceeds = sell_shares * exec_price * (1 - COMMISSION_RATE)
                    cash += proceeds
                    shares -= sell_shares
                    # 更新均成本（减仓不影响均成本价）
                    trades.append({
                        'date': str(date), 'action': 'REDUCE',
                        'price': round(exec_price, 2), 'shares': sell_shares,
                        'reason': reason[:40],
                    })
                continue

            # ══?BUY / STRONG_BUY ══?
            if action in ('BUY', 'STRONG_BUY') and limit >= 0.05:
                target_pct = limit  # 目标仓位比例
                cooldown_active = (i - last_exit_idx) < COOLDOWN_DAYS

                if shares == 0 and cooldown_active:
                    # 冷却期不允许全新建仓
                    continue

                if shares == 0:
                    # 全新建仓
                    budget = total * target_pct
                    buy_shares = int(budget / price)
                    if buy_shares >= 1 and budget >= MIN_TRADE_VALUE:
                        exec_price = price * (1 + SLIPPAGE)
                        cost = buy_shares * exec_price * (1 + COMMISSION_RATE)
                        cash -= cost
                        shares += buy_shares
                        avg_cost = exec_price
                        total_cost = cost
                        total_shares_bought = buy_shares
                        trades.append({
                            'date': str(date), 'action': 'BUY',
                            'price': round(exec_price, 2), 'shares': buy_shares,
                            'target_pct': round(target_pct * 100, 1),
                            'reason': reason[:40],
                        })
                else:
                    # 加仓/减仓：调整到目标仓位
                    days_since_rebalance = i - last_rebalance_idx
                    pct_diff = target_pct - current_pct

                    if pct_diff > MIN_POSITION_CHANGE:
                        # 需要加仓（仓位差距>15%?
                        if days_since_rebalance < MIN_REBALANCE_DAYS:
                            continue  # 跳过，间隔太?
                        gap_value = total * pct_diff
                        buy_shares = int(gap_value / price)
                        if buy_shares >= 1 and gap_value >= MIN_TRADE_VALUE:
                            exec_price = price * (1 + SLIPPAGE)
                            cost = buy_shares * exec_price * (1 + COMMISSION_RATE)
                            cash -= cost
                            shares += buy_shares
                            total_cost += cost
                            total_shares_bought += buy_shares
                            avg_cost = total_cost / total_shares_bought
                            last_rebalance_idx = i
                            trades.append({
                                'date': str(date), 'action': 'ADD',
                                'price': round(exec_price, 2), 'shares': buy_shares,
                                'from_pct': round(current_pct * 100, 1),
                                'to_pct': round(target_pct * 100, 1),
                                'reason': reason[:40],
                            })
                    elif pct_diff < -MIN_POSITION_CHANGE:
                        # 信号降仓（仓位差?15%?
                        if days_since_rebalance < MIN_REBALANCE_DAYS:
                            continue  # 跳过，间隔太?
                        sell_value = total * (-pct_diff)
                        sell_shares = int(sell_value / price)
                        if sell_shares >= 1 and sell_value >= MIN_TRADE_VALUE:
                            exec_price = price * (1 - SLIPPAGE)
                            proceeds = sell_shares * exec_price * (1 - COMMISSION_RATE)
                            cash += proceeds
                            shares -= sell_shares
                            last_rebalance_idx = i
                            trades.append({
                                'date': str(date), 'action': 'TRIM',
                                'price': round(exec_price, 2), 'shares': sell_shares,
                                'from_pct': round(current_pct * 100, 1),
                                'to_pct': round(target_pct * 100, 1),
                                'reason': reason[:40],
                            })

        # 期末平仓
        if shares > 0:
            price = float(df['close'].iloc[-1])
            date = df.index[-1].date()
            pnl_pct = (price - avg_cost) / avg_cost * 100 if avg_cost > 0 else 0
            proceeds = shares * price * (1 - COMMISSION_RATE)
            cash += proceeds
            trades.append({
                'date': str(date), 'action': 'CLOSE',
                'price': round(price, 2), 'shares': int(shares),
                'pnl_pct': round(pnl_pct, 2), 'reason': '期末平仓',
            })

        # ══?统计 ══?
        return self._compute_stats(trades, portfolio)

    def _compute_stats(self, trades, portfolio):
        final_val = portfolio[-1]['value'] if portfolio else self.initial_cash
        ret = (final_val - self.initial_cash) / self.initial_cash * 100

        # 最大回?
        max_dd = 0.0
        peak = self.initial_cash
        for p in portfolio:
            if p['value'] > peak:
                peak = p['value']
            dd = (peak - p['value']) / peak * 100
            if dd > max_dd:
                max_dd = dd

        # 基准
        entry_p = float(self.df['close'].iloc[self.warmup])
        exit_p = float(self.df['close'].iloc[-1])
        benchmark = (exit_p - entry_p) / entry_p * 100

        # 夏普
        vals = [p['value'] for p in portfolio]
        daily_rets = np.diff(vals) / vals[:-1]
        sharpe = np.mean(daily_rets) / np.std(daily_rets) * np.sqrt(252) if np.std(daily_rets) > 0 else 0

        # 按交易对统计胜率
        buy_trades = [t for t in trades if t['action'] in ('BUY', 'ADD')]
        sell_trades = [t for t in trades if t['action'] in ('EXIT', 'CLOSE')]
        wins = sum(1 for t in sell_trades if t.get('pnl_pct', 0) > 0)
        win_rate = wins / len(sell_trades) * 100 if sell_trades else 0

        # 信号分布
        sig_counts = {}
        for p in portfolio:
            s = p['signal']
            sig_counts[s] = sig_counts.get(s, 0) + 1

        result = {
            'label': self.label,
            'strategy_return': round(ret, 2),
            'benchmark_return': round(benchmark, 2),
            'alpha': round(ret - benchmark, 2),
            'sharpe': round(sharpe, 2),
            'max_drawdown': round(max_dd, 2),
            'total_trades': len(buy_trades),
            'round_trips': len(sell_trades),
            'win_rate': round(win_rate, 1),
            'sig_counts': sig_counts,
            'trades': trades,
        }
        return result


def print_result(r):
    """打印回测结果"""
    print(f'\n{"="*60}')
    print(f'  {r["label"]} 回测结果')
    print(f'{"="*60}')
    print(f'  策略收益:   {r["strategy_return"]:+.2f}%')
    print(f'  基准收益:   {r["benchmark_return"]:+.2f}%')
    print(f'  超额Alpha:  {r["alpha"]:+.2f}%')
    print(f'  夏普比率:   {r["sharpe"]:.2f}')
    print(f'  最大回?   -{r["max_drawdown"]:.2f}%')
    print(f'  入场次数:   {r["total_trades"]}  (含加?')
    print(f'  平仓次数:   {r["round_trips"]}')
    print(f'  胜率:       {r["win_rate"]:.1f}%')

    print(f'\n  信号分布:')
    for k, v in sorted(r['sig_counts'].items(), key=lambda x: -x[1]):
        print(f'    {k}: {v}')

    print(f'\n  交易明细:')
    for t in r['trades']:
        act = t['action']
        if 'pnl_pct' in t:
            print(f'    {t["date"]} {act:5s} {t["shares"]:>5d}?@{t["price"]:>8.2f}  PnL:{t["pnl_pct"]:+.2f}%  {t.get("reason","")[:35]}')
        elif 'from_pct' in t:
            print(f'    {t["date"]} {act:5s} {t["shares"]:>5d}?@{t["price"]:>8.2f}  {t["from_pct"]:.0f}%→{t["to_pct"]:.0f}%  {t.get("reason","")[:30]}')
        elif 'target_pct' in t:
            print(f'    {t["date"]} {act:5s} {t["shares"]:>5d}?@{t["price"]:>8.2f}  目标{t["target_pct"]:.0f}%  {t.get("reason","")[:30]}')
        else:
            print(f'    {t["date"]} {act:5s} {t["shares"]:>5d}?@{t["price"]:>8.2f}  {t.get("reason","")[:35]}')


# ══?主入?══?
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Sonnet三周期回测v3（多笔持仓）')
    parser.add_argument('--spy', action='store_true', help='回测SPY')
    parser.add_argument('--hk', action='store_true', help='回测港股(Futu)')
    parser.add_argument('--tickflow', type=str, help='回测TickFlow港股(代码?00700.HK)')
    parser.add_argument('--all-hk', action='store_true', help='回测全部8只港?)
    args = parser.parse_args()

    results = []

    if args.spy or (not args.hk and not args.tickflow and not args.all_hk):
        print('\n>>> 回测 SPY 10年日?<<<')
        df = load_spy_daily()
        print(f'  数据: {len(df)}? {df.index[0].date()} ~ {df.index[-1].date()}')
        bt = SonnetBacktester(df, label='SPY')
        r = bt.run()
        if r:
            print_result(r)
            results.append(r)

    if args.hk or args.all_hk:
        HK_STOCKS = [
            ('HK.00700', '腾讯'), ('HK.09988', '阿里'),
            ('HK.01810', '小米'), ('HK.03690', '美团'),
            ('HK.09618', '京东'), ('HK.02318', '平安'),
            ('HK.00005', '汇丰'), ('HK.00941', '中国移动'),
        ]
        stocks = HK_STOCKS if args.all_hk else HK_STOCKS[:4]
        for code, name in stocks:
            print(f'\n>>> 回测 {name} ({code}) <<<')
            try:
                df = load_futu_stock(code, name)
                print(f'  数据: {len(df)}? {df.index[0].date()} ~ {df.index[-1].date()}')
                bt = SonnetBacktester(df, label=f'{name}({code})')
                r = bt.run()
                if r:
                    print_result(r)
                    results.append(r)
            except Exception as e:
                print(f'  {name} 回测失败: {e}')

    if args.tickflow:
        tf_code = args.tickflow
        print(f'\n>>> 回测 TickFlow: {tf_code} <<<')
        try:
            df = load_tickflow_stock(tf_code)
            print(f'  数据: {len(df)}? {df.index[0].date()} ~ {df.index[-1].date()}')
            bt = SonnetBacktester(df, label=f'TickFlow:{tf_code}')
            r = bt.run()
            if r:
                print_result(r)
                results.append(r)
        except Exception as e:
            print(f'  TickFlow回测失败: {e}')

    # 汇?
    if len(results) > 1:
        print(f'\n{"="*80}')
        print(f'  回测汇?)
        print(f'{"="*80}')
        print(f'  {"标的":<18} {"策略收益":>10} {"基准":>10} {"超额":>10} {"夏普":>8} {"回撤":>8} {"交易":>6} {"胜率":>6}')
        for r in results:
            print(f'  {r["label"]:<18} {r["strategy_return"]:>+10.1f}% {r["benchmark_return"]:>+10.1f}% {r["alpha"]:>+10.1f}% {r["sharpe"]:>8.2f} {r["max_drawdown"]:>8.1f}% {r["total_trades"]:>6} {r["win_rate"]:>5.1f}%')

    # 保存
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    save_path = os.path.join(OUTPUT_DIR, 'sonnet_backtest_v3.json')
    with open(save_path, 'w', encoding='utf-8') as f:
        # 不保存trades明细到汇总（太大），只存统计
        summary = [{k: v for k, v in r.items() if k != 'trades'} for r in results]
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f'\n结果已保? {save_path}')
