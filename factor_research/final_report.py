"""全量因子挖掘研究 - 最终综合报告"""
import warnings
warnings.filterwarnings('ignore')
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

def _rsi(p, n=14):
    d = np.diff(p); g = np.where(d>0, d, 0.0); l_ = np.where(d<0, -d, 0.0)
    r = np.full(len(p), 50.0)
    if len(p) <= n: return r
    ag, al = np.mean(g[:n]), np.mean(l_[:n])
    r[n-1] = 50.0 if al<1e-10 else 100-100/(1+ag/(al+1e-10))
    for i in range(n, len(p)-1):
        ag = (ag*(n-1)+g[i])/n; al = (al*(n-1)+l_[i])/n
        r[i] = 50.0 if al<1e-10 else 100-100/(1+ag/(al+1e-10))
    return r

print('='*70)
print('全量因子挖掘研究 - 综合报告')
print('='*70)

# ── HK ────────────────────────────────────────────────
df_hk = pd.read_csv(r'E:\quant\ml_alpha\hk_features_full.csv')
df_hk['trade_date'] = pd.to_datetime(df_hk['trade_date'], errors='coerce')
df_hk = df_hk.dropna(subset=['trade_date'])
df_hk = df_hk.sort_values(['symbol', 'trade_date'])
print(f'\n[HK] {len(df_hk)} rows, {df_hk["symbol"].nunique()} stocks')
print(f'  {df_hk["trade_date"].min().date()} ~ {df_hk["trade_date"].max().date()}')

# Compute multi-period RSI for HK
print('  计算 weekly/monthly RSI...')
all_rows = []
for sym, grp in df_hk.groupby('symbol'):
    grp = grp.sort_values('trade_date')
    c = grp['close'].values.astype(np.float64)
    n = len(c)
    if n < 60: continue
    rsi14 = _rsi(c, 14)
    wc = np.array([c[i*5+4] for i in range(n//5) if i*5+4 < n])
    wrsi = _rsi(wc, 4) if len(wc) >= 4 else np.full(len(wc), 50)
    mc = np.array([c[i*20+19] for i in range(n//20) if i*20+19 < n])
    mrsi = _rsi(mc, 3) if len(mc) >= 3 else np.full(len(mc), 50)
    w_rsi = np.full(n, 50.0); m_rsi = np.full(n, 50.0)
    for i in range(n):
        wi = i//5; mi = i//20
        w_rsi[i] = wrsi[wi] if wi < len(wrsi) else 50
        m_rsi[i] = mrsi[mi] if mi < len(mrsi) else 50
    res = (np.where(rsi14<30,1,np.where(rsi14>70,-1,0)) +
           np.where(w_rsi<30,0.5,np.where(w_rsi>70,-0.5,0)) +
           np.where(m_rsi<30,0.25,np.where(m_rsi>70,-0.25,0)))
    dates = grp['trade_date'].values
    fwd = grp['future_ret_5d'].values if 'future_ret_5d' in grp.columns else np.full(n, np.nan)
    for j in range(20, n):
        row = {'symbol': sym, 'trade_date': str(dates[j])[:10]}
        for cname in ['frac_balance_20','frac_balance_10','fractal_top_5','fractal_bottom_5',
                       'top_count_10','bottom_count_10','macd','vol_ratio','mom_5d',
                       'atr_ratio','rsi_14','future_ret_5d']:
            if cname in grp.columns:
                row[cname] = float(grp[cname].values[j])
        row['weekly_rsi'] = float(w_rsi[j])
        row['monthly_rsi'] = float(m_rsi[j])
        row['resonance'] = float(res[j])
        row['fwd_ret_5d'] = float(fwd[j]) if j < len(fwd) else np.nan
        all_rows.append(row)

hk_df = pd.DataFrame(all_rows)
hk_df['trade_date'] = pd.to_datetime(hk_df['trade_date'], errors='coerce')
hk_df = hk_df.dropna(subset=['trade_date', 'fwd_ret_5d'])
cutoff = hk_df['trade_date'].max() - pd.Timedelta(days=730)
recent = hk_df[hk_df['trade_date'] >= cutoff].copy()
print(f'  近2年: {len(recent)} rows, {recent["symbol"].nunique()} stocks')

# IC
print('\n  HK Top20 by |RankIC|:')
FACTOR_COLS = ['weekly_rsi','monthly_rsi','resonance','rsi_14','frac_balance_20',
               'frac_balance_10','fractal_top_5','fractal_bottom_5','top_count_10',
               'bottom_count_10','macd','vol_ratio','mom_5d','atr_ratio']
hk_ics = {}
for col in FACTOR_COLS:
    if col not in recent.columns: continue
    d = recent.dropna(subset=[col, 'fwd_ret_5d'])
    if len(d) < 60: continue
    ric, _ = spearmanr(d[col], d['fwd_ret_5d'])
    if not np.isnan(ric):
        hk_ics[col] = ric

for i, (col, ric) in enumerate(sorted(hk_ics.items(), key=lambda x: abs(x[1]), reverse=True)[:20], 1):
    stars = '*' * max(1, int(abs(ric)*40))
    print(f'    {i:2d}. {col:22s} RIC={ric:+.4f} {stars}')

# Fusion
good = {k: v for k, v in hk_ics.items() if abs(v) >= 0.02}
print(f'\n  有效因子(|RIC|>=0.02): {len(good)} 个')
if good:
    tw = sum(abs(v) for v in good.values())
    valid = recent.dropna(subset=['fwd_ret_5d']).copy()
    fused = np.zeros(len(valid))
    for col, ric in good.items():
        vals = valid[col].values.astype(float)
        mask = ~np.isnan(vals)
        v = vals[mask]
        if len(v) > 2 and np.std(v) > 1e-10:
            vn = np.zeros(len(vals))
            vn[mask] = (v - np.mean(v)) / (np.std(v) + 1e-10)
            fused += (abs(ric) / tw) * vn
    valid['fusion'] = fused
    vf = valid.dropna(subset=['fusion', 'fwd_ret_5d'])
    if len(vf) > 50:
        ric, _ = spearmanr(vf['fusion'], vf['fwd_ret_5d'])
        print(f'  融合 RankIC: {ric:+.4f}')
        vf = vf.copy()
        try:
            vf['q'] = pd.qcut(vf['fusion'], 5, labels=['Q1(弱)','Q2','Q3','Q4','Q5(强)'], duplicates='drop')
            grp = vf.groupby('q')['fwd_ret_5d'].mean()
            print(f'  分层Spread: {grp.iloc[-1]-grp.iloc[0]:+.4f}')
            for q, r in grp.items(): print(f'    {q}: {r:+.4f}')
        except: pass
        vf['month'] = vf['trade_date'].astype(str).str[:7]
        monthly = []
        for m, g in vf.groupby('month'):
            if len(g) > 10:
                c = g['fusion'].corr(g['fwd_ret_5d'])
                if not np.isnan(c): monthly.append(c)
        wr = np.mean([1 for x in monthly if x > 0]) if monthly else 0
        print(f'  月胜: {wr:.0%} ({sum(1 for x in monthly if x>0)}/{len(monthly)}月)')

# ── CSI300 ───────────────────────────────────────────
print('\n' + '='*70)
print('[CSI300] Top15:')
try:
    csi_ic = pd.read_csv(r'E:\quant\ml_alpha\factor_research\ic_csi300.csv')
    for i, (_, row) in enumerate(csi_ic.head(15).iterrows(), 1):
        stars = '*' * max(1, int(abs(row['RankIC'])*40))
        print(f'  {i:2d}. {row["factor"]:22s} RIC={row["RankIC"]:+.4f} {stars}  月胜={row["win_rate"]:.0%}')
except Exception as e:
    print(f'  读取失败: {e}')

# ── SPX ──────────────────────────────────────────────
print('\n[SPX] Top15:')
try:
    spx_ic = pd.read_csv(r'E:\quant\ml_alpha\factor_research\ic_spx.csv')
    for i, (_, row) in enumerate(spx_ic.head(15).iterrows(), 1):
        stars = '*' * max(1, int(abs(row['RankIC'])*40))
        print(f'  {i:2d}. {row["factor"]:22s} RIC={row["RankIC"]:+.4f} {stars}  月胜={row["win_rate"]:.0%}')
except Exception as e:
    print(f'  读取失败: {e}')

print('\n' + '='*70)
print('综合研究完成!')
print('='*70)
