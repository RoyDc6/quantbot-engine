# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import json, pandas as pd, sys
sys.path.insert(0, r'E:\quant\xmm-strategy')
from xmm_sonnet_model import XMMSonnetPrecomputed, TREND_LONG, TD_COMPLETE, TD_REF

with open(r'E:\quant\scanner\cache\SPY_daily_600.json', 'r', encoding='utf-8') as f:
    df = pd.DataFrame(json.load(f))
df['trade_date'] = pd.to_datetime(df['trade_date'])
df.set_index('trade_date', inplace=True)
df.sort_index(inplace=True)
print(f'日线: {len(df)}条 {df.index[0].date()}~{df.index[-1].date()}')

pc = XMMSonnetPrecomputed(df)
print(f'月线重采样: {len(pc.monthly_df)}条')
m_trend = pc.m_trend
w_trend = pc.w_trend
d_trend = pc.d_trend
print(f'Monthly EMA: m_sp={m_trend["ema_fast"]} m_lp={m_trend["ema_slow"]}')
print(f'Weekly EMA: w_sp={w_trend["ema_fast"]} w_lp={w_trend["ema_slow"]}')
print(f'Daily EMA: d_sp={d_trend["ema_fast"]} d_lp={d_trend["ema_slow"]}')

WARMUP = max(TREND_LONG, TD_COMPLETE + TD_REF)
print(f'WARMUP: {WARMUP}')
print(f'Monthly rows available: {len(pc.monthly_df)}, need {m_trend["ema_slow"]} for slow EMA')
nm = len(pc.monthly_df)
lp = m_trend["ema_slow"]
print(f'Monthly EMA (6,90) would need nm//2={nm//2}, actual nm={nm}, m_lp={lp}')