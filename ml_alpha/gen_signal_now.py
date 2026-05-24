# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import pandas as pd, numpy as np, json, warnings
warnings.filterwarnings('ignore')

FACTOR_WEIGHTS = {
    'frac_balance_20': 0.1042, 'top_count_20': 0.0852, 'fractal_top_5': 0.0794,
    'frac_balance_10': 0.0751, 'fractal_bottom_5': 0.0742, 'fractal_top_10': 0.0678,
    'fractal_bottom_10': 0.0637, 'top_count_10': 0.0624, 'bottom_count_20': 0.0361,
    'bottom_count_10': 0.0336, 'bull_divergence': 0.0271, 'resonance': 0.0177,
    'td9_count': 0.0171, 'macd_cross_up': 0.0134, 'td9_extreme': 0.0115,
    'entropy_60': 0.0348, 'hurst_60': 0.0262, 'apen_20': 0.0259, 'apen_60': 0.0156,
    'bb_ratio': 0.0304, 'atr_ratio': 0.0271, 'mom_5d': 0.0267, 'mom_10d': 0.0125,
    'rsi_14': 0.0117, 'macd': 0.0112, 'vol_ratio': 0.0095,
}

chan_df = pd.read_csv(r'E:\quant\ml_alpha\hk_features_full.csv', parse_dates=['trade_date'])
latest_date = chan_df['trade_date'].max()
latest = chan_df[chan_df['trade_date'] == latest_date].copy()

try:
    comp_df = pd.read_csv(r'E:\quant\ml_alpha\complexity_factor_data.csv', parse_dates=['date'])
    comp_latest = comp_df.rename(columns={'date': 'trade_date'})
    comp_latest = comp_latest[comp_latest['trade_date'] == comp_latest['trade_date'].max()]
    comp_cols = ['symbol','trade_date','hurst_60','apen_20','apen_60','entropy_60']
    comp_merge = comp_latest[[c for c in comp_cols if c in comp_latest.columns]]
    latest = latest.merge(comp_merge, on=['symbol','trade_date'], how='left')
except:
    pass

def normalize_series(s):
    s = s.astype(float)
    mean, std = s.mean(), s.std()
    if std == 0 or pd.isna(std):
        return pd.Series(0.0, index=s.index)
    return (s - mean) / std

score = np.zeros(len(latest))
for factor, weight in FACTOR_WEIGHTS.items():
    if factor in latest.columns:
        norm_vals = normalize_series(latest[factor])
        score += norm_vals.values * weight

latest['fusion_score'] = score
latest = latest.sort_values('fusion_score', ascending=False).reset_index(drop=True)

top10 = latest.head(10)
bottom5 = latest.tail(5)

date_str = latest_date.strftime('%Y-%m-%d')

sep = '=' * 70
dash = '-' * 70

print(sep)
print('港股选股信号报告 (数据截止: {})'.format(date_str))
print('生成时间: 2026-04-22 10:00 CST')
print(sep)

print()
print('--- Top 10 买入信号 ---')
header = '{:>4} {:>12} {:<12} {:>8} {:>8} {:>4} {:>5} {:>6} {:>4}'.format(
    'Rank', 'Code', 'Name', 'Price', 'Score', 'FB5', 'FB10', 'BullD', 'TD9')
print(header)
print(dash)
for i, (_, r) in enumerate(top10.iterrows(), 1):
    nm = str(r['name'])[:12] if pd.notna(r['name']) else 'N/A'
    line = '{:>4} {:>12} {:<12} {:>8.2f} {:>+8.3f} {:>4} {:>5} {:>6} {:>4}'.format(
        i, r['symbol'], nm, float(r['close']), float(r['fusion_score']),
        int(r.get('fractal_bottom_5', 0)), int(r.get('fractal_bottom_10', 0)),
        int(r.get('bull_divergence', 0)), int(r.get('td9_count', 0)))
    print(line)

print()
print('--- Bottom 5 回避信号 ---')
header2 = '{:>4} {:>12} {:<12} {:>8} {:>8} {:>4} {:>5}'.format(
    'Rank', 'Code', 'Name', 'Price', 'Score', 'FT5', 'TC10')
print(header2)
print(dash)
for i, (_, r) in enumerate(bottom5.iterrows(), 1):
    nm = str(r['name'])[:12] if pd.notna(r['name']) else 'N/A'
    rank = len(latest) - 5 + i
    line = '{:>4} {:>12} {:<12} {:>8.2f} {:>+8.3f} {:>4} {:>5}'.format(
        rank, r['symbol'], nm, float(r['close']), float(r['fusion_score']),
        int(r.get('fractal_top_5', 0)), int(r.get('top_count_10', 0)))
    print(line)

print()
print('--- 市场概况 ---')
print('扫描股票数: {}'.format(len(latest)))
print('平均得分: {:+.3f}'.format(float(latest['fusion_score'].mean())))
print('得分中位: {:+.3f}'.format(float(latest['fusion_score'].median())))
print('得分标准差: {:.3f}'.format(float(latest['fusion_score'].std())))
pos = int((latest['fusion_score'] > 0).sum())
neg = int((latest['fusion_score'] < 0).sum())
total = len(latest)
print('正信号: {} ({:.0f}%)  负信号: {} ({:.0f}%)'.format(pos, pos/total*100, neg, neg/total*100))

print()
print('--- Top10信号特征 ---')
print('平均融合得分: {:+.3f}'.format(float(top10['fusion_score'].mean())))
print('5日底分型均值: {:.2f}'.format(float(top10['fractal_bottom_5'].mean())))
print('底背离出现: {}只'.format(int(top10['bull_divergence'].sum())))
print('TD9序列均值: {:.1f}'.format(float(top10['td9_count'].mean())))
if 'frac_balance_20' in top10.columns:
    t10v = float(top10['frac_balance_20'].mean())
    allv = float(latest['frac_balance_20'].mean())
    print('20日分型平衡: {:.3f} (全市场: {:.3f})'.format(t10v, allv))
if 'bb_ratio' in top10.columns:
    t10v = float(top10['bb_ratio'].mean())
    allv = float(latest['bb_ratio'].mean())
    print('布林位置: {:.3f} (全市场: {:.3f})'.format(t10v, allv))
if 'rsi_14' in top10.columns:
    t10v = float(top10['rsi_14'].mean())
    allv = float(latest['rsi_14'].mean())
    print('RSI14: {:.1f} (全市场: {:.1f})'.format(t10v, allv))
if 'mom_5d' in top10.columns:
    t10v = float(top10['mom_5d'].mean())
    allv = float(latest['mom_5d'].mean())
    print('5日动量: {:.3f} (全市场: {:.3f})'.format(t10v, allv))

# Save
out_path = r'E:\quant\ml_alpha\signals\signal_{}.csv'.format(latest_date.strftime('%Y%m%d'))
out_cols = ['symbol','name','trade_date','close','fusion_score'] + [f for f in FACTOR_WEIGHTS.keys() if f in latest.columns]
latest[out_cols].to_csv(out_path, index=False, encoding='utf-8-sig')
print()
print('信号已保存: {}'.format(out_path))

# Save summary JSON
summary = {
    'date': latest_date.strftime('%Y-%m-%d'),
    'total_stocks': int(len(latest)),
    'top10_symbols': top10['symbol'].tolist(),
    'bottom5_symbols': bottom5['symbol'].tolist(),
    'avg_score_top10': float(top10['fusion_score'].mean()),
    'avg_score_all': float(latest['fusion_score'].mean()),
    'median_score': float(latest['fusion_score'].median()),
    'positive_count': pos,
    'negative_count': neg,
}
summary_path = r'E:\quant\ml_alpha\signals\summary_{}.json'.format(latest_date.strftime('%Y%m%d'))
with open(summary_path, 'w', encoding='utf-8') as f:
    json.dump(summary, f, indent=2, ensure_ascii=False)
print('摘要已保存: {}'.format(summary_path))

print(sep)
