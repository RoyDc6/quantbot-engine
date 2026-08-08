# -*- coding: utf-8 -*-
import os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
if not os.environ.get("TICKFLOW_API_KEY"):
    raise RuntimeError("TICKFLOW_API_KEY environment variable is required")
from tickflow import TickFlow
import pandas as pd

tf = TickFlow(api_key=os.environ['TICKFLOW_API_KEY'])
print("TickFlow credential configured")

# Test correct period names
for period in ['1d', '1w', '1M', '1Q', '1Y']:
    try:
        d = tf.klines.get('SPY.US', period=period, count=10)
        n = len(d.get('close', []))
        ts = d.get('timestamp', [])
        t_range = ''
        if ts:
            t_range = f"{pd.Timestamp(ts[0], unit='ms').date()} ~ {pd.Timestamp(ts[-1], unit='ms').date()}"
        print(f"  {period}: {n} bars {t_range}")
    except Exception as e:
        print(f"  {period}: ERROR - {type(e).__name__}: {e}")
