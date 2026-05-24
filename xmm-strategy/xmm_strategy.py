# -*- coding: utf-8 -*-
"""
徐小明量化策略 · 结构系统 v4.0
三层独立信号 + TD放大器
"""
import json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import pandas as pd
from collections import Counter
from modules.engine import XMMStrategy


def load_spy_data():
    with open(r'E:/quant/scanner/cache/SPY_US.json', 'r', encoding='utf-8') as f:
        raw = json.load(f)
    df = pd.DataFrame(raw)
    date_col = 'datetime' if 'datetime' in df.columns else 'date'
    df['trade_date'] = pd.to_datetime(df[date_col], unit='ms').dt.strftime('%Y-%m-%d')
    df = df[['trade_date','open','high','low','close','volume']].sort_values('trade_date').reset_index(drop=True)
    df.set_index('trade_date', inplace=True)
    return df


def run_scan(df, lookback=60):
    engine = XMMStrategy(short_period=25, long_period=90)
    results = []
    for i in range(lookback, len(df)):
        window = df.iloc[:i+1].copy()
        sig = engine.analyze(window)
        sig['date'] = df.index[i]
        sig['close'] = float(df['close'].iloc[i])
        results.append(sig)
    return results


def print_signal_matrix(results):
    print("=" * 135)
    print(f"{'日期':<12}{'收盘':>8}{'市场':<10}{'趋势层':<26}{'结构层':<26}{'TD':>5}  {'最终':<8}{'仓位':>7}  多/空分  原因")
    print("-" * 135)
    for r in results:
        # 市场
        mkt_map = {'UP': '↑UP', 'DOWN': '↓DOWN', 'SIDEWAYS': '→横', 'UNKNOWN': '?'}
        mkt = mkt_map.get(r.get('trend_layer', {}).get('market', r['market_trend']), '?')

        # 趋势层
        tl = r.get('trend_layer', {})
        trend_parts = []
        if tl.get('cross_short_up'): trend_parts.append('短顶突')
        if tl.get('cross_short_down'): trend_parts.append('短底跌')
        if tl.get('cross_long_up'): trend_parts.append('长顶突')
        if tl.get('cross_long_down'): trend_parts.append('长底跌')
        trend_str = '/'.join(trend_parts) if trend_parts else '—'

        # 结构层
        sl = r.get('structure_layer', {})
        struct_parts = []
        if sl.get('底部钝化'): struct_parts.append('底钝化')
        if sl.get('底部结构'): struct_parts.append('底结构')
        if sl.get('顶部钝化'): struct_parts.append('顶钝化')
        if sl.get('顶部结构'): struct_parts.append('顶结构')
        if sl.get('底钝化消失'): struct_parts.append('底钝消')
        if sl.get('顶钝化消失'): struct_parts.append('顶钝消')
        struct_str = '/'.join(struct_parts) if struct_parts else '—'

        # TD
        tc = r.get('td_count', 0)
        ta = r.get('td_amplified', False)
        td_str = f"{tc}"
        if ta: td_str += "×"
        else: td_str += " "

        # 最终信号
        sig_map = {'BUY': '🟢BUY', 'SELL': '🔴SELL', 'HOLD': '⚪HOLD'}
        sig = sig_map.get(r['signal'], r['signal'])
        pos = f"{r['position_size']:.0%}" if r['position_size'] > 0 else '—'
        bull = r.get('bull_score', 0)
        bear = r.get('bear_score', 0)
        reason = r['reason'][:40]

        print(f"{r['date']:<12}{r['close']:>8.2f}{mkt:<10}{trend_str:<26}{struct_str:<26}{td_str:>5}  {sig:<8}{pos:>7}  {bull:4.1f}/{bear:4.1f}  {reason}")


def print_stats(results):
    s = Counter(r['signal'] for r in results)
    t = Counter(r.get('trend_layer', {}).get('market', '?') for r in results)
    print(f"\n📊 市场分布:")
    print(f"  ↑UP: {t.get('UP',0)}天  ↓DOWN: {t.get('DOWN',0)}天  →横: {t.get('SIDEWAYS',0)}天")
    print(f"\n📊 最终信号:")
    print(f"  🟢BUY: {s.get('BUY',0)}次  🔴SELL: {s.get('SELL',0)}次  ⚪HOLD: {s.get('HOLD',0)}次")
    buy_s = [r for r in results if r['signal'] == 'BUY']
    sell_s = [r for r in results if r['signal'] == 'SELL']
    if buy_s:
        avg_bull = sum(r['bull_score'] for r in buy_s) / len(buy_s)
        avg_bear = sum(r['bear_score'] for r in buy_s) / len(buy_s)
        print(f"  BUY平均: 多头={avg_bull:.1f}分 空头={avg_bear:.1f}分")
    if sell_s:
        avg_bull = sum(r['bull_score'] for r in sell_s) / len(sell_s)
        avg_bear = sum(r['bear_score'] for r in sell_s) / len(sell_s)
        print(f"  SELL平均: 多头={avg_bull:.1f}分 空头={avg_bear:.1f}分")


def print_latest(r):
    print(f"\n{'='*135}")
    print(f"📊 最新信号: {r['date']}  收盘=${r['close']:.2f}")
    print(f"{'='*135}")

    # 趋势层
    tl = r.get('trend_layer', {})
    mkt = tl.get('market', '?')
    print(f"\n【趋势层】  市场={mkt}")
    print(f"  收盘={tl.get('close', 0):.2f}  短顶={r['short_top']:.2f}  短底={r['short_bot']:.2f}  长顶={r['long_top']:.2f}  长底={r['long_bot']:.2f}")
    tcross = []
    if tl.get('cross_short_up'): tcross.append('短顶突破')
    if tl.get('cross_short_down'): tcross.append('短底跌破')
    if tl.get('cross_long_up'): tcross.append('长顶突破')
    if tl.get('cross_long_down'): tcross.append('长底跌破')
    print(f"  交叉信号: {' | '.join(tcross) if tcross else '无'}")
    print(f"  多头分={tl.get('bull_score',0)}  空头分={tl.get('bear_score',0)}")

    # 结构层
    sl = r.get('structure_layer', {})
    print(f"\n【结构层】  MACD背离系统")
    checks = [
        ('底部钝化', '🔵底部钝化', '🔵底钝化'),
        ('底部结构', '🟢底部结构 ⭐⭐⭐', '🔵底钝化'),
        ('顶部钝化', '🔴顶部钝化', '🔴顶钝化'),
        ('顶部结构', '🔴顶部结构 ⭐⭐⭐', '🔴顶结构'),
        ('底钝化消失', '⚪底钝化消失', '⚪底钝消'),
        ('顶钝化消失', '⚪顶钝化消失', '⚪顶钝消'),
    ]
    any_struct = False
    for k, true_label, false_label in checks:
        if k in sl:
            any_struct = True
            mark = '✅' if sl[k] else '❌'
            print(f"  {mark} {true_label if sl[k] else false_label}")
    if not any_struct:
        print(f"  无结构信号")

    # TD层
    print(f"\n【TD序列】  计数={r['td_count']}  TD×1.5放大={r['td_amplified']}")

    # 最终融合
    print(f"\n【决策输出】")
    print(f"  信号: {r['signal']}  仓位: {r['position_size']:.0%}  类型: {r['signal_type']}")

    sig_map = {'BUY': '🟢BUY', 'SELL': '🔴SELL', 'HOLD': '⚪HOLD'}
    sig_icon = sig_map.get(r['signal'], r['signal'])
    star = '⭐' * int(r['strength']) if r['strength'] > 0 else '无'
    print(f"\n【最终信号】")
    print(f"  🏷️  {sig_icon}  强度={star}  ({r['signal_type']})")
    print(f"  💰 仓位: {r['position_size']:.0%}" if r['position_size'] > 0 else f"  💰 仓位: —")
    print(f"  📝 原因: {r['reason']}")


def main():
    print("加载SPY数据...")
    df = load_spy_data()
    print(f"数据: {len(df)}条  {df.index[0]} ~ {df.index[-1]}")
    print(f"价格: ${df['close'].iloc[0]:.2f} → ${df['close'].iloc[-1]:.2f} "
          f"({(df['close'].iloc[-1]/df['close'].iloc[0]-1)*100:+.1f}%)")

    print(f"\n运行徐小明三层信号扫描（最后40天）...")
    results = run_scan(df, lookback=60)
    print(f"扫描完成: {len(results)}天")

    print_stats(results)
    print_signal_matrix(results)
    print_latest(results[-1])

    # 关键信号
    key = [r for r in results if r['signal'] in ('BUY', 'SELL')]
    if key:
        print(f"\n⚡ 关键交易信号 ({len(key)}次):")
        for r in key:
            print(f"  {r['date']}  {r['signal']} {r['position_size']:.0%}  净分={r['net_score']:+.1f}  {r['reason']}")
    else:
        print(f"\n⚡ 无关键交易信号")


if __name__ == '__main__':
    main()
