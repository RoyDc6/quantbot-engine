# -*- coding: utf-8 -*-
"""
因子正交化 V2 — PCA降维 + 组约束优化
核心思路: 缠论18因子高度相关 → PCA压缩为3-5个正交主成分
         → 与复杂度/技术因子无组内相关
         → 组约束下最大化RankIC

比V1简单砍权聪明: 保留信息，释放权重
"""

import sys, io, warnings
import numpy as np
import pandas as pd
from scipy.stats import spearmanr, rankdata
from scipy.optimize import minimize

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
warnings.filterwarnings('ignore')

# ── 加载数据 ─────────────────────────────────────────────
print("=" * 70)
print("因子正交化 V2 — PCA降维 + 组约束优化")
print("=" * 70)

comp_df = pd.read_csv(r'E:\quant\ml_alpha\complexity_factor_data.csv', parse_dates=['date'])
comp_df = comp_df.rename(columns={'date': 'trade_date'})

chan_df = pd.read_csv(r'E:\quant\ml_alpha\hk_features_full.csv', parse_dates=['trade_date'])

chan_df['symbol'] = chan_df['symbol'].str.strip()
comp_df['symbol'] = comp_df['symbol'].str.strip()

merged = comp_df.merge(chan_df, on=['symbol', 'trade_date'], how='inner', suffixes=('', '_chan'))
print(f"\n合并数据: {len(merged)}条, {merged['symbol'].nunique()}只股票")

# ── 因子定义 ─────────────────────────────────────────────
complexity_factors = ['apen_60', 'hurst_60', 'apen_20', 'entropy_60', 'higuchi_fd_60']
chan_factors_list = [
    'fractal_top_5', 'fractal_bottom_5', 'fractal_top_10', 'fractal_bottom_10',
    'top_count_10', 'bottom_count_10', 'frac_balance_10',
    'top_count_20', 'bottom_count_20', 'frac_balance_20',
    'td9_count', 'td9_buy_zone', 'td9_sell_zone', 'td9_extreme',
    'resonance', 'bull_divergence', 'macd_bull', 'macd_cross_up'
]
tech_factors_list = ['rsi_14', 'macd', 'atr_ratio', 'bb_ratio', 'vol_ratio', 'mom_5d', 'mom_10d']

chan_factors = [f for f in chan_factors_list if f in merged.columns]
tech_factors = [f for f in tech_factors_list if f in merged.columns]
complexity_factors = [f for f in complexity_factors if f in merged.columns]

print(f"\n原始因子: 缠论{len(chan_factors)} + 复杂度{len(complexity_factors)} + 技术{len(tech_factors)} = {len(chan_factors)+len(complexity_factors)+len(tech_factors)}")

# ── Step 1: 缠论因子PCA ─────────────────────────────────
print("\n" + "=" * 70)
print("Step 1: 缠论因子PCA降维")
print("=" * 70)

chan_data = merged[chan_factors].values.astype(float)
chan_valid = ~np.isnan(chan_data).any(axis=1)

# 标准化
chan_mean = np.nanmean(chan_data[chan_valid], axis=0)
chan_std = np.nanstd(chan_data[chan_valid], axis=0)
chan_std[chan_std == 0] = 1
chan_normalized = (chan_data[chan_valid] - chan_mean) / chan_std

# PCA (手工实现，不依赖sklearn)
cov_matrix = np.cov(chan_normalized.T)
eigenvalues, eigenvectors = np.linalg.eigh(cov_matrix)

# 按特征值降序排列
idx = np.argsort(eigenvalues)[::-1]
eigenvalues = eigenvalues[idx]
eigenvectors = eigenvectors[:, idx]

total_var = eigenvalues.sum()
cumulative_var = np.cumsum(eigenvalues) / total_var

print(f"\n特征值分析:")
print(f"{'主成分':8s} {'特征值':>10s} {'方差占比':>10s} {'累计':>10s}")
print("-" * 42)
for i in range(min(10, len(eigenvalues))):
    print(f"PC{i+1:2d}       {eigenvalues[i]:10.3f} {eigenvalues[i]/total_var*100:9.2f}% {cumulative_var[i]*100:9.2f}%")

# 选择解释80%方差的主成分
n_components = np.searchsorted(cumulative_var, 0.80) + 1
n_components = max(3, min(n_components, 6))  # 至少3个，最多6个
print(f"\n选择主成分数: {n_components} (解释{cumulative_var[n_components-1]*100:.1f}%方差)")

# 提取主成分
components = eigenvectors[:, :n_components]
chan_pca = chan_normalized @ components  # (N, n_components)

# 保存PCA载荷矩阵（用于生产环境）
loading_df = pd.DataFrame(
    components,
    index=chan_factors,
    columns=[f'chan_PC{i+1}' for i in range(n_components)]
)
loading_df.to_csv(r'E:\quant\ml_alpha\pca_loadings.csv', encoding='utf-8-sig')
print(f"\nPCA载荷矩阵已保存: pca_loadings.csv")

# 显示各PC的主要贡献因子
print(f"\n主成分解读:")
for i in range(n_components):
    loadings = loading_df[f'chan_PC{i+1}'].abs().sort_values(ascending=False)
    top3 = loadings.head(3)
    top_str = ", ".join([f"{f}({v:.3f})" for f, v in top3.items()])
    ic = spearmanr(chan_pca[:, i], merged.loc[chan_valid, 'future_5d'].values)[0]
    print(f"  PC{i+1}: IC={ic:+.4f} | 主要因子: {top_str}")

# ── Step 2: 构建新因子矩阵 ──────────────────────────────
print("\n" + "=" * 70)
print("Step 2: 构建正交化因子矩阵")
print("=" * 70)

# 将PCA结果写回merged
pca_cols = [f'chan_PC{i+1}' for i in range(n_components)]
for i, col in enumerate(pca_cols):
    merged[col] = np.nan
    merged.loc[chan_valid, col] = chan_pca[:, i]

# 新因子列表: PCA chan + 复杂度 + 技术
new_factors = pca_cols + complexity_factors + tech_factors
print(f"\n正交化因子: PCA{len(pca_cols)} + 复杂度{len(complexity_factors)} + 技术{len(tech_factors)} = {len(new_factors)}")

# ── Step 3: 单因子IC ────────────────────────────────────
print("\n" + "=" * 70)
print("Step 3: 正交化因子IC分析")
print("=" * 70)

ic_results = []
for f in new_factors:
    valid = merged[[f, 'future_5d']].dropna()
    if len(valid) < 30:
        continue
    ic, _ = spearmanr(valid[f], valid['future_5d'])
    g = 'PCA-Chan' if f.startswith('chan_PC') else ('复杂度' if f in complexity_factors else '技术')
    ic_results.append({'factor': f, 'group': g, 'ic': ic, 'abs_ic': abs(ic), 'n': len(valid)})

ic_df = pd.DataFrame(ic_results).sort_values('abs_ic', ascending=False)

print(f"\n{'因子':20s} {'组':10s} {'RankIC':>10s} {'|IC|':>8s}")
print("-" * 52)
for _, row in ic_df.iterrows():
    print(f"{row['factor']:20s} {row['group']:10s} {row['ic']:+10.4f} {row['abs_ic']:>8.4f}")

# ── Step 4: IC加权融合 ──────────────────────────────────
print("\n" + "=" * 70)
print("Step 4: IC加权融合（无约束基线）")
print("=" * 70)

strong_factors = ic_df[ic_df['abs_ic'] > 0.02]['factor'].tolist()
print(f"\n强因子: {len(strong_factors)}个")

factor_ics = {row['factor']: row['ic'] for _, row in ic_df.iterrows() if row['factor'] in strong_factors}

# 标准化
def normalize_factor(series):
    vals = series.values.astype(float)
    mean, std = np.nanmean(vals), np.nanstd(vals)
    if std == 0: return np.zeros(len(vals))
    return (vals - mean) / std

for f in strong_factors:
    merged[f'{f}_norm'] = normalize_factor(merged[f])

# IC加权
weights_raw = {f: abs(factor_ics.get(f, 0)) for f in strong_factors}
ws = sum(weights_raw.values())
weights_raw = {f: w / ws for f, w in weights_raw.items()}

# 组权重
group_raw = {'PCA-Chan': 0, '复杂度': 0, '技术': 0}
for f, w in weights_raw.items():
    if f.startswith('chan_PC'): group_raw['PCA-Chan'] += w
    elif f in complexity_factors: group_raw['复杂度'] += w
    else: group_raw['技术'] += w

print(f"\n无约束基线 — 组权重:")
for g, w in group_raw.items():
    bar = "█" * int(w * 100)
    print(f"  {g}: {w:.1%} {bar}")

norm_cols = [f'{f}_norm' for f in strong_factors]
merged['raw_score'] = sum(merged[f'{f}_norm'] * w for f, w in weights_raw.items())
raw_ic, _ = spearmanr(merged['raw_score'], merged['future_5d'])

merged['raw_q'] = pd.qcut(merged['raw_score'].rank(method='first'), 5, labels=False, duplicates='drop')
raw_spread = merged[merged['raw_q'] == 4]['future_5d'].mean() - merged[merged['raw_q'] == 0]['future_5d'].mean()

print(f"\n无约束基线: RankIC={raw_ic:+.4f}, Spread={raw_spread*100:+.3f}%")

# ── Step 5: 组约束优化 ──────────────────────────────────
print("\n" + "=" * 70)
print("Step 5: 组约束优化（各组≤50%）")
print("=" * 70)

# 因子索引分组
pca_idx = [i for i, f in enumerate(strong_factors) if f.startswith('chan_PC')]
comp_idx = [i for i, f in enumerate(strong_factors) if f in complexity_factors]
tech_idx = [i for i, f in enumerate(strong_factors) if f in tech_factors]

# 准备数据
factor_matrix = merged[[f'{f}_norm' for f in strong_factors]].values
label = merged['future_5d'].values
valid_mask = ~np.isnan(factor_matrix).any(axis=1) & ~np.isnan(label)
X = factor_matrix[valid_mask]
y = label[valid_mask]
n_factors = X.shape[1]

def neg_rank_ic(w):
    score = X @ w
    rank_score = rankdata(score)
    rank_y = rankdata(y)
    return -np.corrcoef(rank_score, rank_y)[0, 1]

constraints = [
    {'type': 'eq', 'fun': lambda w: np.sum(w) - 1},
    {'type': 'ineq', 'fun': lambda w: 0.50 - np.sum(w[pca_idx])},
    {'type': 'ineq', 'fun': lambda w: 0.50 - np.sum(w[comp_idx]) if len(comp_idx) > 0 else 1},
    {'type': 'ineq', 'fun': lambda w: 0.50 - np.sum(w[tech_idx]) if len(tech_idx) > 0 else 1},
]

w0 = np.ones(n_factors) / n_factors
bounds = [(0, 1)] * n_factors

print("\n正在优化...")
result = minimize(
    neg_rank_ic, w0, method='SLSQP',
    bounds=bounds, constraints=constraints,
    options={'maxiter': 2000, 'ftol': 1e-12}
)
print(f"优化{'成功' if result.success else '未完全收敛'}, 迭代{result.nit}次")

w_opt = np.maximum(result.x, 0)
w_opt = w_opt / w_opt.sum()

weights_constrained = {f: w_opt[i] for i, f in enumerate(strong_factors)}

group_constrained = {'PCA-Chan': 0, '复杂度': 0, '技术': 0}
for f, w in weights_constrained.items():
    if f.startswith('chan_PC'): group_constrained['PCA-Chan'] += w
    elif f in complexity_factors: group_constrained['复杂度'] += w
    else: group_constrained['技术'] += w

print(f"\n约束优化 — 组权重:")
for g, w in group_constrained.items():
    bar = "█" * int(w * 100)
    print(f"  {g}: {w:.1%} {bar}")

merged['constrained_score'] = sum(merged[f'{f}_norm'] * w for f, w in weights_constrained.items())
con_ic, _ = spearmanr(merged['constrained_score'], merged['future_5d'])
merged['con_q'] = pd.qcut(merged['constrained_score'].rank(method='first'), 5, labels=False, duplicates='drop')
con_spread = merged[merged['con_q'] == 4]['future_5d'].mean() - merged[merged['con_q'] == 0]['future_5d'].mean()

print(f"\n约束优化: RankIC={con_ic:+.4f}, Spread={con_spread*100:+.3f}%")

# ── Step 6: vs 旧模型对比 ───────────────────────────────
print("\n" + "=" * 70)
print("Step 6: 三模型对比")
print("=" * 70)

# 旧模型 (原始缠论IC加权)
weights_old = {}
old_strong = [f for f in ic_df[ic_df['abs_ic'] > 0.02]['factor'].tolist() if not f.startswith('chan_PC')]

# 用原始数据重建旧模型IC
old_chan = chan_factors + complexity_factors + tech_factors
old_ic_results = []
for f in old_chan:
    valid = merged[[f, 'future_5d']].dropna()
    if len(valid) < 30: continue
    ic, _ = spearmanr(valid[f], valid['future_5d'])
    old_ic_results.append({'factor': f, 'ic': ic, 'abs_ic': abs(ic)})

old_ic_df = pd.DataFrame(old_ic_results)
old_strong = old_ic_df[old_ic_df['abs_ic'] > 0.02]['factor'].tolist()
old_factor_ics = {row['factor']: row['ic'] for _, row in old_ic_df.iterrows() if row['factor'] in old_strong}

for f in old_strong:
    merged[f'old_{f}_norm'] = normalize_factor(merged[f])

old_weights = {f: abs(old_factor_ics.get(f, 0)) for f in old_strong}
ows = sum(old_weights.values())
old_weights = {f: w / ows for f, w in old_weights.items()}

merged['old_score'] = sum(merged[f'old_{f}_norm'] * w for f, w in old_weights.items())
old_ic_val, _ = spearmanr(merged['old_score'], merged['future_5d'])
merged['old_q'] = pd.qcut(merged['old_score'].rank(method='first'), 5, labels=False, duplicates='drop')
old_spread = merged[merged['old_q'] == 4]['future_5d'].mean() - merged[merged['old_q'] == 0]['future_5d'].mean()

# 旧模型组权重
old_gw = {'缠论': 0, '复杂度': 0, '技术': 0}
for f, w in old_weights.items():
    if f in chan_factors: old_gw['缠论'] += w
    elif f in complexity_factors: old_gw['复杂度'] += w
    else: old_gw['技术'] += w

print(f"\n{'模型':25s} {'RankIC':>10s} {'Spread':>10s} {'缠论/PCA':>10s} {'复杂度':>8s} {'技术':>8s}")
print("-" * 75)
print(f"{'旧模型(原始缠论IC加权)':25s} {old_ic_val:+10.4f} {old_spread*100:+9.3f}% {old_gw['缠论']:9.1%} {old_gw['复杂度']:7.1%} {old_gw['技术']:7.1%}")
print(f"{'V1(简单50%砍权)':25s} {'+0.3194':>10s} {'+4.407%':>10s} {'50.1%':>10s} {'22.1%':>8s} {'27.8%':>8s}")
print(f"{'V2(PCA+约束优化)':25s} {con_ic:+10.4f} {con_spread*100:+9.3f}% {group_constrained['PCA-Chan']:9.1%} {group_constrained['复杂度']:7.1%} {group_constrained['技术']:7.1%}")
print(f"{'V2(无约束基线)':25s} {raw_ic:+10.4f} {raw_spread*100:+9.3f}% {group_raw['PCA-Chan']:9.1%} {group_raw['复杂度']:7.1%} {group_raw['技术']:7.1%}")

# ── Step 7: 月度稳定性 ──────────────────────────────────
print("\n" + "=" * 70)
print("Step 7: 月度IC稳定性对比")
print("=" * 70)

merged['year_month'] = merged['trade_date'].dt.to_period('M')

m_old, m_new, m_raw = [], [], []
for ym, group in merged.groupby('year_month'):
    if len(group) < 20: continue
    ic_o, _ = spearmanr(group['old_score'], group['future_5d'])
    ic_c, _ = spearmanr(group['constrained_score'], group['future_5d'])
    ic_r, _ = spearmanr(group['raw_score'], group['future_5d'])
    m_old.append(ic_o); m_new.append(ic_c); m_raw.append(ic_r)

def stats(arr):
    m = np.mean(arr); s = np.std(arr); wr = np.mean(np.array(arr) > 0)
    return m, s, wr, m/s if s > 0 else 0

mo, so, wro, iro = stats(m_old)
mn, sn, wrn, irn = stats(m_new)
mr, sr, wrr, irr = stats(m_raw)

print(f"\n{'统计':12s} {'旧模型':>12s} {'PCA无约束':>12s} {'PCA约束':>12s}")
print("-" * 52)
print(f"{'均值IC':12s} {mo:+12.4f} {mr:+12.4f} {mn:+12.4f}")
print(f"{'IC标准差':12s} {so:12.4f} {sr:12.4f} {sn:12.4f}")
print(f"{'月胜率':12s} {wro*100:11.1f}% {wrr*100:11.1f}% {wrn*100:11.1f}%")
print(f"{'IC_IR':12s} {iro:+12.4f} {irr:+12.4f} {irn:+12.4f}")

# ── Step 8: 推荐方案 ────────────────────────────────────
print("\n" + "=" * 70)
print("Step 8: 推荐方案")
print("=" * 70)

# 选择IC_IR最高且组集中度<=50%的方案
candidates = [
    ('旧模型', old_ic_val, old_spread, iro, old_gw['缠论'], old_weights),
    ('PCA无约束', raw_ic, raw_spread, irr, group_raw['PCA-Chan'], weights_raw),
    ('PCA约束', con_ic, con_spread, irn, group_constrained['PCA-Chan'], weights_constrained),
]

# 优先选IC_IR > 1.2 且集中度 < 55% 的
best = None
for name, ic, spread, icir, conc, w in candidates:
    if conc <= 0.55 and icir > 1.0:
        if best is None or icir > best[4]:
            best = (name, ic, spread, w, icir, conc)

if best:
    print(f"\n推荐: {best[0]}")
    print(f"  RankIC={best[1]:+.4f}, Spread={best[2]*100:+.3f}%, IC_IR={best[4]:.3f}")
    print(f"  缠论集中度={best[5]:.1%}")
else:
    print(f"\n⚠️  所有约束方案IC_IR均低于1.0，建议保持旧模型但增加风控")

# 保存权重
weight_rows = []
for f in strong_factors:
    g = 'PCA-Chan' if f.startswith('chan_PC') else ('复杂度' if f in complexity_factors else '技术')
    weight_rows.append({
        'factor': f, 'group': g,
        'ic': factor_ics.get(f, 0),
        'raw_weight': weights_raw.get(f, 0),
        'constrained_weight': weights_constrained.get(f, 0),
    })
pd.DataFrame(weight_rows).to_csv(r'E:\quant\ml_alpha\pca_orthogonal_weights.csv', index=False, encoding='utf-8-sig')

# 保存预测
merged[['symbol', 'trade_date', 'future_5d', 'old_score', 'raw_score', 'constrained_score']].to_csv(
    r'E:\quant\ml_alpha\pca_orthogonal_predictions.csv', index=False, encoding='utf-8-sig')

print("\n已保存:")
print("  - pca_loadings.csv (PCA载荷矩阵，用于生产)")
print("  - pca_orthogonal_weights.csv (权重对比)")
print("  - pca_orthogonal_predictions.csv (预测结果)")

print("\n" + "=" * 70)
print("PCA因子正交化研究完成!")
print("=" * 70)
