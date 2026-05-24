# check_sonnet_signals.py
# 诊断Sonnet信号

import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, 'E:/quant/xmm-strategy')

import pandas as pd
from xmm_sonnet_model import XMMSonnetModel

def load_spy():
    with open('E:/quant/scanner/cache/SPY_US_1y.json', 'r') as f:
        raw = json.load(f)
    df = pd.DataFrame(raw)
    df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
    df = df.sort_values('datetime').set_index('datetime')
    for c in ['open','high','low','close','volume']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    return df.dropna()

df = load_spy()
model = XMMSonnetModel()
pc = model.precompute(df, verbose=False)

print('=' * 100)
print('Sonnet信号诊断 (检查底部结构形成情况)')
print('=' * 100)

# 统计底部结构出现的次数
bot_struct_days = []
bot_div_days    = []
top_struct_days = []

for i in range(95, len(df)):
    sig = pc.get(i)
    if sig['bot_struct']:
        bot_struct_days.append((df.index[i].strftime('%Y-%m-%d'), sig))
    if sig['bot_div']:
        bot_div_days.append((df.index[i].strftime('%Y-%m-%d'), sig))
    if sig['top_struct']:
        top_struct_days.append((df.index[i].strftime('%Y-%m-%d'), sig))

print(f'\n底部结构形成: {len(bot_struct_days)}次')
print(f'底部钝化:     {len(bot_div_days)}次')
print(f'顶部结构形成: {len(top_struct_days)}次')

if bot_struct_days:
    print('\n📅 底部结构明细:')
    for dt, sig in bot_struct_days[:10]:
        print(f'  {dt}  月线:{sig["monthly_trend"]:<12} 周线:{sig["weekly_trend"]:<12} 日线:{sig["daily_trend"]:<12}  信号:{sig["signal"]}')

if top_struct_days:
    print('\n📅 顶部结构明细:')
    for dt, sig in top_struct_days[:5]:
        print(f'  {dt}  信号:{sig["signal"]}  原因:{sig["layer2"]}')

# 检查EXIT信号
print('\n📅 EXIT/REDUCE信号:')
exit_count = 0
for i in range(95, len(df)):
    sig = pc.get(i)
    if sig['signal'] in ('EXIT', 'REDUCE'):
        exit_count += 1
        if exit_count <= 10:
            print(f'  {df.index[i].strftime("%Y-%m-%d")}  {sig["signal"]:<6} L1:{sig["layer1"]} L2:{sig["layer2"]}')

print(f'\n总计EXIT/REDUCE: {exit_count}次')
