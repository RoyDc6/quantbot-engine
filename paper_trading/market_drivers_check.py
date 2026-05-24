from futu import OpenQuoteContext, QuoteCtx, KLType, SubType, Market
import pandas as pd
from datetime import datetime, timedelta
import sys

q = OpenQuoteContext(host='127.0.0.1', port=11111)

print('=== 美股核心指标 ===')
try:
    vix_data = q.get_market_snapshot(['US.VXX'])
    spy_data = q.get_market_snapshot(['US.SPY'])
    print(f'VXX: {vix_data[1]}')
    print(f'SPY: {spy_data[1]}')
except Exception as e:
    print(f'美股数据获取失败: {e}')

print('\n=== 港股核心标的 ===')
hk_stocks = ['HK.00700', 'HK.09988', 'HK.01810', 'HK.03690', 'HK.09618', 'HK.02318']
for stock in hk_stocks:
    try:
        data = q.get_market_snapshot([stock])
        print(f'{stock}: {data[1]}')
    except Exception as e:
        print(f'{stock} 获取失败: {e}')

print('\n=== A股核心指数 ===')
a_indices = ['SZ.000001', 'SH.000001', 'SZ.399001']
for idx in a_indices:
    try:
        data = q.get_market_snapshot([idx])
        print(f'{idx}: {data[1]}')
    except Exception as e:
        print(f'{idx} 获取失败: {e}')

q.close()
