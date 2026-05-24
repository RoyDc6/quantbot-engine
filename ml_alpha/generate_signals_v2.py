"""
生产级选股信号生成器 v2.1 - 修复版
- 正确计算 weekly_rsi 和 resonance
- 三市场统一信号框架
"""

import sys, io, warnings, os
import numpy as np
import pandas as pd
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
warnings.filterwarnings('ignore')

# ── RSI 计算函数 ─────────────────────────────────────────
def _rsi(p, n=14):
    """标准RSI计算 - 修复版"""
    d = np.diff(p); g = np.where(d>0, d, 0.0); l_ = np.where(d<0, -d, 0.0)
    r = np.full(len(p), 50.0)
    if len(p) <= n: return r
    ag, al = np.mean(g[:n]), np.mean(l_[:n])
    r[n] = 50.0 if al<1e-10 else 100-100/(1+ag/(al+1e-10))
    for i in range(n+1, len(p)):
        ag = (ag*(n-1)+g[i-1])/n
        al = (al*(n-1)+l_[i-1])/n
        r[i] = 50.0 if al<1e-10 else 100-100/(1+ag/(al+1e-10))
    return r

def get_weekly_rsi(close, period=4):
    """获取最新周的RSI值（基于日历周重采样，不再假设5天=1周）"""
    n = len(close)
    if n < 30:
        return 50.0
    # 用日历周重采样（周五收盘）
    import pandas as pd
    idx = pd.date_range(end=pd.Timestamp.today(), periods=n, freq='B')
    s = pd.Series(close, index=idx[:n])
    weekly = s.resample('W-FRI').last().dropna()
    if len(weekly) < period + 1:
        return 50.0
    wrsi = _rsi(weekly.values, period)
    return wrsi[-1]

def get_monthly_rsi(close, period=3):
    """获取最新月的RSI值"""
    n = len(close)
    # 合成月线（取每月20日收盘）
    monthly_closes = [close[i*20+19] for i in range(n//20) if i*20+19 < n]
    if len(monthly_closes) < period + 1:
        return 50.0
    mrsi = _rsi(np.array(monthly_closes), period)
    return mrsi[-1]

def compute_resonance(rsi_d, rsi_w, rsi_m):
    """计算共振因子"""
    return (1 if rsi_d<30 else -1 if rsi_d>70 else 0) + \
           (0.5 if rsi_w<30 else -0.5 if rsi_w>70 else 0) + \
           (0.25 if rsi_m<30 else -0.25 if rsi_m>70 else 0)

print("="*70)
print("生产级选股信号生成器 v2.1")
print(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("="*70)

# ── 因子权重 ───────────────────────────────────────────
FACTOR_WEIGHTS = {
    'weekly_rsi': 0.202,
    'resonance': -0.152,
    'frac_balance_10': 0.107,
    'monthly_rsi': 0.094,
    'frac_balance_20': 0.088,
    'rsi_14': 0.029,
}

# ── 数据源配置 ────────────────────────────────────────
MARKETS = {
    'HK': r'E:\quant\ml_alpha\hk_features_full.csv',
    'CSI300': r'E:\quant\ml_alpha\csi300_features_raw.csv',
    'SPX': r'E:\quant\ml_alpha\spx_features_raw.csv',
}

all_signals = []

for market, path in MARKETS.items():
    print(f"\n{'='*70}")
    print(f"[{market}] 处理市场...")
    print("="*70)
    
    if not os.path.exists(path):
        print(f"  文件不存在: {path}")
        continue
    
    df = pd.read_csv(path)
    df['trade_date'] = pd.to_datetime(df['trade_date'], errors='coerce')
    df = df.dropna(subset=['trade_date'])
    
    latest_date = df['trade_date'].max()
    print(f"  数据范围: {df['trade_date'].min().date()} ~ {latest_date.date()}")
    print(f"  总股票数: {df['symbol'].nunique()}")
    
    # 计算新因子
    print(f"  计算 weekly_rsi 和 resonance...")
    signals = []
    
    for sym, grp in df.groupby('symbol'):
        grp = grp.sort_values('trade_date')
        c = grp['close'].values.astype(np.float64)
        n = len(c)
        if n < 60: continue
        
        # 计算多周期RSI
        rsi_d = _rsi(c, 14)[-1]
        rsi_w = get_weekly_rsi(c, 4)
        rsi_m = get_monthly_rsi(c, 3)
        resonance = compute_resonance(rsi_d, rsi_w, rsi_m)
        
        # 获取其他因子
        row = {'symbol': sym, 'market': market, 'trade_date': latest_date,
               'close': float(c[-1]), 'weekly_rsi': float(rsi_w), 
               'monthly_rsi': float(rsi_m), 'resonance': float(resonance),
               'rsi_14': float(rsi_d)}
        
        for col in ['frac_balance_10', 'frac_balance_20', 'vol_regime', 'atr_ratio']:
            if col in grp.columns:
                row[col] = float(grp[col].values[-1])
        
        signals.append(row)
    
    if signals:
        mkt_df = pd.DataFrame(signals)
        print(f"  计算完成: {len(mkt_df)} 只股票")
        
        # 统计新因子分布
        print(f"\n  [{market}] 因子分布:")
        print(f"    weekly_rsi: {mkt_df['weekly_rsi'].min():.1f} ~ {mkt_df['weekly_rsi'].max():.1f}, 均值={mkt_df['weekly_rsi'].mean():.1f}")
        print(f"    resonance: {mkt_df['resonance'].min():+.2f} ~ {mkt_df['resonance'].max():+.2f}, 均值={mkt_df['resonance'].mean():+.2f}")
        
        # 计算融合得分
        def norm(s):
            mean, std = s.mean(), s.std()
            if std < 1e-10: return pd.Series(0, index=s.index)
            return (s - mean) / std
        
        mkt_df['fusion_score'] = 0
        for factor, weight in FACTOR_WEIGHTS.items():
            if factor in mkt_df.columns:
                mkt_df['fusion_score'] += norm(mkt_df[factor]) * weight
        
        # Top10
        print(f"\n  [{market}] Top 10 买入信号:")
        top10 = mkt_df.nlargest(10, 'fusion_score')
        print(f"    {'Rank':>4} {'Symbol':>12} {'Score':>8} {'weekly_rsi':>10} {'resonance':>10} {'Price':>10}")
        print("    " + "-"*60)
        for i, (_, row) in enumerate(top10.iterrows(), 1):
            print(f"    {i:>4} {row['symbol']:>12} {row['fusion_score']:>+8.3f} "
                  f"{row['weekly_rsi']:>10.1f} {row['resonance']:>+10.2f} {row['close']:>10.2f}")
        
        # 周线超卖
        oversold = mkt_df[mkt_df['weekly_rsi'] < 40].nlargest(5, 'fusion_score')
        if len(oversold) > 0:
            print(f"\n  [{market}] 周线RSI超卖 (<40):")
            for _, row in oversold.iterrows():
                print(f"    {row['symbol']:>12} RSI={row['weekly_rsi']:.1f} Score={row['fusion_score']:+.3f}")
        
        # 共振买入
        res_buy = mkt_df[mkt_df['resonance'] > 0.5].nlargest(5, 'fusion_score')
        if len(res_buy) > 0:
            print(f"\n  [{market}] 共振买入信号:")
            for _, row in res_buy.iterrows():
                print(f"    {row['symbol']:>12} Res={row['resonance']:+.2f} Score={row['fusion_score']:+.3f}")
        
        all_signals.append(mkt_df)

# ── 跨市场汇总 ────────────────────────────────────────
if all_signals:
    print(f"\n{'='*70}")
    print("[CROSS-MARKET] 三市场融合信号")
    print("="*70)
    
    combined = pd.concat(all_signals, ignore_index=True)
    
    print(f"\n市场分布:")
    for mkt in combined['market'].unique():
        mkt_df = combined[combined['market'] == mkt]
        print(f"  {mkt}: {len(mkt_df)} 只, 平均得分 {mkt_df['fusion_score'].mean():+.3f}")
    
    print(f"\n跨市场 Top 20:")
    top20 = combined.nlargest(20, 'fusion_score')
    print(f"  {'Rank':>4} {'Mkt':>8} {'Symbol':>12} {'Score':>8} {'weekly_rsi':>10} {'resonance':>10}")
    print("  " + "-"*60)
    for i, (_, row) in enumerate(top20.iterrows(), 1):
        print(f"  {i:>4} {row['market']:>8} {row['symbol']:>12} {row['fusion_score']:>+8.3f} "
              f"{row['weekly_rsi']:>10.1f} {row['resonance']:>+10.2f}")
    
    # 策略信号
    print(f"\n策略信号汇总:")
    
    # 1. 周线超卖反弹
    weekly_oversold = combined[combined['weekly_rsi'] < 35].nlargest(10, 'fusion_score')
    print(f"\n  [策略1] 周线超卖反弹 (weekly_rsi < 35):")
    for _, row in weekly_oversold.iterrows():
        print(f"    {row['market']:>8} {row['symbol']:>12} RSI={row['weekly_rsi']:.1f}")
    
    # 2. 共振买入
    resonance_buy = combined[combined['resonance'] > 1].nlargest(10, 'fusion_score')
    print(f"\n  [策略2] 共振买入 (resonance > 1):")
    for _, row in resonance_buy.iterrows():
        print(f"    {row['market']:>8} {row['symbol']:>12} Res={row['resonance']:+.2f}")
    
    # 3. 融合高分
    print(f"\n  [策略3] 融合高分 Top 10:")
    for _, row in combined.nlargest(10, 'fusion_score').iterrows():
        print(f"    {row['market']:>8} {row['symbol']:>12} Score={row['fusion_score']:+.3f}")
    
    # 保存
    today = datetime.now().strftime('%Y%m%d')
    out_dir = r'E:\quant\ml_alpha\signals'
    os.makedirs(out_dir, exist_ok=True)
    
    combined.to_csv(f'{out_dir}\\cross_market_signal_{today}.csv',
                    index=False, encoding='utf-8-sig')
    print(f"\n[保存] {out_dir}\\cross_market_signal_{today}.csv")

print("\n" + "="*70)
print("信号生成完成!")
print("="*70)
