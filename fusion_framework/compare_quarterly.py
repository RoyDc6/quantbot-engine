import pandas as pd, numpy as np

# 季频 vs 月频 逐季度对比
nav_q = pd.read_csv(r'E:\quant\fusion_framework\quarterly_nav.csv')
nav_q['date'] = pd.to_datetime(nav_q['date'])
nav_q['ret'] = nav_q['nav'].pct_change().fillna(0)

nav_m = pd.read_csv(r'E:\quant\fusion_framework\low_monthly_nav.csv')
nav_m['date'] = pd.to_datetime(nav_m['date'])
nav_m['ret'] = nav_m['nav'].pct_change().fillna(0)

print('=== 逐季度对比：季频 vs 月频 ===')
print(f'{"季度":>6}  {"季频NAV":>12}  {"持仓":>4}  {"月频NAV":>12}  {"持仓":>4}')
for s, e, q in [(0,100,'Q1'),(100,200,'Q2'),(200,300,'Q3'),(300,400,'Q4'),(400,500,'Q5')]:
    e = min(e, len(nav_q))
    em = min(e, len(nav_m))
    qv = nav_q.iloc[s:e]
    qm = nav_m.iloc[s:em]
    if len(qv) < 2: continue
    ret_q = (qv.iloc[-1]['nav']/qv.iloc[0]['nav']-1)*100
    ret_m = (qm.iloc[-1]['nav']/qm.iloc[0]['nav']-1)*100
    print(f'{q:>6}  {qv.iloc[-1]["nav"]/1e4:>10.1f}万  {int(qv.iloc[-1]["n_pos"]):>4}只  {qm.iloc[-1]["nav"]/1e4:>10.1f}万  {int(qm.iloc[-1]["n_pos"]):>4}只')

print()
print('季频总NAV:', nav_q.iloc[-1]['nav']/1e4, '万')
print('月频总NAV:', nav_m.iloc[-1]['nav']/1e4, '万')
print()
print('=== 关键洞察 ===')
print('季频持仓为何这么少？')
print('REBAL_FREQ=60, 500天只有 500//60 = 8次调仓机会')
print('月频持仓为何满仓？因为每5天有机会重建，频率高得多')
print()
print('止损触发21次 = 约一半的持仓被止损出局')
print('-8%止损在港股高波动市场太容易触发')
print('止损杀死了反弹机会（港股反弹迅猛）')
