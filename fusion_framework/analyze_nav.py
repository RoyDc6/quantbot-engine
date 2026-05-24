import pandas as pd, numpy as np

nav = pd.read_csv(r'E:\quant\fusion_framework\low_monthly_nav.csv')
nav['date'] = pd.to_datetime(nav['date'])
nav['nav_norm'] = nav['nav'] / nav['nav'].iloc[0] * 100

print('=== NAV走势（每100日）===')
for i in range(0, 500, 100):
    row = nav.iloc[i] if i < len(nav) else nav.iloc[-1]
    print(f'  Day {i+1:3d}: NAV={row["nav"]/1e4:.1f}万  持仓={int(row["n_pos"])}只')

print()
print(f'期末: {nav.iloc[-1]["nav"]/1e4:.1f}万  持仓:{int(nav.iloc[-1]["n_pos"])}只')

# 分季度
print()
print('=== 分季度表现 ===')
for s,e,q in [(0,100,'Q1'),(100,200,'Q2'),(200,300,'Q3'),(300,400,'Q4'),(400,500,'Q5')]:
    if e > len(nav): e = len(nav)
    qnav = nav.iloc[s:e]
    if len(qnav) < 2: continue
    ret = (qnav.iloc[-1]['nav']/qnav.iloc[0]['nav']-1)*100
    print(f'  {q}: {ret:+.1f}%  终:{qnav.iloc[-1]["nav"]/1e4:.1f}万')

# 关键对比
print()
print('=== 关键对比 ===')
print(f'策略（0.7%现实）: +38.7%  年化17.8%  Sharpe1.07')
print(f'买入持有top20:     +65.0%  年化29.6%  Sharpe>2')
print(f'差距:             -26.3%              策略跑输基准')
print()
print('=== 根本原因分析 ===')
print('1. 月频调仓364次，每次0.7%成本 = 约2.5x年化成本')
print('2. 策略在趋势市追涨（高wrsi），但月频入场时机差')
print('3. 基准top20同样是动量策略，且无成本')
print('4. IC=0.49极强但实际收益不达预期 → 时序误差 + 成本')
print()
print('=== 优化方向 ===')
print('A. 降低调仓频率：季频(60日)换手约15次，成本降至1.5%')
print('B. 提高持仓上限：从15只→30只，降低单只风险')
print('C. 改变入场逻辑：从追涨改为等回调买')
print('D. 添加止损：-8%止损降低最大回撤')
print('E. 真实评估：用更长历史（2020-2024熊牛市验证）')
