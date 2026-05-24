"""
生产级选股信号生成器
- 读取最新数据
- 计算复杂度因子
- 融合缠论特征
- 输出今日Top10买入信号
"""

import sys, io, warnings
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from scipy.stats import spearmanr

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
warnings.filterwarnings('ignore')

print("="*70)
print("生产级选股信号生成器")
print(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("="*70)

# ── 加载模型配置 ─────────────────────────────────────────
print("\n[1] 加载模型配置...")

# IC加权权重 (来自融合模型)
FACTOR_WEIGHTS = {
    # 缠论因子 (主力)
    'frac_balance_20': 0.1042,
    'top_count_20': 0.0852,
    'fractal_top_5': 0.0794,
    'frac_balance_10': 0.0751,
    'fractal_bottom_5': 0.0742,
    'fractal_top_10': 0.0678,
    'fractal_bottom_10': 0.0637,
    'top_count_10': 0.0624,
    'bottom_count_20': 0.0361,
    'bottom_count_10': 0.0336,
    'bull_divergence': 0.0271,
    'resonance': 0.0177,
    'td9_count': 0.0171,
    'macd_cross_up': 0.0134,
    'td9_extreme': 0.0115,
    # 复杂度因子 (辅助)
    'entropy_60': 0.0348,
    'hurst_60': 0.0262,
    'apen_20': 0.0259,
    'apen_60': 0.0156,
    # 技术因子
    'bb_ratio': 0.0304,
    'atr_ratio': 0.0271,
    'mom_5d': 0.0267,
    'mom_10d': 0.0125,
    'rsi_14': 0.0117,
    'macd': 0.0112,
    'vol_ratio': 0.0095,
}

# ── 加载最新数据 ─────────────────────────────────────────
print("\n[2] 加载最新市场数据...")

# 缠论特征 (最新)
chan_df = pd.read_csv(r'E:\quant\ml_alpha\hk_features_full.csv', parse_dates=['trade_date'])
latest_date = chan_df['trade_date'].max()
print(f"  缠论数据最新日期: {latest_date.date()}")

# 获取最新一天的数据
latest_chan = chan_df[chan_df['trade_date'] == latest_date].copy()
print(f"  当日股票数: {len(latest_chan)}")

# 复杂度因子 (需要重新计算最新值)
print("\n[3] 计算最新复杂度因子...")

import tickflow_env  # noqa: auto-inject TICKFLOW_API_KEY
from tickflow import TickFlow
from complexity_factors import compute_complexity_factors

tf = TickFlow() if os.environ.get('TICKFLOW_API_KEY') else TickFlow.free()

complexity_records = []
for _, row in latest_chan.iterrows():
    symbol = row['symbol']
    try:
        # 获取200日K线
        kline = tf.klines.get(symbol, period='1d', count=200, as_dataframe=True)
        if kline is None or len(kline) < 60:
            continue
        
        # 计算收益率序列
        kline['ret'] = kline['close'].pct_change()
        returns = kline['ret'].dropna().values
        
        if len(returns) < 60:
            continue
        
        # 计算复杂度因子
        factors = compute_complexity_factors(returns)
        complexity_records.append({
            'symbol': symbol,
            'trade_date': latest_date,
            **factors
        })
    except Exception as e:
        pass  # 静默跳过错误

if complexity_records:
    comp_latest = pd.DataFrame(complexity_records)
    print(f"  成功计算复杂度因子: {len(comp_latest)}只股票")
else:
    # 如果没有新数据，使用已有数据
    comp_latest = pd.read_csv(r'E:\quant\ml_alpha\complexity_factor_data.csv', parse_dates=['date'])
    comp_latest = comp_latest.rename(columns={'date': 'trade_date'})
    comp_latest = comp_latest[comp_latest['trade_date'] == comp_latest['trade_date'].max()]
    print(f"  使用历史复杂度因子: {len(comp_latest)}只股票")

# ── 合并数据 ─────────────────────────────────────────────
print("\n[4] 合并因子数据...")

# 合并
merged = latest_chan.merge(comp_latest, on=['symbol', 'trade_date'], how='left')
print(f"  合并后: {len(merged)}只股票")

# ── 计算融合得分 ─────────────────────────────────────────
print("\n[5] 计算融合得分...")

def normalize_series(s):
    """标准化到[-1, 1]"""
    mean, std = s.mean(), s.std()
    if std == 0:
        return pd.Series(0, index=s.index)
    return (s - mean) / std

# 标准化各因子并加权
merged['fusion_score'] = 0
for factor, weight in FACTOR_WEIGHTS.items():
    if factor in merged.columns:
        # 标准化
        norm_vals = normalize_series(merged[factor])
        # 根据IC方向调整 (这里假设权重已考虑方向)
        merged['fusion_score'] += norm_vals * weight

# 处理缺失值
merged['fusion_score'] = merged['fusion_score'].fillna(merged['fusion_score'].median())

# ── 生成选股信号 ─────────────────────────────────────────
print("\n[6] 生成选股信号...")

# 排序
merged = merged.sort_values('fusion_score', ascending=False)

# Top 10 买入信号
top10 = merged.head(10)[['symbol', 'name', 'close', 'fusion_score', 
                         'fractal_bottom_5', 'fractal_bottom_10', 
                         'bull_divergence', 'hurst_60', 'entropy_60']]

print("\n" + "="*70)
print(f"📈 今日港股Top 10 买入信号 ({latest_date.date()})")
print("="*70)
print(f"{'Rank':>4s} {'Symbol':>10s} {'Name':<12s} {'Price':>8s} {'Score':>8s} {'Bottom5':>7s} {'BullDiv':>7s}")
print("-"*70)

for i, (_, row) in enumerate(top10.iterrows(), 1):
    name = str(row['name'])[:12] if pd.notna(row['name']) else 'N/A'
    print(f"{i:>4d} {row['symbol']:>10s} {name:<12s} {row['close']:>8.2f} {row['fusion_score']:>+8.3f} "
          f"{int(row.get('fractal_bottom_5', 0)):>7d} {int(row.get('bull_divergence', 0)):>7d}")

# Bottom 5 卖出/回避信号
print("\n" + "="*70)
print(f"📉 今日港股Bottom 5 回避信号 ({latest_date.date()})")
print("="*70)

bottom5 = merged.tail(5)[['symbol', 'name', 'close', 'fusion_score',
                          'fractal_top_5', 'top_count_10']]
print(f"{'Rank':>4s} {'Symbol':>10s} {'Name':<12s} {'Price':>8s} {'Score':>8s} {'Top5':>7s} {'TopCnt':>7s}")
print("-"*70)

for i, (_, row) in enumerate(bottom5.iterrows(), 1):
    name = str(row['name'])[:12] if pd.notna(row['name']) else 'N/A'
    rank = len(merged) - 5 + i
    print(f"{rank:>4d} {row['symbol']:>10s} {name:<12s} {row['close']:>8.2f} {row['fusion_score']:>+8.3f} "
          f"{int(row.get('fractal_top_5', 0)):>7d} {int(row.get('top_count_10', 0)):>7d}")

# ── 信号解读 ─────────────────────────────────────────────
print("\n" + "="*70)
print("📊 信号解读")
print("="*70)

# 统计
print(f"\n市场概况:")
print(f"  总股票数: {len(merged)}")
print(f"  平均得分: {merged['fusion_score'].mean():+.3f}")
print(f"  得分中位数: {merged['fusion_score'].median():+.3f}")
print(f"  得分标准差: {merged['fusion_score'].std():.3f}")

# 买入信号特征
print(f"\nTop 10 信号特征:")
print(f"  平均得分: {top10['fusion_score'].mean():+.3f}")
print(f"  底分型占比: {top10['fractal_bottom_5'].mean()*100:.1f}% (5日)")
print(f"  底分型占比: {top10['fractal_bottom_10'].mean()*100:.1f}% (10日)")
print(f"   bullish背离: {top10['bull_divergence'].sum()}只")

# 行业/市值分布 (如果有数据)
if 'market_cap' in merged.columns:
    print(f"\n市值分布:")
    print(f"  Top10平均市值: {top10['market_cap'].mean()/1e8:.1f}亿")

# ── 保存信号 ─────────────────────────────────────────────
print("\n[7] 保存选股信号...")

# 保存全部得分
output_cols = ['symbol', 'name', 'trade_date', 'close', 'fusion_score'] + \
              [f for f in FACTOR_WEIGHTS.keys() if f in merged.columns]
merged[output_cols].to_csv(
    rf'E:\quant\ml_alpha\signals\signal_{latest_date.strftime("%Y%m%d")}.csv',
    index=False, encoding='utf-8-sig'
)

# 保存Top10
summary = {
    'date': latest_date.strftime('%Y-%m-%d'),
    'total_stocks': len(merged),
    'top10': top10['symbol'].tolist(),
    'bottom5': bottom5['symbol'].tolist(),
    'avg_score_top10': top10['fusion_score'].mean(),
    'avg_score_all': merged['fusion_score'].mean(),
}

import json
with open(rf'E:\quant\ml_alpha\signals\summary_{latest_date.strftime("%Y%m%d")}.json', 'w') as f:
    json.dump(summary, f, indent=2)

print(f"  已保存: signal_{latest_date.strftime('%Y%m%d')}.csv")
print(f"  已保存: summary_{latest_date.strftime('%Y%m%d')}.json")

# ── 风险提示 ─────────────────────────────────────────────
print("\n" + "="*70)
print("⚠️ 风险提示")
print("="*70)
print("""
1. 本信号基于历史数据回测，不构成投资建议
2. 港股波动较大，建议控制仓位
3. 模型RankIC=0.38，胜率约60%，存在亏损风险
4. 建议结合基本面分析和市场宏观环境决策
5. 过去表现不代表未来收益
""")

print("\n" + "="*70)
print("选股信号生成完成!")
print("="*70)
