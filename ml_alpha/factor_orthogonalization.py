# -*- coding: utf-8 -*-
"""
因子正交化研究 — P0-1
目标: 降低缠论因子集中度至≤50%，提升模型鲁棒性
方法:
  1. 诊断当前权重分布
  2. 按因子来源分组，约束组权重上限
  3. 引入 L2 正则化避免单因子过拟合
  4. 对比约束前后 RankIC / Spread / 月度稳定性
"""

import sys, io, warnings
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from scipy.optimize import minimize

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
warnings.filterwarnings('ignore')

# ── 加载数据 ─────────────────────────────────────────────
print("=" * 70)
print("因子正交化研究 — 降低缠论集中度")
print("=" * 70)

comp_df = pd.read_csv(r'E:\quant\ml_alpha\complexity_factor_data.csv', parse_dates=['date'])
comp_df = comp_df.rename(columns={'date': 'trade_date'})

chan_df = pd.read_csv(r'E:\quant\ml_alpha\hk_features_full.csv', parse_dates=['trade_date'])

chan_df['symbol'] = chan_df['symbol'].str.strip()
comp_df['symbol'] = comp_df['symbol'].str.strip()

merged = comp_df.merge(chan_df, on=['symbol', 'trade_date'], how='inner', suffixes=('', '_chan'))
print(f"\n合并数据: {len(merged)}条, {merged['symbol'].nunique()}只股票")
print(f"日期范围: {merged['trade_date'].min().date()} ~ {merged['trade_date'].max().date()}")

# ── 因子分类 ─────────────────────────────────────────────
complexity_factors = ['apen_60', 'hurst_60', 'apen_20', 'entropy_60', 'higuchi_fd_60']
chan_factors = [
    'fractal_top_5', 'fractal_bottom_5', 'fractal_top_10', 'fractal_bottom_10',
    'top_count_10', 'bottom_count_10', 'frac_balance_10',
    'top_count_20', 'bottom_count_20', 'frac_balance_20',
    'td9_count', 'td9_buy_zone', 'td9_sell_zone', 'td9_extreme',
    'resonance', 'bull_divergence', 'macd_bull', 'macd_cross_up'
]
tech_factors = ['rsi_14', 'macd', 'atr_ratio', 'bb_ratio', 'vol_ratio', 'mom_5d', 'mom_10d']

# 只保留存在的因子
chan_factors = [f for f in chan_factors if f in merged.columns]
tech_factors = [f for f in tech_factors if f in merged.columns]
complexity_factors = [f for f in complexity_factors if f in merged.columns]

all_factors = complexity_factors + chan_factors + tech_factors
print(f"\n因子总数: {len(all_factors)}")
print(f"  缠论: {len(chan_factors)}")
print(f"  复杂度: {len(complexity_factors)}")
print(f"  技术: {len(tech_factors)}")

# ── 1. 单因子IC ─────────────────────────────────────────
print("\n" + "=" * 70)
print("1. 单因子RankIC分析")
print("=" * 70)

def calc_ic(df, factor, label='future_5d'):
    valid = df[[factor, label]].dropna()
    if len(valid) < 30:
        return np.nan, 0
    ic, _ = spearmanr(valid[factor], valid[label])
    return ic, len(valid)

ic_results = []
for f in all_factors:
    ic, n = calc_ic(merged, f)
    group = '复杂度' if f in complexity_factors else ('缠论' if f in chan_factors else '技术')
    ic_results.append({'factor': f, 'group': group, 'ic': ic, 'abs_ic': abs(ic), 'n': n})

ic_df = pd.DataFrame(ic_results).sort_values('abs_ic', ascending=False)

print(f"\n{'因子':25s} {'组':6s} {'RankIC':>10s} {'|IC|':>8s}")
print("-" * 55)
for _, row in ic_df.head(20).iterrows():
    print(f"{row['factor']:25s} {row['group']:6s} {row['ic']:+10.4f} {row['abs_ic']:>8.4f}")

# ── 2. 权重分布诊断 ──────────────────────────────────────
print("\n" + "=" * 70)
print("2. 权重分布诊断（IC加权模型）")
print("=" * 70)

# 筛选强因子
strong_factors = ic_df[ic_df['abs_ic'] > 0.02]['factor'].tolist()
print(f"\n强因子: {len(strong_factors)}个")

# 标准化
def normalize_factor(df, factor, target_ic):
    vals = df[factor].values.astype(float)
    mean, std = np.nanmean(vals), np.nanstd(vals)
    if std == 0:
        return np.zeros(len(vals))
    normalized = (vals - mean) / std
    if target_ic < 0:
        normalized = -normalized
    return normalized

factor_ics = {row['factor']: row['ic'] for _, row in ic_df.iterrows() if row['factor'] in strong_factors}

for f in strong_factors:
    merged[f'{f}_norm'] = normalize_factor(merged, f, factor_ics.get(f, 0))

# 当前IC加权模型
weights_current = {f: abs(factor_ics.get(f, 0)) for f in strong_factors}
wsum = sum(weights_current.values())
weights_current = {f: w / wsum for f, w in weights_current.items()}

# 按组统计
group_weights = {'缠论': 0, '复杂度': 0, '技术': 0}
for f, w in weights_current.items():
    if f in chan_factors:
        group_weights['缠论'] += w
    elif f in complexity_factors:
        group_weights['复杂度'] += w
    else:
        group_weights['技术'] += w

print(f"\n当前IC加权模型 — 组权重分布:")
for g, w in group_weights.items():
    bar = "█" * int(w * 100)
    print(f"  {g}: {w:.1%} {bar}")

print(f"\n风险评估: 缠论因子占比 {group_weights['缠论']:.1%}")
if group_weights['缠论'] > 0.6:
    print("  ⚠️  集中度风险: 缠论因子权重 >60%，模型本质上是缠论投票器")

# 当前模型IC
norm_cols = [f'{f}_norm' for f in strong_factors]
merged['current_score'] = sum(merged[f'{f}_norm'] * w for f, w in weights_current.items())
current_ic, _ = spearmanr(merged['current_score'], merged['future_5d'])

# 分层
merged['current_group'] = pd.qcut(merged['current_score'].rank(method='first'), 5, labels=False, duplicates='drop')
current_spread = merged[merged['current_group'] == 4]['future_5d'].mean() - merged[merged['current_group'] == 0]['future_5d'].mean()

print(f"\n当前模型: RankIC={current_ic:+.4f}, Spread={current_spread*100:+.3f}%")

# ── 3. 约束优化模型 ──────────────────────────────────────
print("\n" + "=" * 70)
print("3. 约束优化模型（组权重上限 = 50%）")
print("=" * 70)

# 准备数据
factor_matrix = merged[[f'{f}_norm' for f in strong_factors]].values
label = merged['future_5d'].values
valid_mask = ~np.isnan(factor_matrix).any(axis=1) & ~np.isnan(label)
X = factor_matrix[valid_mask]
y = label[valid_mask]
n_factors = X.shape[1]

# 因子分组索引
chan_idx = [i for i, f in enumerate(strong_factors) if f in chan_factors]
comp_idx = [i for i, f in enumerate(strong_factors) if f in complexity_factors]
tech_idx = [i for i, f in enumerate(strong_factors) if f in tech_factors]

# 优化目标: 最大化 RankIC (即最小化负RankIC)
def neg_rank_ic(w):
    score = X @ w
    # Spearman rank correlation (approximate via Pearson on ranks)
    from scipy.stats import rankdata
    rank_score = rankdata(score)
    rank_y = rankdata(y)
    corr = np.corrcoef(rank_score, rank_y)[0, 1]
    return -corr  # minimize negative = maximize

# 约束条件
constraints = [
    {'type': 'eq', 'fun': lambda w: np.sum(w) - 1},  # 权重和=1
    {'type': 'ineq', 'fun': lambda w: 0.50 - np.sum(w[chan_idx])},  # 缠论≤50%
    {'type': 'ineq', 'fun': lambda w: 0.50 - np.sum(w[comp_idx]) if len(comp_idx) > 0 else 1},  # 复杂度≤50%
    {'type': 'ineq', 'fun': lambda w: 0.50 - np.sum(w[tech_idx]) if len(tech_idx) > 0 else 1},  # 技术≤50%
]

# L2正则化 (通过修改目标函数)
def neg_rank_ic_l2(w, lam=0.01):
    return neg_rank_ic(w) + lam * np.sum(w ** 2)

# 初始等权
w0 = np.ones(n_factors) / n_factors

# 边界: 所有权重 >= 0
bounds = [(0, 1)] * n_factors

# 优化
print("\n正在优化...")
result = minimize(
    neg_rank_ic_l2, w0, method='SLSQP',
    bounds=bounds, constraints=constraints,
    options={'maxiter': 1000, 'ftol': 1e-10}
)

if result.success:
    w_opt = result.x
    print(f"优化成功: {'是' if result.success else '否'}, 迭代次数: {result.nit}")
else:
    print(f"优化未完全收敛, 使用当前解")
    w_opt = result.x

# 归一化
w_opt = np.maximum(w_opt, 0)
w_opt = w_opt / w_opt.sum()

# 新模型权重
weights_new = {f: w_opt[i] for i, f in enumerate(strong_factors)}

# 新模型组权重
group_new = {'缠论': 0, '复杂度': 0, '技术': 0}
for f, w in weights_new.items():
    if f in chan_factors:
        group_new['缠论'] += w
    elif f in complexity_factors:
        group_new['复杂度'] += w
    else:
        group_new['技术'] += w

print(f"\n约束优化模型 — 组权重分布:")
for g, w in group_new.items():
    bar = "█" * int(w * 100)
    print(f"  {g}: {w:.1%} {bar}")

# 新模型IC
merged['new_score'] = sum(merged[f'{f}_norm'] * w for f, w in weights_new.items())
new_ic, _ = spearmanr(merged['new_score'], merged['future_5d'])
merged['new_group'] = pd.qcut(merged['new_score'].rank(method='first'), 5, labels=False, duplicates='drop')
new_spread = merged[merged['new_group'] == 4]['future_5d'].mean() - merged[merged['new_group'] == 0]['future_5d'].mean()

print(f"\n约束模型: RankIC={new_ic:+.4f}, Spread={new_spread*100:+.3f}%")

# ── 4. 对比分析 ──────────────────────────────────────────
print("\n" + "=" * 70)
print("4. 对比分析: 旧模型 vs 约束模型")
print("=" * 70)

print(f"\n{'指标':20s} {'旧模型(IC加权)':>15s} {'约束模型(≤50%)':>15s} {'变化':>10s}")
print("-" * 65)
print(f"{'RankIC':20s} {current_ic:+15.4f} {new_ic:+15.4f} {(new_ic-current_ic)*100:+9.2f}%")
print(f"{'Spread(Q5-Q1)':20s} {current_spread*100:+14.3f}% {new_spread*100:+14.3f}% {(new_spread-current_spread)*100:+9.3f}%")
print(f"{'缠论权重':20s} {group_weights['缠论']:14.1%} {group_new['缠论']:14.1%} {(group_new['缠论']-group_weights['缠论'])*100:+9.1f}pp")
print(f"{'复杂度权重':20s} {group_weights['复杂度']:14.1%} {group_new['复杂度']:14.1%} {(group_new['复杂度']-group_weights['复杂度'])*100:+9.1f}pp")
print(f"{'技术权重':20s} {group_weights['技术']:14.1%} {group_new['技术']:14.1%} {(group_new['技术']-group_weights['技术'])*100:+9.1f}pp")

# ── 5. 月度稳定性对比 ────────────────────────────────────
print("\n" + "=" * 70)
print("5. 月度IC稳定性对比")
print("=" * 70)

merged['year_month'] = merged['trade_date'].dt.to_period('M')

monthly_old, monthly_new = [], []
for ym, group in merged.groupby('year_month'):
    if len(group) < 30:
        continue
    ic_old, _ = spearmanr(group['current_score'], group['future_5d'])
    ic_new, _ = spearmanr(group['new_score'], group['future_5d'])
    monthly_old.append({'month': str(ym), 'ic': ic_old})
    monthly_new.append({'month': str(ym), 'ic': ic_new})

m_old = pd.DataFrame(monthly_old)
m_new = pd.DataFrame(monthly_new)

print(f"\n{'月份':12s} {'旧模型IC':>12s} {'新模型IC':>12s} {'差值':>10s}")
print("-" * 50)
for i in range(min(len(m_old), len(m_new))):
    diff = m_new.iloc[i]['ic'] - m_old.iloc[i]['ic']
    marker = "↑" if diff > 0 else "↓"
    print(f"{m_old.iloc[i]['month']:12s} {m_old.iloc[i]['ic']:+12.4f} {m_new.iloc[i]['ic']:+12.4f} {diff:+9.4f} {marker}")

print(f"\n{'统计':12s} {'旧模型':>12s} {'新模型':>12s}")
print("-" * 40)
print(f"{'均值IC':12s} {m_old['ic'].mean():+12.4f} {m_new['ic'].mean():+12.4f}")
print(f"{'IC标准差':12s} {m_old['ic'].std():12.4f} {m_new['ic'].std():12.4f}")
print(f"{'月胜率':12s} {(m_old['ic']>0).mean()*100:11.1f}% {(m_new['ic']>0).mean()*100:11.1f}%")
print(f"{'IC_IR':12s} {m_old['ic'].mean()/m_old['ic'].std() if m_old['ic'].std()>0 else 0:+12.4f} {m_new['ic'].mean()/m_new['ic'].std() if m_new['ic'].std()>0 else 0:+12.4f}")

# ── 6. 因子权重详情 ──────────────────────────────────────
print("\n" + "=" * 70)
print("6. 因子权重详情")
print("=" * 70)

print(f"\n{'因子':25s} {'组':6s} {'旧权重':>10s} {'新权重':>10s} {'变化':>10s}")
print("-" * 65)
for f in strong_factors:
    old_w = weights_current.get(f, 0)
    new_w = weights_new.get(f, 0)
    g = '缠论' if f in chan_factors else ('复杂度' if f in complexity_factors else '技术')
    diff = new_w - old_w
    marker = "↑" if diff > 0.01 else ("↓" if diff < -0.01 else "")
    print(f"{f:25s} {g:6s} {old_w:10.4f} {new_w:10.4f} {diff:+9.4f} {marker}")

# ── 7. 保存结果 ──────────────────────────────────────────
print("\n" + "=" * 70)
print("7. 保存结果")
print("=" * 70)

# 保存权重
weight_df = pd.DataFrame([
    {
        'factor': f,
        'group': '缠论' if f in chan_factors else ('复杂度' if f in complexity_factors else '技术'),
        'old_weight': weights_current.get(f, 0),
        'new_weight': weights_new.get(f, 0),
        'ic': factor_ics.get(f, 0),
    }
    for f in strong_factors
])
weight_df.to_csv(r'E:\quant\ml_alpha\orthogonal_weights.csv', index=False, encoding='utf-8-sig')

# 保存预测结果
merged[['symbol', 'trade_date', 'future_5d', 'current_score', 'new_score'] + strong_factors].to_csv(
    r'E:\quant\ml_alpha\orthogonal_predictions.csv', index=False, encoding='utf-8-sig')

print("\n已保存:")
print("  - orthogonal_weights.csv (权重对比)")
print("  - orthogonal_predictions.csv (预测结果)")

# 总结
print("\n" + "=" * 70)
print("总结")
print("=" * 70)
improvement = new_ic - current_ic
if improvement > 0:
    print(f"✅ 约束模型 RankIC 提升 {improvement*100:+.2f}%")
elif improvement > -0.005:
    print(f"⚖️  约束模型 RankIC 变化 {improvement*100:+.2f}%（可接受范围）")
else:
    print(f"⚠️  约束模型 RankIC 下降 {improvement*100:.2f}%（需进一步调优）")

print(f"✅ 缠论因子集中度: {group_weights['缠论']:.1%} → {group_new['缠论']:.1%}")
print(f"✅ 复杂度因子权重: {group_weights['复杂度']:.1%} → {group_new['复杂度']:.1%}")
print(f"✅ IC_IR: {m_old['ic'].mean()/m_old['ic'].std():.3f} → {m_new['ic'].mean()/m_new['ic'].std():.3f}")

print("\n" + "=" * 70)
print("因子正交化研究完成!")
print("=" * 70)
