# -*- coding: utf-8 -*-
"""检查趋势突破信号为何未被触发"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import json

# 加载数据
with open(r'E:\quant\scanner\cache\SPY_US_1y.json', 'r', encoding='utf-8') as f:
    df = json.load(f)  # 直接是列表

print(f"Total bars: {len(df)}")
print(f"Date range: {df[0]['trade_date']} ~ {df[-1]['trade_date']}")

# 加载模型
sys.path.insert(0, r'E:\quant\xmm-strategy')
from xmm_sonnet_model import XMMSonnetPrecomputed, TREND_LONG, TD_COMPLETE, TD_REF

import pandas as pd

# 转换为DataFrame，trade_date 设为索引
df_pd = pd.DataFrame(df)
df_pd['trade_date'] = pd.to_datetime(df_pd['trade_date'])
df_pd.set_index('trade_date', inplace=True)

pc = XMMSonnetPrecomputed(df_pd)

# 预计算后取结果
WARMUP = max(TREND_LONG, TD_COMPLETE + TD_REF)  # ~94

# 检查趋势突破信号
print("\n检查趋势突破条件...")

trend_breakout_count = 0
trend_follow_count = 0

for i in range(WARMUP, len(df)):
    sig = pc.get(i)
    
    # 检查趋势突破模式
    if '趋势突破' in sig.get('layer2', ''):
        trend_breakout_count += 1
        print(f"{df[i]['trade_date']} 趋势突破: {sig['layer2']} | 仓位:{sig['position_limit']:.0%}")
    
    if '趋势跟随' in sig.get('layer2', ''):
        trend_follow_count += 1
        print(f"{df[i]['trade_date']} 趋势跟随: {sig['layer2']} | 仓位:{sig['position_limit']:.0%}")

print(f"\n趋势突破信号: {trend_breakout_count} 次")
print(f"趋势跟随信号: {trend_follow_count} 次")

# 检查月线强多的情况
print("\n检查月线趋势分布...")
monthly_trends = {}
for i in range(WARMUP, len(df)):
    sig = pc.get(i)
    mt = sig['monthly_trend']
    monthly_trends[mt] = monthly_trends.get(mt, 0) + 1

for mt, count in sorted(monthly_trends.items(), key=lambda x: -x[1]):
    print(f"  {mt}: {count}")

# 检查 cross_long_up 突破
print("\n检查月/周长期通道突破...")
m_long_bull_count = 0
w_long_bull_count = 0

for i in range(WARMUP, len(df)):
    sig = pc.get(i)
    mi = int(pc._d2m[i])
    wi = int(pc._d2w[i])
    
    if bool(pc.m_trend['cross_long_up'].iloc[mi]):
        m_long_bull_count += 1
    if bool(pc.w_trend['cross_long_up'].iloc[wi]):
        w_long_bull_count += 1

print(f"月线长期通道突破: {m_long_bull_count} 次")
print(f"周线长期通道突破: {w_long_bull_count} 次")

# 检查是否有同时满足: 月线强多 + 长期突破 + 无顶部结构
print("\n检查组合条件...")
combo_count = 0
for i in range(WARMUP, len(df)):
    sig = pc.get(i)
    mi = int(pc._d2m[i])
    wi = int(pc._d2w[i])
    
    m_ts = sig['monthly_trend']
    has_top_struct = sig['top_struct']
    has_top_passive = False  # 需要从 d_ms 获取
    
    m_long_bull = bool(pc.m_trend['cross_long_up'].iloc[mi])
    w_long_bull = bool(pc.w_trend['cross_long_up'].iloc[wi])
    
    if m_ts == 'strong_bull' and not has_top_struct and (m_long_bull or w_long_bull):
        combo_count += 1
        if combo_count <= 5:
            print(f"  {df[i]['trade_date']}: 月线={m_ts}, 月突破={m_long_bull}, 周突破={w_long_bull}")

print(f"\n组合条件满足: {combo_count} 次")
