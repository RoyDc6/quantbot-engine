# -*- coding: utf-8 -*-
"""
09988.HK（阿里巴巴）徐小明策略回测
直接从Futu API获取数据，全历史扫描
"""
import json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import pandas as pd
from collections import Counter
from datetime import datetime, timedelta

sys.path.insert(0, 'E:/quant')
import config
from modules.engine import XMMStrategy


# ── 1. 从Futu获取数据 ──────────────────────────────────────────────
def fetch_09988():
    """从Futu OpenD下载09988日线"""
    from futu import OpenQuoteContext
    ctx = OpenQuoteContext(host=config.FUTU_HOST, port=config.FUTU_PORT)
    try:
        end_dt = datetime.now().strftime('%Y-%m-%d')
        start_dt = (datetime.now() - timedelta(days=730)).strftime('%Y-%m-%d')  # 2年数据
        ret, data, page_key = ctx.request_history_kline(
            'HK.09988', start=start_dt, end=end_dt,
            ktype='K_DAY', autype=None
        )
        if ret != 0:
            print(f"[ERROR] Futu返回: ret={ret}, msg={data}")
            return None
        if not isinstance(data, pd.DataFrame) or len(data) < 50:
            print(f"[ERROR] 数据不足: {len(data) if isinstance(data, pd.DataFrame) else 0}条")
            return None

        data = data.sort_values('time_key')
        df = pd.DataFrame()
        df['trade_date'] = pd.to_datetime(data['time_key'])
        df['open'] = data['open'].astype(float)
        df['high'] = data['high'].astype(float)
        df['low'] = data['low'].astype(float)
        df['close'] = data['close'].astype(float)
        df['volume'] = data['volume'].astype(float)
        df.set_index('trade_date', inplace=True)
        return df
    finally:
        ctx.close()


# ── 2. 全历史滚动扫描 ─────────────────────────────────────────────
def run_scan(df, lookback=60):
    engine = XMMStrategy(short_period=25, long_period=90)
    results = []
    for i in range(lookback, len(df)):
        window = df.iloc[:i+1].copy()
        sig = engine.analyze(window)
        sig['date'] = str(df.index[i])[:10]
        sig['close'] = float(df['close'].iloc[i])
        results.append(sig)
    return results


# ── 3. 输出 ────────────────────────────────────────────────────────
def print_signal_matrix(results):
    print("=" * 135)
    print(f"{'日期':<12}{'收盘':>8}{'市场':<10}{'趋势层':<26}{'结构层':<26}{'TD':>5}  {'最终':<8}{'仓位':>7}  原因")
    print("-" * 135)
    for r in results:
        mkt_map = {'UP': '↑UP', 'DOWN': '↓DOWN', 'SIDEWAYS': '→横', 'UNKNOWN': '?'}
        mkt = mkt_map.get(r.get('trend_layer', {}).get('market', r['market_trend']), '?')

        tl = r.get('trend_layer', {})
        trend_parts = []
        if tl.get('cross_short_up'): trend_parts.append('短顶突')
        if tl.get('cross_short_down'): trend_parts.append('短底跌')
        if tl.get('cross_long_up'): trend_parts.append('长顶突')
        if tl.get('cross_long_down'): trend_parts.append('长底跌')
        trend_str = '/'.join(trend_parts) if trend_parts else '—'

        sl = r.get('structure_layer', {})
        struct_parts = []
        if sl.get('底部钝化'): struct_parts.append('底钝化')
        if sl.get('底部结构'): struct_parts.append('底结构')
        if sl.get('顶部钝化'): struct_parts.append('顶钝化')
        if sl.get('顶部结构'): struct_parts.append('顶结构')
        struct_str = '/'.join(struct_parts) if struct_parts else '—'

        tc = r.get('td_count', 0)
        ta = r.get('td_amplified', False)
        td_str = f"{tc}"
        if ta: td_str += "×"
        else: td_str += " "

        sig_map = {'BUY': '🟢BUY', 'SELL': '🔴SELL', 'HOLD': '⚪HOLD'}
        sig = sig_map.get(r['signal'], r['signal'])
        pos = f"{r['position_size']:.0%}" if r['position_size'] > 0 else '—'
        reason = r['reason'][:40]

        print(f"{r['date']:<12}{r['close']:>8.2f}{mkt:<10}{trend_str:<26}{struct_str:<26}{td_str:>5}  {sig:<8}{pos:>7}  {reason}")


def print_stats(results):
    s = Counter(r['signal'] for r in results)
    t = Counter(r.get('trend_layer', {}).get('market', '?') for r in results)
    print(f"\n📊 市场分布:")
    print(f"  ↑UP: {t.get('UP',0)}天  ↓DOWN: {t.get('DOWN',0)}天  →横: {t.get('SIDEWAYS',0)}天")
    print(f"\n📊 最终信号:")
    print(f"  🟢BUY: {s.get('BUY',0)}次  🔴SELL: {s.get('SELL',0)}次  ⚪HOLD: {s.get('HOLD',0)}次")

    key = [r for r in results if r['signal'] in ('BUY', 'SELL')]
    if key:
        print(f"\n⚡ 关键交易信号 ({len(key)}次):")
        for r in key:
            print(f"  {r['date']}  {r['signal']} {r['position_size']:.0%}  类型:{r['signal_type']}  原因:{r['reason']}")
    else:
        print(f"\n⚡ 无关键交易信号")


def print_latest(r):
    print(f"\n{'='*135}")
    print(f"📊 最新信号: {r['date']}  收盘=${r['close']:.2f}")
    print(f"{'='*135}")
    tl = r.get('trend_layer', {})
    print(f"\n【趋势层】  市场={tl.get('market','?')}")
    print(f"  收盘={tl.get('close',0):.2f}  短顶={r['short_top']:.2f}  短底={r['short_bot']:.2f}  长顶={r['long_top']:.2f}  长底={r['long_bot']:.2f}")
    sl = r.get('structure_layer', {})
    print(f"\n【结构层】")
    for k in ['底部钝化','底部结构','顶部钝化','顶部结构']:
        if k in sl:
            print(f"  {'✅' if sl[k] else '❌'} {k}" + (f" (连续{sl.get('连续顶部钝化天数',0)}天)" if k == '顶部钝化' and sl.get(k) else f" (连续{sl.get('连续底部钝化天数',0)}天)" if k == '底部钝化' and sl.get(k) else ""))
    print(f"\n【TD序列】  计数={r['td_count']}  TD×1.5放大={r['td_amplified']}")
    print(f"\n【决策】  信号: {r['signal']}  仓位: {r['position_size']:.0%}  类型: {r['signal_type']}")
    print(f"  原因: {r['reason']}")


# ── 4. 主流程 ──────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("  09988.HK（阿里巴巴）徐小明策略回测")
    print("=" * 60)

    print("\n从Futu OpenD获取数据...")
    df = fetch_09988()
    if df is None:
        print("[FAIL] 无法获取数据，请确认Futu OpenD正在运行")
        return

    print(f"数据: {len(df)}条  {str(df.index[0])[:10]} ~ {str(df.index[-1])[:10]}")
    print(f"价格: ${df['close'].iloc[0]:.2f} → ${df['close'].iloc[-1]:.2f} "
          f"({(df['close'].iloc[-1]/df['close'].iloc[0]-1)*100:+.1f}%)")

    print(f"\n运行徐小明三层信号扫描...")
    results = run_scan(df, lookback=60)
    print(f"扫描完成: {len(results)}天")

    print_stats(results)
    print_signal_matrix(results)
    print_latest(results[-1])


if __name__ == '__main__':
    main()