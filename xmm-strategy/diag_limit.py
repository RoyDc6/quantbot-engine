# -*- coding: utf-8 -*-
"""定位 Sonnet position_limit 过低的根因"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import json, pandas as pd, numpy as np
sys.path.insert(0, r'E:\quant\xmm-strategy')
from xmm_sonnet_model import XMMSonnetPrecomputed, TREND_LONG, TD_COMPLETE, TD_REF

CACHE = r'E:\quant\scanner\cache'
with open(f'{CACHE}/SPY_daily_2500.json') as f:
    df = pd.DataFrame(json.load(f))
df['trade_date'] = pd.to_datetime(df['trade_date'])
df.set_index('trade_date', inplace=True)
df.sort_index(inplace=True)

pc = XMMSonnetPrecomputed(df, verbose=False)
WARMUP = max(TREND_LONG, TD_COMPLETE + TD_REF)

# 找到 limit >= 0.10 的所有信号
high_limit = []
for i in range(WARMUP, len(df)):
    sig = pc.get(i)
    limit = sig['position_limit']
    if limit >= 0.10:
        high_limit.append({
            'date': df.index[i].date(),
            'close': sig['close'],
            'limit': limit,
            'm': sig['monthly_trend'],
            'w': sig['weekly_trend'],
            'd': sig['daily_trend'],
            'd_ld': sig['d_low_n'],
            'bot_s': sig['bot_struct'],
            'bot_div': sig['bot_div'],
        })

print(f"高仓位信号 (limit >= 0.10): {len(high_limit)} 个 / 总 {len(df)-WARMUP} 天")
print(f"占比: {len(high_limit)/(len(df)-WARMUP)*100:.1f}%")

# 按周线状态分组
w_groups = {}
for x in high_limit:
    w = x['w']
    if w not in w_groups: w_groups[w] = []
    w_groups[w].append(x['limit'])

print("\n=== 高仓位时的周线分布 ===")
for w, limits in sorted(w_groups.items()):
    arr = np.array(limits)
    print(f"  周线={w}: {len(arr)}次, 均limit={arr.mean():.3f}, max={arr.max():.3f}")

# 看 limit 为什么这么低: 分析月度 EMA 参数
nm = len(pc.monthly_df)
m_lp_used = pc.m_trend['长底'].ewm(span=60).mean().iloc[-1]
print(f"\n月线 EMA slow 使用: {nm//2} (实际 m_lp)")

# 直接看 get_position_limit 的逻辑
# 通过模拟不同状态组合看输出
print("\n=== 模拟 position_limit 行为 ===")
# 找 limit 最高的几次
top5 = sorted(high_limit, key=lambda x: x['limit'], reverse=True)[:5]
for x in top5:
    print(f"  {x['date']} limit={x['limit']:.3f} m={x['m']} w={x['w']} d={x['d']} d_ld={x['d_ld']} bot_s={x['bot_s']}")

# 找 limit 最低但 BUY 信号的情况
print("\n=== BUY 但 limit < 0.10 的原因 ===")
low_limit_buy = [x for x in high_limit if x['limit'] < 0.10]
print(f"低仓位 BUY: {len(low_limit_buy)} 次")
if low_limit_buy:
    ex = low_limit_buy[0]
    print(f"  例: {ex['date']} limit={ex['limit']:.3f} m={ex['m']} w={ex['w']} d={ex['d']} d_ld={ex['d_ld']}")