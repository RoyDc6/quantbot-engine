# -*- coding: utf-8 -*-
import sys, os
os.chdir(r'E:\quant\fusion_framework')
sys.path.insert(0, 'E:/quant/fusion_framework')
sys.path.insert(0, 'E:/quant')

import warnings
warnings.filterwarnings('ignore')
import numpy as np
import pandas as pd

from run_spy_backtest import *

df = load_spy_data()
close = df['close'].values

rsi_d = compute_rsi(close, 14)
rsi_w = compute_weekly_rsi(close, 4)

print("SPY RSI分析（全部100天）:")
print(f"  日RSI: 均值={rsi_d.mean():.1f}, 范围=[{rsi_d.min():.1f}, {rsi_d.max():.1f}]")
print(f"  周RSI: 均值={rsi_w.mean():.1f}, 范围=[{rsi_w.min():.1f}, {rsi_w.max():.1f}]")
print(f"  RSI<=30天数: {(rsi_d<=30).sum()}")
print(f"  RSI>=70天数: {(rsi_d>=70).sum()}")

print("\n策略信号（40天窗口）:")
fm = generate_fm_signals(df)
xmm = generate_xmm_signals(df)
print(f"融合模型 BUY: {sum(1 for s in fm if s.raw_signal=='BUY')} / {len(fm)}")
print(f"融合模型 SELL: {sum(1 for s in fm if s.raw_signal=='SELL')} / {len(fm)}")
print(f"XMM系统 BUY: {sum(1 for s in xmm if s.action=='BUY')} / {len(xmm)}")
print(f"XMM系统 SELL: {sum(1 for s in xmm if s.action=='SELL')} / {len(xmm)}")

print("\n融合打分（全部40天）:")
for s in fm:
    flag = " <<<" if abs(s.score) > 3 else ""
    print(f"  {s.date}  score={s.score:+.1f}  signal={s.raw_signal}  "
          f"wRSI={s.weekly_rsi:.1f}{flag}")
