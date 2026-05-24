"""港股融合模型升级 - 加入weekly_rsi和resonance"""
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
print('港股融合模型升级 - 加入weekly_rsi + resonance')
print('='*70)

# 加载HK特征
df = pd.read_csv(r'E:\quant\ml_alpha\hk_features_full.csv')
df['trade_date'] = pd.to_datetime(df['trade_date'], errors='coerce')
df = df.dropna(subset=['trade_date'])
df = df.sort_values(['symbol', 'trade_date'])
print(f'\nHK数据: {len(df)} rows, {df["symbol"].nunique()} stocks')
print(f'时间范围: {df["trade_date"].min().date()} ~ {df["trade_date"].max().date()}')

# 计算新因子
print('\n计算 weekly_rsi 和 resonance...')
all_rows = []
for sym, grp in df.groupby('symbol'):
    grp = grp.sort_values('trade_date')
    c = grp['close'].values.astype(np.float64)
    n = len(c)
    if n < 60: continue
    
    # 日线RSI
    rsi14 = _rsi(c, 14)
    
    # 周线合成
    wc = np.array([c[i*5+4] for i in range(n//5) if i*5+4 < n])
    wrsi = _rsi(wc, 4) if len(wc) >= 4 else np.full(len(wc), 50)
    
    # 月线合成
    mc = np.array([c[i*20+19] for i in range(n//20) if i*20+19 < n])
    mrsi = _rsi(mc, 3) if len(mc) >= 3 else np.full(len(mc), 50)
    
    # 映射回日线
    w_rsi = np.full(n, 50.0); m_rsi = np.full(n, 50.0)
    for i in range(n):
        wi = i//5; mi = i//20
        w_rsi[i] = wrsi[wi] if wi < len(wrsi) else 50
        m_rsi[i] = mrsi[mi] if mi < len(mrsi) else 50
    
    # 共振因子
    resonance = (np.where(rsi14<30, 1, np.where(rsi14>70, -1, 0)) +
                 np.where(w_rsi<30, 0.5, np.where(w_rsi>70, -0.5, 0)) +
                 np.where(m_rsi<30, 0.25, np.where(m_rsi>70, -0.25, 0)))
    
    dates = grp['trade_date'].values
    fwd = grp['future_ret_5d'].values if 'future_ret_5d' in grp.columns else grp['ret_5d'].shift(-5).values
    
    # 缠论因子
    for j in range(20, n):
        row = {
            'symbol': sym, 'trade_date': str(dates[j])[:10],
            'weekly_rsi': float(w_rsi[j]),
            'monthly_rsi': float(m_rsi[j]),
            'resonance': float(resonance[j]),
            'fwd_ret_5d': float(fwd[j]) if j < len(fwd) else np.nan,
        }
        # 读取缠论因子
        for col in ['frac_balance_20','frac_balance_10','fractal_top_5','fractal_bottom_5',
                    'top_count_10','bottom_count_10','td9_count','bull_divergence',
                    'macd','vol_ratio','mom_5d','mom_10d','atr_ratio','rsi_14']:
            if col in grp.columns:
                row[col] = float(grp[col].values[j])
        all_rows.append(row)

hk = pd.DataFrame(all_rows)
hk['trade_date'] = pd.to_datetime(hk['trade_date'], errors='coerce')
hk = hk.dropna(subset=['trade_date', 'fwd_ret_5d'])
print(f'有效记录: {len(hk)} rows')

# 近2年验证
cutoff = hk['trade_date'].max() - pd.Timedelta(days=730)
recent = hk[hk['trade_date'] >= cutoff].copy()
print(f'近2年验证: {len(recent)} rows, {recent["symbol"].nunique()} stocks')

# 定义因子组
CHAN_FACTORS = ['frac_balance_20','frac_balance_10','fractal_top_5','fractal_bottom_5',
                'top_count_10','bottom_count_10','td9_count','bull_divergence',
                'macd','vol_ratio','mom_5d','mom_10d','atr_ratio','rsi_14']
NEW_FACTORS = ['weekly_rsi','resonance','monthly_rsi']
LABEL = 'fwd_ret_5d'

def calc_ic(df, col, label):
    d = df.dropna(subset=[col, label])
    if len(d) < 60: return np.nan
    return spearmanr(d[col], d[label])[0]

def fusion_model(df, factors, label):
    """IC加权融合"""
    ics = {}
    for col in factors:
        if col not in df.columns: continue
        ic = calc_ic(df, col, label)
        if not np.isnan(ic) and abs(ic) >= 0.01:
            ics[col] = ic
    
    if len(ics) < 2: return None, ics
    
    tw = sum(abs(v) for v in ics.values())
    valid = df.dropna(subset=[label]).copy()
    fused = np.zeros(len(valid))
    
    for col, ic in ics.items():
        vals = valid[col].values.astype(float)
        mask = ~np.isnan(vals)
        v = vals[mask]
        if len(v) > 2 and np.std(v) > 1e-10:
            vn = np.zeros(len(vals))
            vn[mask] = (v - np.mean(v)) / (np.std(v) + 1e-10)
            fused += (abs(ic) / tw) * vn
    
    valid['fusion'] = fused
    return valid, ics

def evaluate(valid, label):
    """评估融合模型"""
    vf = valid.dropna(subset=['fusion', label])
    if len(vf) < 50: return {}
    
    ric, _ = spearmanr(vf['fusion'], vf[label])
    ic = vf['fusion'].corr(vf[label])
    
    # 分层
    vf = vf.copy()
    try:
        vf['q'] = pd.qcut(vf['fusion'], 5, labels=['Q1(弱)','Q2','Q3','Q4','Q5(强)'], duplicates='drop')
        grp = vf.groupby('q')[label].mean()
        spread = grp.iloc[-1] - grp.iloc[0] if len(grp) >= 2 else 0
    except:
        spread = 0
        grp = {}
    
    # 月度胜率
    vf['month'] = vf['trade_date'].astype(str).str[:7]
    monthly = []
    for m, g in vf.groupby('month'):
        if len(g) > 10:
            c = g['fusion'].corr(g[label])
            if not np.isnan(c): monthly.append(c)
    wr = np.mean([1 for x in monthly if x > 0]) if monthly else 0
    
    return {
        'rank_ic': ric,
        'ic': ic,
        'spread': spread,
        'groups': grp,
        'monthly_wr': wr,
        'n_months': len(monthly),
        'n_wins': sum(1 for x in monthly if x > 0)
    }

print('\n' + '='*70)
print('模型对比')
print('='*70)

# ── 模型A: 缠论单模型 ───────────────────────────────────
print('\n[A] 缠论单模型 (14因子)')
valid_a, ics_a = fusion_model(recent, CHAN_FACTORS, LABEL)
if valid_a is not None:
    res_a = evaluate(valid_a, LABEL)
    print(f'  RankIC: {res_a["rank_ic"]:+.4f}')
    print(f'  Spread: {res_a["spread"]:+.4f}')
    print(f'  月胜: {res_a["monthly_wr"]:.0%} ({res_a["n_wins"]}/{res_a["n_months"]}月)')
    print(f'  有效因子: {len(ics_a)} 个')
    for col, ic in sorted(ics_a.items(), key=lambda x: abs(x[1]), reverse=True)[:5]:
        stars = '*' * max(1, int(abs(ic)*40))
        print(f'    {col:20s} IC={ic:+.4f} {stars}')

# ── 模型B: 新因子单模型 ─────────────────────────────────
print('\n[B] 新因子单模型 (3因子)')
valid_b, ics_b = fusion_model(recent, NEW_FACTORS, LABEL)
if valid_b is not None:
    res_b = evaluate(valid_b, LABEL)
    print(f'  RankIC: {res_b["rank_ic"]:+.4f}')
    print(f'  Spread: {res_b["spread"]:+.4f}')
    print(f'  月胜: {res_b["monthly_wr"]:.0%} ({res_b["n_wins"]}/{res_b["n_months"]}月)')
    print(f'  有效因子: {len(ics_b)} 个')
    for col, ic in ics_b.items():
        stars = '*' * max(1, int(abs(ic)*40))
        print(f'    {col:20s} IC={ic:+.4f} {stars}')

# ── 模型C: 融合模型 (缠论+新因子) ───────────────────────
print('\n[C] 融合模型 (缠论+新因子, 17因子)')
ALL_FACTORS = CHAN_FACTORS + NEW_FACTORS
valid_c, ics_c = fusion_model(recent, ALL_FACTORS, LABEL)
if valid_c is not None:
    res_c = evaluate(valid_c, LABEL)
    print(f'  RankIC: {res_c["rank_ic"]:+.4f}')
    print(f'  Spread: {res_c["spread"]:+.4f}')
    print(f'  月胜: {res_c["monthly_wr"]:.0%} ({res_c["n_wins"]}/{res_c["n_months"]}月)')
    print(f'  有效因子: {len(ics_c)} 个')
    
    # 因子权重
    print('\n  Top10因子权重:')
    for col, ic in sorted(ics_c.items(), key=lambda x: abs(x[1]), reverse=True)[:10]:
        w = abs(ic) / sum(abs(v) for v in ics_c.values())
        stars = '*' * max(1, int(abs(ic)*40))
        print(f'    {col:20s} IC={ic:+.4f} w={w:.1%} {stars}')
    
    # 分层详情
    if res_c['groups']:
        print('\n  分层收益:')
        for q, r in res_c['groups'].items():
            print(f'    {q}: {r:+.4f}')

# ── 对比总结 ────────────────────────────────────────────
print('\n' + '='*70)
print('对比总结')
print('='*70)
print(f'\n{"模型":<20} {"RankIC":>10} {"Spread":>10} {"月胜率":>10} {"有效因子":>10}')
print('-'*60)
if 'res_a' in dir(): print(f'{"缠论单模型":<20} {res_a["rank_ic"]:+10.4f} {res_a["spread"]:+10.4f} {res_a["monthly_wr"]:+10.0%} {len(ics_a):+10d}')
if 'res_b' in dir(): print(f'{"新因子单模型":<20} {res_b["rank_ic"]:+10.4f} {res_b["spread"]:+10.4f} {res_b["monthly_wr"]:+10.0%} {len(ics_b):+10d}')
if 'res_c' in dir(): print(f'{"融合模型":<20} {res_c["rank_ic"]:+10.4f} {res_c["spread"]:+10.4f} {res_c["monthly_wr"]:+10.0%} {len(ics_c):+10d}')

# 提升幅度
if 'res_a' in dir() and 'res_c' in dir():
    imp_ic = (res_c['rank_ic'] - res_a['rank_ic']) / abs(res_a['rank_ic']) * 100
    imp_sp = (res_c['spread'] - res_a['spread']) / abs(res_a['spread']) * 100 if res_a['spread'] != 0 else 0
    print(f'\n融合模型 vs 缠论单模型:')
    print(f'  RankIC 提升: {imp_ic:+.1f}%')
    print(f'  Spread 提升: {imp_sp:+.1f}%')

print('\n' + '='*70)
print('完成!')
print('='*70)
