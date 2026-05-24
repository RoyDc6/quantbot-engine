"""诊断融合框架：为何收益这么高？仓位分析"""
import pandas as pd, numpy as np, os, warnings
warnings.filterwarnings('ignore')
os.chdir(r'E:\quant\fusion_framework')

# 重新加载和计算
df = pd.read_csv('E:/quant/ml_alpha/hk_features_full.csv')
df['trade_date'] = pd.to_datetime(df['trade_date'])
df = df[df['volume'] > 0].copy()
df = df[df['close'] > 0.5].copy()

# 周RSI
weekly_list = []
for sym, g in df.groupby('symbol'):
    g = g.sort_values('trade_date')
    close = g['close'].values
    n = len(close)
    wrsi = np.full(n, np.nan)
    weekly_close = [close[i*5+4] for i in range(n//5) if i*5+4 < n]
    weekly_rsi = np.full(len(weekly_close), 50.0)
    if len(weekly_close) >= 15:
        d = np.diff(weekly_close); g2 = np.where(d>0,d,0.0); l2 = np.where(d<0,-d,0.0)
        period = 4
        ag, al = np.mean(g2[:period]), np.mean(l2[:period])
        weekly_rsi[period] = 100-100/(1+ag/(al+1e-10)) if al>1e-10 else 100.0
        for i in range(period+1, len(weekly_close)):
            ag=(ag*(period-1)+g2[i-1])/period; al=(al*(period-1)+l2[i-1])/period
            weekly_rsi[i]=100-100/(1+ag/(al+1e-10)) if al>1e-10 else 100.0
    for i in range(n):
        w=i//5
        if w < len(weekly_rsi): wrsi[i] = weekly_rsi[w]
    tmp = g.copy(); tmp['weekly_rsi'] = wrsi
    weekly_list.append(tmp)
df = pd.concat(weekly_list, ignore_index=True)

ALL_DATES = sorted(df['trade_date'].unique())
recent_dates = ALL_DATES[-500:]

# 信号生成
def gen_sigs(date):
    sigs = []
    today = df[df['trade_date'] == date]
    for _, row in today.iterrows():
        sym = row['symbol']
        hist = df[(df['symbol']==sym)&(df['trade_date']<=date)].tail(60)
        if len(hist) < 60: continue
        rsi_w = row.get('weekly_rsi', 50.0)
        rsi_d = row.get('rsi_14', 50.0)
        macd_h = row.get('macd_hist', 0.0)
        ma20r = row.get('ma20_ratio', 1.0)
        ma60r = row.get('ma60_ratio', 1.0)
        resonance = row.get('resonance', 0.0)
        for v in [rsi_w, rsi_d, macd_h, ma20r, ma60r, resonance]:
            if np.isnan(v): v = 0.0
        score = 0.202*(rsi_w-50) + (-0.152)*resonance
        trend_up = ma20r > 1.0 and ma60r > 1.0
        fm_sig = 'BUY' if score >= 5 else ('SELL' if score <= -5 else 'HOLD')
        above20 = ma20r > 1.0; above60 = ma60r > 1.0; macd_pos = macd_h > 0
        if above20 and above60 and macd_pos and rsi_d < 70:
            xmm_sig = 'BUY'
        elif rsi_d >= 78 or (not above20 and rsi_d > 65):
            xmm_sig = 'SELL'
        else:
            xmm_sig = 'HOLD'
        # 趋势自适应
        if trend_up:
            matrix = {
                ('BUY','BUY'):('STRONG_BUY',0.80),('BUY','HOLD'):('BUY',0.60),('BUY','SELL'):('BUY',0.60),
                ('HOLD','BUY'):('BUY',0.60),('HOLD','HOLD'):('HOLD',0.0),('HOLD','SELL'):('HOLD',0.0),
                ('SELL','BUY'):('BUY',0.60),('SELL','HOLD'):('HOLD',0.0),('SELL','SELL'):('SELL',0.80),
            }
        else:
            matrix = {
                ('BUY','BUY'):('STRONG_BUY',0.80),('BUY','HOLD'):('BUY',0.60),('BUY','SELL'):('REDUCED',0.20),
                ('HOLD','BUY'):('HOLD',0.0),('HOLD','HOLD'):('HOLD',0.0),('HOLD','SELL'):('SELL',0.60),
                ('SELL','BUY'):('REDUCED',0.20),('SELL','HOLD'):('SELL',0.60),('SELL','SELL'):('STRONG_SELL',0.80),
            }
        level_str, pos = matrix.get((fm_sig, xmm_sig), ('HOLD', 0.0))
        sigs.append({
            'symbol': sym, 'date': date, 'close': row['close'],
            'future_ret_5d': row.get('future_ret_5d', np.nan),
            'fm_sig': fm_sig, 'xmm_sig': xmm_sig, 'score': score,
            'rsi_w': rsi_w, 'trend_up': trend_up, 'level': level_str, 'position': pos,
        })
    return sigs

# 持仓分析
print("="*60)
print("融合框架持仓诊断")
print("="*60)
all_sigs = []
for date in recent_dates[-60:]:  # 最近60天
    sigs = gen_sigs(date)
    for s in sigs:
        all_sigs.append(s)

sig_df = pd.DataFrame(all_sigs)
buy_df = sig_df[sig_df['level'].isin(['BUY','STRONG_BUY'])]
print(f"总信号: {len(sig_df)}条, BUY: {len(buy_df)}条")
print(f"日均BUY: {len(buy_df)/60:.1f}只")

# 每次持仓多少天
print(f"\n每次BUY持续天数:")
buy_dates = {}
for _, row in buy_df.iterrows():
    sym = row['symbol']
    if sym not in buy_dates:
        buy_dates[sym] = []
    buy_dates[sym].append(row['date'])

# 换手率
total_days = 60
total_buys = len(buy_df)
daily_buys = len(buy_df) / total_days
print(f"  日均持仓: {daily_buys:.1f}只")
print(f"  总BUY信号: {total_buys}次 / {total_days}天")
print(f"  日均换手: ~{daily_buys:.1f}只 (假设日均再平衡)")

# 看看收益来源
# 分组看：高周RSI vs 低周RSI
buy_clean = buy_df.dropna(subset=['future_ret_5d'])
print(f"\nBUY信号收益 (n={len(buy_clean)}):")
print(f"  平均5日收益: {buy_clean['future_ret_5d'].mean()*100:.3f}%")
print(f"  胜率: {(buy_clean['future_ret_5d']>0).mean()*100:.1f}%")
print(f"  平均wrsi: {buy_clean['rsi_w'].mean():.1f}")
print(f"  trend_up比例: {(buy_clean['trend_up']).mean()*100:.0f}%")

# 按wrsi分组
for lo, hi in [(0,40),(40,50),(50,60),(60,70),(70,100)]:
    sub = buy_clean[(buy_clean['rsi_w']>=lo)&(buy_clean['rsi_w']<hi)]
    if len(sub) > 0:
        print(f"  wrsi[{lo:>3}-{hi:>3}]: n={len(sub):>4}  ret={sub['future_ret_5d'].mean()*100:+.3f}%  胜率={(sub['future_ret_5d']>0).mean()*100:.0f}%")

# 收益分解
print(f"\n收益分解:")
n_days = 500
avg_ret = buy_clean['future_ret_5d'].mean()
avg_n = daily_buys
# 日收益 = avg_ret / 5 * avg_n_stocks * position_fraction
# 假设每只股票权重 = 0.8 / n_stocks
avg_n_stocks = daily_buys
daily_ret = avg_ret / 5 * min(avg_n_stocks, 10) * 0.8  # 最多10只
annual_trade_days = 250
print(f"  日均BUY股票数: {avg_n:.1f}")
print(f"  平均5日收益: {avg_ret*100:.3f}%")
print(f"  估算日组合收益: {daily_ret*100:.4f}%")
print(f"  估算年化收益: {((1+daily_ret)**annual_trade_days-1)*100:.1f}%")

# 考虑交易成本
# HK: 佣金0.03% + 印花税0.1% + 滑点0.05% = ~0.2% per trade
# 日换手: avg_n只 * 2 = ~10只/天
daily_cost = avg_n * 2 * 0.002
net_daily_ret = daily_ret - daily_cost
print(f"\n含交易成本(0.2%/笔):")
print(f"  日交易成本: ~{daily_cost*100:.3f}%")
print(f"  净日收益: {net_daily_ret*100:.4f}%")
print(f"  净年化: {((1+net_daily_ret)**annual_trade_days-1)*100:.1f}%")
print(f"  净Sharpe: {net_daily_ret/(abs(net_daily_ret)*0.5+1e-10)*16:.2f}")  # 简化

# 基准对比
print(f"\n对比基准:")
print(f"  等权持有HK: ~+65% (500天)")
print(f"  融合框架毛收益: ~+1173% (可能高估)")
print(f"  融合框架净收益: ~年化{((1+net_daily_ret)**250-1)*100:.0f}%")
