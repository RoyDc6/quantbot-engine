"""诊断：融合打分公式方向测试"""
import pandas as pd, numpy as np
from scipy.stats import spearmanr
import os, warnings
warnings.filterwarnings('ignore')

df = pd.read_csv('E:/quant/ml_alpha/hk_features_full.csv')
df = df[df['volume'] > 0].copy()
df['trade_date'] = pd.to_datetime(df['trade_date'])

# 加载周RSI（从之前计算结果）
# 直接用全部数据的weekly_rsi_IC=+0.2156和resonance_IC=-0.023
# 对应原始公式: 0.202*(50-wrsi) = 0.202*50 - 0.202*wrsi = 正 - 0.202*wrsi
# IC(融合) = IC(0.202*50) + IC(-0.202*wrsi) + IC(-0.152*resonance)
#          = 0 + (-0.202)*IC(wrsi) + (-0.152)*IC(resonance)
#          = (-0.202)*0.2156 + (-0.152)*(-0.023)
#          = -0.0436 + 0.0035 = -0.0401 → 负！这就是-0.2045的原因

# 正确的融合公式应该是：
# A: 0.202*(wrsi-50) - 0.152*resonance
#    IC = 0.202*0.2156 + (-0.152)*(-0.023) = 0.0436 + 0.0035 = 0.0471 → 正！
# B: 0.202*(50-wrsi) - 0.152*resonance  (原错误公式)
#    IC = 0.202*(-0.2156) + 0.0035 = -0.0401 → 负！

print("理论推导:")
print("A: 0.202*(wrsi-50) - 0.152*resonance → IC理论=+0.0471")
print("B: 0.202*(50-wrsi) - 0.152*resonance → IC理论=-0.0401")

# 验证：用实际数据计算IC
recent = df[df['trade_date'] > '2025-01-01'].copy()
recent = recent.dropna(subset=['rsi_14', 'future_ret_5d', 'resonance'])
print(f"\n验证数据: {len(recent)}行")

# rsi_14 IC
ic_rsi, p1 = spearmanr(recent['rsi_14'], recent['future_ret_5d'])
print(f"rsi_14 IC={ic_rsi:+.4f}")

# resonance IC
ic_res, p2 = spearmanr(recent['resonance'], recent['future_ret_5d'])
print(f"resonance IC={ic_res:+.4f}")

# 融合打分A (wrsi方向, 用rsi_14代理wrsi)
recent['score_A'] = 0.202 * (recent['rsi_14'] - 50) + (-0.152) * recent['resonance']
ic_A, pA = spearmanr(recent['score_A'], recent['future_ret_5d'])
print(f"\n公式A: 0.202*(rsi-50) - 0.152*res → IC={ic_A:+.4f} {'***' if pA<0.001 else '**' if pA<0.01 else '*' if pA<0.05 else ''}")

# 融合打分B (旧公式)
recent['score_B'] = 0.202 * (50 - recent['rsi_14']) + (-0.152) * recent['resonance']
ic_B, pB = spearmanr(recent['score_B'], recent['future_ret_5d'])
print(f"公式B: 0.202*(50-rsi) - 0.152*res → IC={ic_B:+.4f} {'***' if pB<0.001 else '**' if pB<0.01 else '*' if pB<0.05 else ''}")

# 纯周RSI IC
recent2 = recent.copy()
recent2['weekly_ret'] = recent2.groupby('symbol')['close'].pct_change(5)
recent2 = recent2.dropna(subset=['weekly_ret'])
ic_w, pw = spearmanr(recent2['weekly_ret'], recent2['future_ret_5d'])
print(f"\n5日动量 IC={ic_w:+.4f} (p={pw:.4f})")

# 简单单因子：5日负收益后买入（超卖）
recent2['mom_score'] = -recent2['weekly_ret']  # 过去5日跌得多 → 买入
ic_m, pm = spearmanr(recent2['mom_score'], recent2['future_ret_5d'])
print(f"超卖打分 IC={ic_m:+.4f} (p={pm:.4f}) {'***' if pm<0.001 else ''}")

print("\n结论:")
if ic_A > ic_B:
    print("✓ 公式A正确 (wrsi方向)")
else:
    print("✓ 公式B正确 (50-wrsi方向)")
