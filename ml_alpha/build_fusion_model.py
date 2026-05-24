"""
复杂度 + 缠论 融合模型 (无sklearn版本)
- 合并两类因子
- 计算交叉IC
- 构建多因子打分模型
- 分层回测验证
"""

import sys, io, warnings
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
warnings.filterwarnings('ignore')

# ── 加载数据 ─────────────────────────────────────────────
print("="*70)
print("复杂度 + 缠论 融合模型构建 (纯Python)")
print("="*70)

# 复杂度因子
comp_df = pd.read_csv(r'E:\quant\ml_alpha\complexity_factor_data.csv', parse_dates=['date'])
comp_df = comp_df.rename(columns={'date': 'trade_date'})
print(f"\n复杂度因子: {len(comp_df)}条, {comp_df['symbol'].nunique()}只股票")

# 缠论特征
chan_df = pd.read_csv(r'E:\quant\ml_alpha\hk_features_full.csv', parse_dates=['trade_date'])
print(f"缠论特征: {len(chan_df)}条, {chan_df['symbol'].nunique()}只股票")

# ── 合并 ─────────────────────────────────────────────────
chan_df['symbol'] = chan_df['symbol'].str.strip()
comp_df['symbol'] = comp_df['symbol'].str.strip()

merged = comp_df.merge(
    chan_df, 
    on=['symbol', 'trade_date'], 
    how='inner',
    suffixes=('', '_chan')
)
print(f"\n合并后: {len(merged)}条, {merged['symbol'].nunique()}只股票")
print(f"日期范围: {merged['trade_date'].min().date()} ~ {merged['trade_date'].max().date()}")

# ── 定义因子列表 ─────────────────────────────────────────
complexity_factors = ['apen_60', 'hurst_60', 'apen_20', 'entropy_60', 'higuchi_fd_60']

chan_factors = [
    'fractal_top_5', 'fractal_bottom_5', 'fractal_top_10', 'fractal_bottom_10',
    'top_count_10', 'bottom_count_10', 'frac_balance_10',
    'top_count_20', 'bottom_count_20', 'frac_balance_20',
    'td9_count', 'td9_buy_zone', 'td9_sell_zone', 'td9_extreme',
    'resonance', 'bull_divergence',
    'macd_bull', 'macd_cross_up'
]

tech_factors = ['rsi_14', 'macd', 'atr_ratio', 'bb_ratio', 'vol_ratio', 'mom_5d', 'mom_10d']

chan_factors = [f for f in chan_factors if f in merged.columns]
tech_factors = [f for f in tech_factors if f in merged.columns]

all_factors = complexity_factors + chan_factors + tech_factors
print(f"\n使用因子: {len(all_factors)}个")
print(f"  复杂度: {len(complexity_factors)}个: {complexity_factors}")
print(f"  缠论: {len(chan_factors)}个: {chan_factors[:8]}...")
print(f"  技术: {len(tech_factors)}个: {tech_factors}")

# ── 1. 单因子IC分析 ──────────────────────────────────────
print("\n" + "="*70)
print("1. 单因子IC分析 (RankIC)")
print("="*70)

def calc_ic(df, factor, label='future_5d'):
    valid = df[[factor, label]].dropna()
    if len(valid) < 30:
        return np.nan, 0
    ic, _ = spearmanr(valid[factor], valid[label])
    return ic, len(valid)

ic_results = []
for f in all_factors:
    ic, n = calc_ic(merged, f)
    ic_results.append({'factor': f, 'type': 
        '复杂度' if f in complexity_factors else ('缠论' if f in chan_factors else '技术'),
        'ic': ic, 'abs_ic': abs(ic), 'n': n
    })

ic_df = pd.DataFrame(ic_results).sort_values('abs_ic', ascending=False)
print(f"\n{'Factor':25s} {'Type':8s} {'RankIC':>10s} {'|IC|':>8s} {'N':>6s}")
print("-"*70)
for _, row in ic_df.head(25).iterrows():
    stars = "⭐⭐⭐" if row['abs_ic'] > 0.05 else ("⭐⭐" if row['abs_ic'] > 0.03 else "⭐" if row['abs_ic'] > 0.02 else "")
    print(f"{row['factor']:25s} {row['type']:8s} {row['ic']:+10.4f} {row['abs_ic']:>8.4f} {row['n']:>6d} {stars}")

# ── 2. 因子间相关性分析 ──────────────────────────────────
print("\n" + "="*70)
print("2. 复杂度 vs 缠论 相关性矩阵")
print("="*70)

corr_matrix = merged[all_factors].corr()

print(f"\n{'复杂度因子':20s}", end="")
for cf in chan_factors[:8]:
    print(f"{cf[:8]:>9s}", end=" ")
print()
print("-"*100)

for f in complexity_factors:
    print(f"{f:20s}", end=" ")
    for cf in chan_factors[:8]:
        if f in corr_matrix.index and cf in corr_matrix.columns:
            corr = corr_matrix.loc[f, cf]
            marker = "*" if abs(corr) > 0.2 else ""
            print(f"{corr:+8.3f}{marker:<1s}", end=" ")
    print()

# ── 3. 等权融合模型 ──────────────────────────────────────
print("\n" + "="*70)
print("3. 等权融合模型")
print("="*70)

# 选择强因子（|IC| > 0.02）
strong_factors = ic_df[ic_df['abs_ic'] > 0.02]['factor'].tolist()
print(f"\n强因子 ({len(strong_factors)}个): {strong_factors}")

# 标准化并赋予方向
def normalize_factor(df, factor, target_ic):
    """标准化因子，IC为负则反向"""
    vals = df[factor].values
    mean, std = np.nanmean(vals), np.nanstd(vals)
    if std == 0:
        return np.zeros(len(vals))
    normalized = (vals - mean) / std
    # IC为负则反向（使高得分=高收益）
    if target_ic < 0:
        normalized = -normalized
    return normalized

# 构建等权得分
model_df = merged[strong_factors + ['future_5d', 'symbol', 'trade_date']].copy()

# 获取各因子IC方向
factor_ics = {row['factor']: row['ic'] for _, row in ic_df.iterrows() if row['factor'] in strong_factors}

# 计算标准化得分
for f in strong_factors:
    model_df[f'{f}_norm'] = normalize_factor(model_df, f, factor_ics.get(f, 0))

# 等权融合
norm_cols = [f'{f}_norm' for f in strong_factors]
model_df['equal_weight_score'] = model_df[norm_cols].mean(axis=1)

# IC验证
eq_ic, _ = spearmanr(model_df['equal_weight_score'], model_df['future_5d'])
print(f"\n等权融合模型 RankIC: {eq_ic:+.4f}")

# ── 4. IC加权融合模型 ────────────────────────────────────
print("\n" + "="*70)
print("4. IC加权融合模型")
print("="*70)

# IC加权
weights = {f: abs(factor_ics.get(f, 0)) for f in strong_factors}
weight_sum = sum(weights.values())
if weight_sum > 0:
    weights = {f: w/weight_sum for f, w in weights.items()}

print("\n因子权重:")
for f, w in sorted(weights.items(), key=lambda x: x[1], reverse=True):
    print(f"  {f:25s}: {w:.4f}")

# 计算加权得分
model_df['ic_weighted_score'] = sum(model_df[f'{f}_norm'] * w for f, w in weights.items())

# IC验证
icw_ic, _ = spearmanr(model_df['ic_weighted_score'], model_df['future_5d'])
print(f"\nIC加权融合模型 RankIC: {icw_ic:+.4f}")

# ── 5. 分层回测 ──────────────────────────────────────────
print("\n" + "="*70)
print("5. 分层回测对比")
print("="*70)

def backtest_layer(df, pred_col, label_col='future_5d', n_layers=5):
    """分层回测"""
    df = df.copy()
    df['group'] = pd.qcut(df[pred_col].rank(method='first'), n_layers, labels=False, duplicates='drop')
    
    results = []
    for g in range(n_layers):
        group_df = df[df['group'] == g]
        if len(group_df) == 0:
            continue
        avg_ret = group_df[label_col].mean()
        win_rate = (group_df[label_col] > 0).mean()
        std = group_df[label_col].std()
        results.append({
            'layer': f'Q{g+1}',
            'count': len(group_df),
            'avg_ret': avg_ret,
            'win_rate': win_rate,
            'sharpe': avg_ret / std if std > 0 else 0
        })
    return pd.DataFrame(results)

# 对比各模型
print("\n--- 单因子对比 ---")
for f in ['apen_60', 'hurst_60', 'resonance', 'bull_divergence']:
    if f in model_df.columns:
        ic, _ = spearmanr(model_df[f], model_df['future_5d'])
        layers = backtest_layer(model_df, f)
        spread = layers['avg_ret'].iloc[-1] - layers['avg_ret'].iloc[0]
        print(f"{f:20s}: RankIC={ic:+.4f}, Spread={spread*100:+.3f}%")

print("\n--- 等权融合 ---")
eq_layers = backtest_layer(model_df, 'equal_weight_score')
print(eq_layers.to_string(index=False))
eq_spread = eq_layers['avg_ret'].iloc[-1] - eq_layers['avg_ret'].iloc[0]
print(f"Spread (Q5-Q1): {eq_spread*100:+.3f}%")

print("\n--- IC加权融合 ---")
icw_layers = backtest_layer(model_df, 'ic_weighted_score')
print(icw_layers.to_string(index=False))
icw_spread = icw_layers['avg_ret'].iloc[-1] - icw_layers['avg_ret'].iloc[0]
print(f"Spread (Q5-Q1): {icw_spread*100:+.3f}%")

# ── 6. 复杂度 vs 缠论 贡献分析 ───────────────────────────
print("\n" + "="*70)
print("6. 复杂度 vs 缠论 贡献分析")
print("="*70)

comp_strong = [f for f in strong_factors if f in complexity_factors]
chan_strong = [f for f in strong_factors if f in chan_factors]
tech_strong = [f for f in strong_factors if f in tech_factors]

print(f"\n强因子分布:")
print(f"  复杂度: {len(comp_strong)}个: {comp_strong}")
print(f"  缠论: {len(chan_strong)}个: {chan_strong}")
print(f"  技术: {len(tech_strong)}个: {tech_strong}")

# 单独构建各类模型
for name, factors in [('复杂度', comp_strong), ('缠论', chan_strong), ('技术', tech_strong)]:
    if len(factors) < 2:
        continue
    # 等权
    norm_cols = [f'{f}_norm' for f in factors]
    model_df[f'{name}_score'] = model_df[norm_cols].mean(axis=1)
    ic, _ = spearmanr(model_df[f'{name}_score'], model_df['future_5d'])
    layers = backtest_layer(model_df, f'{name}_score')
    spread = layers['avg_ret'].iloc[-1] - layers['avg_ret'].iloc[0]
    print(f"\n{name}等权模型: RankIC={ic:+.4f}, Spread={spread*100:+.3f}%")

print(f"\n融合模型: RankIC={icw_ic:+.4f}, Spread={icw_spread*100:+.3f}%")

# ── 7. 时序稳定性 ────────────────────────────────────────
print("\n" + "="*70)
print("7. 融合模型时序稳定性")
print("="*70)

model_df['year_month'] = model_df['trade_date'].dt.to_period('M')
monthly_ic = []
for ym, group in model_df.groupby('year_month'):
    ic, _ = spearmanr(group['ic_weighted_score'], group['future_5d'])
    monthly_ic.append({'month': str(ym), 'ic': ic, 'n': len(group)})

monthly_df = pd.DataFrame(monthly_ic)
print(f"\n月度IC统计:")
print(f"  均值: {monthly_df['ic'].mean():+.4f}")
print(f"  标准差: {monthly_df['ic'].std():.4f}")
print(f"  胜率: {(monthly_df['ic'] > 0).mean()*100:.1f}%")
print(f"  月份数: {len(monthly_df)}")

print("\n月度IC详情:")
for _, row in monthly_df.iterrows():
    bar = "█" * int(abs(row['ic']) * 50)
    print(f"  {row['month']}: {row['ic']:+.4f} {bar}")

# ── 8. 保存结果 ──────────────────────────────────────────
print("\n" + "="*70)
print("8. 保存融合模型结果")
print("="*70)

# 保存预测结果
model_df[['symbol', 'trade_date', 'future_5d', 'equal_weight_score', 
          'ic_weighted_score'] + strong_factors].to_csv(
    r'E:\quant\ml_alpha\fusion_model_results.csv', index=False, encoding='utf-8-sig')

# 保存IC结果
ic_df.to_csv(r'E:\quant\ml_alpha\fusion_ic_analysis.csv', index=False, encoding='utf-8-sig')

# 保存月度IC
monthly_df.to_csv(r'E:\quant\ml_alpha\fusion_monthly_ic.csv', index=False, encoding='utf-8-sig')

print("\n已保存:")
print("  - fusion_model_results.csv (预测结果)")
print("  - fusion_ic_analysis.csv (因子IC分析)")
print("  - fusion_monthly_ic.csv (月度IC)")

print("\n" + "="*70)
print("融合模型构建完成!")
print("="*70)
