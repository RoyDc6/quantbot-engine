# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import json, pandas as pd

CACHE = r'E:\quant\scanner\cache'
for fn in ['SPY_daily_2500.json', 'SPY_weekly_500.json', 'SPY_monthly_240.json']:
    with open(f'{CACHE}/{fn}') as f:
        d = json.load(f)
    df = pd.DataFrame(d)
    if 'trade_date' in df.columns:
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        print(f'{fn}: {len(df)} rows, {df["trade_date"].min().date()} ~ {df["trade_date"].max().date()}')
    else:
        print(f'{fn}: {len(df)} rows, cols={list(df.columns[:6])}')
