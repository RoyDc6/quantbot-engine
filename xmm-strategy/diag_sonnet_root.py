# -*- coding: utf-8 -*-
"""分析 Sonnet 策略踏空根因，量化每个信号的仓位分配"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import json, pandas as pd
import numpy as np
sys.path.insert(0, r'E:\quant\xmm-strategy')
from xmm_sonnet_model import XMMSonnetPrecomputed, TREND_LONG, TD_COMPLETE, TD_REF, _td_label, POSITION_MATRIX

CACHE = r'E:\quant\scanner\cache'
with open(f'{CACHE}/SPY_daily_2500.json') as f:
    df = pd.DataFrame(json.load(f))
df['trade_date'] = pd.to_datetime(df['trade_date'])
df.set_index('trade_date', inplace=True)
df.sort_index(inplace=True)

pc = XMMSonnetPrecomputed(df, verbose=False)
WARMUP = max(TREND_LONG, TD_COMPLETE + TD_REF)

# ═══ 问题1：BUY信号时 position_limit 的分布 ═══
buy_signals = []
for i in range(WARMUP, len(df)):
    sig = pc.get(i)
    if sig['signal'] in ('BUY', 'STRONG_BUY'):
        buy_signals.append({
            'date': df.index[i].date(),
            'limit': sig['position_limit'],
            'm_trend': sig['monthly_trend'],
            'w_trend': sig['weekly_trend'],
            'd_trend': sig['daily_trend'],
            'm_ln': sig['m_low_n'], 'm_hn': sig['m_high_n'],
            'w_ln': sig['w_low_n'], 'w_hn': sig['w_high_n'],
            'd_ln': sig['d_low_n'], 'd_hn': sig['d_high_n'],
            'layer2': sig['layer2'],
        })

limits = [x['limit'] for x in buy_signals]
print(f"BUY信号: {len(buy_signals)}次")
print(f"limit分布: mean={np.mean(limits):.3f}, median={np.median(limits):.3f}")
print(f"  <0.05: {sum(1 for l in limits if l<0.05)}")
print(f"  0.05~0.15: {sum(1 for l in limits if 0.05<=l<0.15)}")
print(f"  0.15~0.30: {sum(1 for l in limits if 0.15<=l<0.30)}")
print(f"  0.30~0.50: {sum(1 for l in limits if 0.30<=l<0.50)}")
print(f"  >=0.50: {sum(1 for l in limits if l>=0.50)}")

# ═══ 问题2：POSITION_MATRIX 在月线 strong_bull 时的输出 ═══
print(f"\n=== 月线strong_bull时仓位矩阵行为 ===")
# 月线 strong_bull → m_low_n 和 m_high_n 通常是什么值？
m_ln_dist = {}
m_hn_dist = {}
for x in buy_signals:
    ln, hn = x['m_ln'], x['m_hn']
    m_ln_dist[ln] = m_ln_dist.get(ln, 0) + 1
    m_hn_dist[hn] = m_hn_dist.get(hn, 0) + 1

print(f"月线 low_N 分布: {dict(sorted(m_ln_dist.items()))}")
print(f"月线 high_N 分布: {dict(sorted(m_hn_dist.items()))}")

# 模拟：月线TD标签 → 仓位查表
print(f"\n模拟仓位查表 (月线TD标签 vs 周线TD标签):")
for m_label in ['low_deep', 'low', 'neutral', 'high_low', 'high_high']:
    for w_label in ['low_deep', 'low', 'neutral', 'high_low', 'high_high']:
        key = (m_label, w_label)
        val = POSITION_MATRIX.get(key, '默认0.10')
        if val != '默认0.10':
            print(f"  ({m_label:10s}, {w_label:10s}) → {val}")

# ═══ 问题3：回测中实际触发的BUY信号类型 ═══
print(f"\n=== BUY 信号类型统计 ===")
type_counts = {}
for x in buy_signals:
    l2 = x['layer2']
    type_counts[l2] = type_counts.get(l2, 0) + 1
for k, v in sorted(type_counts.items(), key=lambda x: -x[1]):
    print(f"  {k}: {v}次")

# ═══ 问题4：如果允许满仓跟随月线强多，理论收益 ═══
print(f"\n=== 理论收益估算：月线强多时满仓持有 ===")
entry_price = float(df['close'].iloc[WARMUP])
exit_price = float(df['close'].iloc[-1])
print(f"买入持有: {entry_price:.2f} → {exit_price:.2f} = {(exit_price/entry_price-1)*100:.2f}%")

# 月线 strong_bull 且无 EXIT 条件 → 满仓持有
# 如果只在月线走弱时空仓
monthly_exits = 0
in_market_days = 0
for i in range(WARMUP, len(df)):
    sig = pc.get(i)
    if sig['monthly_trend'] == 'strong_bull':
        in_market_days += 1
    else:
        monthly_exits += 1

print(f"月线强多天数: {in_market_days}/{len(df)-WARMUP} ({in_market_days/(len(df)-WARMUP)*100:.1f}%)")
print(f"月线非强多天数: {monthly_exits}")

# ═══ 结论 ═══
print(f"\n=== 核心诊断 ===")
print(f"1. 月线100% strong_bull → 按Sonnet规则永远可以入场")
print(f"2. 但仓位矩阵: (neutral, neutral)→0.10, (low, neutral)→0.30")
print(f"3. 月线强多时TD很少到low → 大部分时间是neutral → 仓位10%")
print(f"4. 趋势跟随模式还被压缩到≤15%和≤30%")
print(f"5. 结论: 月线强多时应该默认持有，结构信号用于加/减仓而非决定是否入场")
