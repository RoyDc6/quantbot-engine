"""
全量因子挖掘 v4.0 - 健壮版
"""
import sys, io, warnings, os
from datetime import datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

# ═══════════════════════════════════════════════════════════
# 因子库
# ═══════════════════════════════════════════════════════════

def _ma(p, n):
    r = np.full(len(p), np.nan)
    if len(p) >= n: r[n-1:] = np.convolve(p, np.ones(n)/n, mode='valid')
    return r

def _ema(p, n):
    r = np.full(len(p), np.nan)
    if len(p) < n: return r
    r[n-1] = np.mean(p[:n]); k = 2/(n+1)
    for i in range(n, len(p)): r[i] = p[i]*k + r[i-1]*(1-k)
    return r

def _atr(h, l, c, n=14):
    tr = np.maximum(h[1:]-l[1:], np.maximum(np.abs(h[1:]-c[:-1]), np.abs(l[1:]-c[:-1])))
    tr = np.concatenate([[0], tr])
    r = np.full(len(c), np.nan)
    if len(c) >= n:
        r[n-1] = np.mean(tr[:n])
        for i in range(n, len(c) - 1): r[i] = (r[i-1]*(n-1)+tr[i])/n
    return r

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

def _williams_r(h, l, c, n=14):
    rh = np.full(len(c), np.nan); rl = np.full(len(c), np.nan)
    for i in range(n-1, len(c)):
        rh[i] = np.max(h[i-n+1:i+1]); rl[i] = np.min(l[i-n+1:i+1])
    with np.errstate(divide='ignore', invalid='ignore'):
        wr = (c - rh) / (rh - rl + 1e-10) * -100
    return np.nan_to_num(wr, 50)

def _cci(h, l, c, n=20):
    tp = (h+l+c)/3; sma = _ma(tp, n)
    mad = np.full(len(c), np.nan)
    for i in range(n-1, len(c)):
        mad[i] = np.mean(np.abs(tp[i-n+1:i+1]-sma[i]))
    with np.errstate(divide='ignore', invalid='ignore'):
        return np.nan_to_num((tp-sma)/(mad*0.015+1e-10), 0)

def _adx(h, l, c, n=14):
    tr = np.maximum(h[1:]-l[1:], np.maximum(np.abs(h[1:]-c[:-1]), np.abs(l[1:]-c[:-1])))
    pdm = np.maximum(h[1:]-h[:-1], 0); mdm = np.maximum(l[:-1]-l[1:], 0)
    tr = np.concatenate([[0], tr])
    if len(c) <= n*2: return np.full(len(c), 20.0)
    s_tr = np.full(len(c), np.nan); s_pdm = np.full(len(c), np.nan); s_mdm = np.full(len(c), np.nan)
    for i in range(n, len(c) - 1):
        s_tr[i] = np.mean(tr[i-n+1:i+1]) if i==n else (s_tr[i-1]*(n-1)+tr[i])/n
        s_pdm[i] = np.mean(pdm[i-n+1:i+1]) if i==n else (s_pdm[i-1]*(n-1)+pdm[i])/n
        s_mdm[i] = np.mean(mdm[i-n+1:i+1]) if i==n else (s_mdm[i-1]*(n-1)+mdm[i])/n
    plus_di = 100*np.nan_to_num(s_pdm/s_tr, 0)
    minus_di = 100*np.nan_to_num(s_mdm/s_tr, 0)
    dx = np.abs(plus_di-minus_di)/(plus_di+minus_di+1e-10)*100
    adx = np.full(len(c), 20.0)
    for i in range(n*2, len(c)):
        adx[i] = np.mean(dx[n:i+1]) if i==n*2 else (adx[i-1]*(n-1)+dx[i])/n
    return adx

def _stoch(h, l, c, n=14):
    rh = np.full(len(c), np.nan); rl = np.full(len(c), np.nan)
    for i in range(n-1, len(c)):
        rh[i] = np.max(h[i-n+1:i+1]); rl[i] = np.min(l[i-n+1:i+1])
    with np.errstate(divide='ignore', invalid='ignore'):
        k = np.nan_to_num((c-rl)/(rh-rl+1e-10)*100, 50)
    return k, _ma(k, 3)

def _obv(c, v):
    obv = np.zeros(len(c))
    for i in range(1, len(c)):
        if c[i] > c[i-1]: obv[i] = obv[i-1]+v[i]
        elif c[i] < c[i-1]: obv[i] = obv[i-1]-v[i]
    return obv

def _mfi(h, l, c, v, n=14):
    tp = (h+l+c)/3; mf = tp*v
    pos = np.zeros(len(tp)-1); neg = np.zeros(len(tp)-1)
    for i in range(1, len(tp)):
        if tp[i] > tp[i-1]: pos[i-1] = mf[i]
        elif tp[i] < tp[i-1]: neg[i-1] = mf[i]
    mfi = np.full(len(c), 50.0)
    for i in range(n, len(pos)):
        pf = np.mean(pos[i-n+1:i+1]); nf = np.mean(neg[i-n+1:i+1])
        mfi[i] = 100 if nf<1e-10 else 100-100/(1+pf/(nf+1e-10))
    return mfi

def _cons(close):
    n = len(close); dn = np.zeros(n, dtype=int); up = np.zeros(n, dtype=int)
    for i in range(1, n):
        if close[i] < close[i-1]: dn[i] = dn[i-1]+1
        elif close[i] > close[i-1]: up[i] = up[i-1]+1
    mdn = np.zeros(n, dtype=int)
    for i in range(1, n): mdn[i] = mdn[i-1]+1 if dn[i]>0 else 0
    return dn, up, mdn


def extract_factors(ohlcv, max_bars=1500):
    """
    从 (close, high, low, volume, trade_date) tuple 提取43个因子
    ohlcv: dict with keys close, high, low, volume, trade_date
    """
    c = ohlcv['close'].astype(np.float64)
    h = ohlcv['high'].astype(np.float64)
    l = ohlcv['low'].astype(np.float64)
    v = ohlcv['volume'].astype(np.float64)
    n = min(len(c), max_bars)
    o = c[-n:]; oh = h[-n:]; ol = l[-n:]; ov = v[-n:]
    dates = ohlcv.get('trade_date', ['']*len(c))
    if isinstance(dates, (list, np.ndarray)):
        dates = dates[-n:]

    f = {}

    # ── 1. 均值回归 ──────────────────────────────────
    f['rsi_5'] = _rsi(o, 5)
    f['rsi_14'] = _rsi(o, 14)
    f['rsi_28'] = _rsi(o, 28)
    for period, name in [(5,'bb_pos_5'),(10,'bb_pos_10'),(20,'bb_pos_20')]:
        ma = _ma(o, period)
        std = np.zeros(n)
        for i in range(period-1, n): std[i] = np.std(o[i-period+1:i+1])
        bb = np.zeros(n)
        for i in range(period-1, n):
            bb[i] = (o[i]-ma[i])/(std[i]*2+1e-10) if std[i]>1e-10 else 0
        f[name] = bb
    f['williams_r_5'] = _williams_r(oh, ol, o, 5)
    f['williams_r_14'] = _williams_r(oh, ol, o, 14)
    f['cci_14'] = _cci(oh, ol, o, 14)
    f['cci_28'] = _cci(oh, ol, o, 28)

    # ── 2. 量价背离 ──────────────────────────────────
    f['obv'] = _obv(o, ov)
    f['mfi_14'] = _mfi(oh, ol, o, ov, 14)
    vol_pct = np.full(n, 0.5)
    for i in range(29, n):
        w = ov[max(0,i-29):i+1]
        mn,mx=np.min(w),np.max(w)
        vol_pct[i] = (ov[i]-mn)/(mx-mn+1e-10) if mx>mn else 0.5
    f['vol_pct_30d'] = vol_pct
    vol_ma5 = _ma(ov, 5)
    f['vol_surge'] = np.array([ov[i]/max(vol_ma5[i],1) if i>=4 and vol_ma5[i]>1e-6 else 1.0 for i in range(n)])
    pvcorr = np.full(n, 0.0)
    for i in range(19, n):
        pc = o[i-19:i+1]-np.mean(o[i-19:i+1])
        pv_ = ov[i-19:i+1]-np.mean(ov[i-19:i+1])
        denom = np.sqrt(np.mean(pc**2))*np.sqrt(np.mean(pv_**2))+1e-10
        pvcorr[i] = np.mean(pc*pv_)/denom
    f['pv_corr_20d'] = pvcorr
    obv_slope = np.full(n, 0.0)
    for i in range(9, n): obv_slope[i] = (f['obv'][i]-f['obv'][i-9])/10
    c_slope = np.full(n, 0.0)
    for i in range(9, n): c_slope[i] = (o[i]-o[i-9])/o[i-9] if abs(o[i-9])>1e-10 else 0
    f['obv_div'] = obv_slope * np.sign(c_slope)

    # ── 3. 动量 ───────────────────────────────────────
    for d in [3,5,10,20,60]:
        m = np.zeros(n)
        if n > d: m[d:] = (o[d:]-o[:-d])/np.maximum(o[:-d], 1e-10)
        f[f'mom_{d}d'] = m
    mom_accel = np.zeros(n)
    for i in range(10, n):
        m1 = (o[i-5]-o[i-10])/o[i-10] if i>=10 and abs(o[i-10])>1e-10 else 0
        m2 = (o[i]-o[i-5])/o[i-5] if i>=5 and abs(o[i-5])>1e-10 else 0
        mom_accel[i] = m2-m1
    f['mom_accel'] = mom_accel

    # ── 4. 连续下跌 ───────────────────────────────────
    dn, up, mdn = _cons(o)
    f['cons_down'] = dn.astype(float)
    f['cons_up'] = up.astype(float)
    f['max_cons_down'] = mdn.astype(float)

    # ── 5. 多周期 ────────────────────────────────────
    weekly_c = np.array([o[i*5+4] for i in range(n//5) if i*5+4 < n])
    wrsi = _rsi(weekly_c, 4) if len(weekly_c)>=4 else np.full(len(weekly_c), 50)
    monthly_c = np.array([o[i*20+19] for i in range(n//20) if i*20+19 < n])
    mrsi = _rsi(monthly_c, 3) if len(monthly_c)>=3 else np.full(len(monthly_c), 50)
    w_rsi = np.full(n, 50.0); m_rsi = np.full(n, 50.0)
    nw = len(wrsi); nm = len(mrsi)
    for i in range(n):
        wi=i//5; mi=i//20
        w_rsi[i] = wrsi[wi] if wi<nw else 50
        m_rsi[i] = mrsi[mi] if mi<nm else 50
    f['weekly_rsi'] = w_rsi
    f['monthly_rsi'] = m_rsi
    f['resonance'] = (np.where(f['rsi_14']<30,1,np.where(f['rsi_14']>70,-1,0)) +
                      np.where(w_rsi<30,0.5,np.where(w_rsi>70,-0.5,0)) +
                      np.where(m_rsi<30,0.25,np.where(m_rsi>70,-0.25,0)))

    # ── 6. 趋势 ──────────────────────────────────────
    f['adx_14'] = _adx(oh, ol, o, 14)
    f['adx_28'] = _adx(oh, ol, o, 28)
    macd_l = _ema(o,12) - _ema(o,26)
    msig = np.full(n, 0.0)
    valid = macd_l[~np.isnan(macd_l)]
    if len(valid)>=9:
        s=n-len(valid); msig[s+8]=np.mean(valid[:9]); k=2/10
        for j in range(9,len(valid)): msig[s+j]=valid[j]*k+msig[s+j-1]*(1-k)
    f['macd_hist'] = macd_l - msig
    f['macd_hist_ma5'] = _ma(f['macd_hist'], 5)
    ma_slope = np.zeros(n)
    for i in range(19, n):
        ma_slope[i] = (np.mean(o[i-9:i+1])-np.mean(o[i-19:i-9]))/(np.mean(o[i-19:i-9])+1e-10)
    f['ma_slope_20d'] = ma_slope
    atr14 = _atr(oh, ol, o, 14)
    st_sig = np.zeros(n)
    if n>=14:
        upper=(oh[13]+ol[13])/2+3*atr14[13]; lower=(oh[13]+ol[13])/2-3*atr14[13]; st_sig[13]=1
        for i in range(14,n):
            upper=max(upper,(oh[i]+ol[i])/2+3*atr14[i])
            lower=min(lower,(oh[i]+ol[i])/2-3*atr14[i])
            if o[i]>upper: st_sig[i]=1
            elif o[i]<lower: st_sig[i]=-1
            else: st_sig[i]=st_sig[i-1]
    f['supertrend'] = st_sig

    # ── 7. 波动率 ───────────────────────────────────
    vol20 = np.full(n, np.nan)
    for i in range(19,n):
        rets = np.diff(o[max(0,i-19):i+1])/o[max(0,i-19):i]
        vol20[i] = np.nanstd(rets)*np.sqrt(252) if len(rets)>2 else np.nan
    f['vol_annual_20d'] = vol20
    vol_pct60 = np.full(n, 0.5)
    for i in range(59,n):
        w=vol20[max(0,i-59):i+1]; w=w[~np.isnan(w)]
        if len(w)>5:
            mn,mx=np.min(w),np.max(w)
            vol_pct60[i]=(vol20[i]-mn)/(mx-mn+1e-10) if mx>mn else 0.5
    f['vol_pct_60d'] = vol_pct60
    vol_reg = np.full(n, 1.0)
    for i in range(59,n):
        v_med=np.nanmedian(vol20[max(0,i-59):i+1])
        if not np.isnan(vol20[i]):
            if vol20[i]<v_med*0.5: vol_reg[i]=0
            elif vol20[i]>v_med*2: vol_reg[i]=2
    f['vol_regime'] = vol_reg
    atr_pct = np.full(n, 0.5)
    for i in range(29,n):
        w=atr14[max(0,i-29):i+1]; mn,mx=np.min(w),np.max(w)
        atr_pct[i]=(atr14[i]-mn)/(mx-mn+1e-10) if mx>mn else 0.5
    f['atr_pct_30d'] = atr_pct

    # ── 8. 成交量 ───────────────────────────────────
    vol_ma20 = _ma(ov, 20)
    vol_drought = np.zeros(n)
    for i in range(19,n):
        mn=np.min(ov[max(0,i-19):i+1]); denom=vol_ma20[i]-mn
        vol_drought[i]=(ov[i]-mn)/(denom+1e-10) if abs(denom)>1e-6 else 0
    f['vol_drought'] = vol_drought
    vol_burst = np.full(n, 1.0)
    for i in range(19,n):
        mn=np.mean(ov[max(0,i-19):i+1]); vol_burst[i]=ov[i]/(mn+1e-10)
    f['vol_burst'] = vol_burst
    mf_ratio = np.full(n, 1.0)
    tp=(oh+ol+o)/3; mf=tp*ov
    for i in range(14,n):
        s=max(0,i-13)
        w_tp=tp[s:i+1]; prev=tp[s-1] if s>0 else tp[s]
        pos=np.sum(mf[s:i+1][w_tp>prev]); neg=np.sum(mf[s:i+1][w_tp<prev])
        mf_ratio[i]=(pos+1)/(neg+1)
    f['mf_ratio_14d'] = mf_ratio
    cum_pv=np.cumsum(tp*ov); cum_v=np.cumsum(ov)
    vwap=np.array([cum_pv[i]/cum_v[i] if cum_v[i]>0 else o[i] for i in range(n)])
    f['vwap_dev_20d']=np.array([(o[i]-vwap[i])/vwap[i] if abs(vwap[i])>1e-10 else 0 for i in range(n)])
    k14,d14=_stoch(oh,ol,o,14)
    f['stoch_k_14']=k14; f['stoch_d_14']=d14

    # Labels
    f['fwd_ret_5d']=np.array([(o[i+5]-o[i])/o[i] if i+5<n else np.nan for i in range(n)])
    f['fwd_ret_20d']=np.array([(o[i+20]-o[i])/o[i] if i+20<n else np.nan for i in range(n)])

    return f, dates


# ═══════════════════════════════════════════════════════════
# IC计算
# ═══════════════════════════════════════════════════════════

FACTOR_COLS = [
    'rsi_5','rsi_14','rsi_28','bb_pos_5','bb_pos_10','bb_pos_20',
    'williams_r_5','williams_r_14','cci_14','cci_28',
    'obv','mfi_14','vol_pct_30d','vol_surge','pv_corr_20d','obv_div',
    'mom_3d','mom_5d','mom_10d','mom_20d','mom_60d','mom_accel',
    'cons_down','cons_up','max_cons_down',
    'weekly_rsi','monthly_rsi','resonance',
    'adx_14','adx_28','macd_hist','macd_hist_ma5','ma_slope_20d','supertrend',
    'vol_annual_20d','vol_pct_60d','vol_regime','atr_pct_30d',
    'vol_drought','vol_burst','mf_ratio_14d','vwap_dev_20d','stoch_k_14','stoch_d_14',
]

def calc_ic(df, col, label='fwd_ret_5d', min_n=60):
    df2 = df.dropna(subset=[col, label])
    if len(df2) < min_n: return np.nan, np.nan
    return df2[col].corr(df2[label]), spearmanr(df2[col], df2[label])[0]

def calc_monthly(df, col, label='fwd_ret_5d'):
    df2 = df.dropna(subset=[col, label])
    if len(df2) < 20: return []
    df2 = df2.copy()
    df2['month'] = df2['trade_date'].astype(str).str[:7]
    monthly = []
    for m, g in df2.groupby('month'):
        if len(g) > 5:
            c = g[col].corr(g[label])
            if not np.isnan(c): monthly.append(c)
    return monthly


# ═══════════════════════════════════════════════════════════
# 主程序
# ═══════════════════════════════════════════════════════════

print('='*70)
print('全量因子挖掘 v4.0')
print('='*70)

OUT_DIR = r'E:\quant\ml_alpha\factor_research'
os.makedirs(OUT_DIR, exist_ok=True)

# ── Step 1: 提取 ──────────────────────────────────────
print('\n[Step 1] 提取候选因子...')

# 探索可用数据源
sources = [
    ('HK',     r'E:\quant\ml_alpha\hk_cache'),
    ('HK2',    r'E:\quant\hk_cache'),
    ('CSI300', r'E:\quant\ml_alpha\csi300_cache'),
    ('SPX',    r'E:\quant\ml_alpha\spx_cache'),
]

available = []
for name, path in sources:
    if os.path.exists(path):
        files = [f for f in os.listdir(path) if f.endswith('.csv')]
        if files:
            available.append((name, path, files))
            print(f'  {name}: {len(files)} files at {path}')

all_data = {}

for src_name, cache_dir, files in available:
    print(f'\n  提取 {src_name} ({len(files)} files)...')
    rows = []
    err_count = 0
    
    for i, fname in enumerate(files):
        try:
            df = pd.read_csv(os.path.join(cache_dir, fname))
            if len(df) < 60: continue
            sym = str(df['symbol'].iloc[0]) if 'symbol' in df.columns else fname.replace('.csv','')
            
            ohlcv = {
                'close': df['close'].values,
                'high': df['high'].values,
                'low': df['low'].values,
                'volume': df['volume'].values,
                'trade_date': df['trade_date'].values if 'trade_date' in df.columns else None,
            }
            
            feats, dates = extract_factors(ohlcv, max_bars=1500)
            n = len(feats.get('rsi_14', []))
            
            for j in range(30, n - 1):
                row = {'symbol': sym, 'trade_date': str(dates[j])[:10] if dates[j] is not None and j < len(dates) else ''}
                for name, vals in feats.items():
                    if j < len(vals):
                        v = vals[j]
                        if not (np.isnan(v) or np.isinf(v)): row[name] = v
                rows.append(row)
            
            if (i+1) % 25 == 0:
                print(f'    {i+1}/{len(files)} ... {len(rows)} rows')
        except Exception as e:
            err_count += 1
            if err_count <= 3:
                print(f'    ERROR {fname}: {e}')

    if rows:
        mdf = pd.DataFrame(rows)
        mdf['trade_date'] = pd.to_datetime(mdf['trade_date'], errors='coerce')
        mdf = mdf.dropna(subset=['trade_date'])
        # 去重（同一symbol同一日期可能多次出现）
        mdf = mdf.drop_duplicates(subset=['symbol','trade_date'])
        mdf = mdf.sort_values('trade_date')
        # 统一名称
        market = 'HK' if src_name in ('HK','HK2') else src_name
        all_data[market] = mdf
        print(f'  {src_name}: {len(mdf)} rows, {mdf["symbol"].nunique()} stocks')
        print(f'    {mdf["trade_date"].min().date()} ~ {mdf["trade_date"].max().date()}')
    else:
        print(f'  {src_name}: 0 rows ({err_count} errors)')

# ── Step 2: IC ────────────────────────────────────────
print('\n[Step 2] IC初筛...')

ic_results = {}
for mname, df in all_data.items():
    cutoff = df['trade_date'].max() - pd.Timedelta(days=730)
    recent = df[df['trade_date'] >= cutoff].copy()
    print(f'\n  {mname}: {len(recent)} 条, {recent["symbol"].nunique()} 只, 近2年')

    mics = {}
    for col in FACTOR_COLS:
        if col not in recent.columns: continue
        ic, ric = calc_ic(recent, col)
        if not np.isnan(ric):
            monthly = calc_monthly(recent, col)
            wr = np.mean([1 for x in monthly if x > 0]) if monthly else 0
            mics[col] = {'ic':ic,'rank_ic':ric,'monthly':monthly,'win_rate':wr,'n':len(monthly)}

    sf = sorted(mics.items(), key=lambda x: abs(x[1]['rank_ic']), reverse=True)
    ic_results[mname] = mics
    print(f'    Top15:')
    for j, (name, s) in enumerate(sf[:15], 1):
        stars = '⭐' * max(1, int(abs(s['rank_ic'])*40))
        print(f'    {j:2d}. {name:22s} IC={s["ic"]:+.4f}  RIC={s["rank_ic"]:+.4f} {stars}  月胜={s["win_rate"]:.0%}')

# ── Step 3: 融合 ─────────────────────────────────────
print('\n[Step 3] 融合验证...')

for mname, df in all_data.items():
    if mname not in ic_results: continue
    ics = ic_results[mname]
    good = {k:v for k,v in ics.items() if abs(v['rank_ic'])>=0.02}
    print(f'\n  {mname}: {len(good)} 有效因子')

    cutoff = df['trade_date'].max() - pd.Timedelta(days=730)
    recent = df[df['trade_date'] >= cutoff].dropna(subset=['fwd_ret_5d']).copy()
    if len(good) < 2 or len(recent) < 100: continue

    tw = sum(abs(v['rank_ic']) for v in good.values())
    fused = np.zeros(len(recent))

    for col, stats in good.items():
        vals = recent[col].values.astype(float)
        mask = ~np.isnan(vals)
        v = vals[mask]
        if len(v) > 2 and np.std(v) > 1e-10:
            vn = np.zeros(len(vals))
            vn[mask] = (v - np.mean(v)) / (np.std(v) + 1e-10)
            fused += (abs(stats['rank_ic']) / tw) * vn

    recent['fusion'] = fused
    valid = recent.dropna(subset=['fusion', 'fwd_ret_5d'])
    if len(valid) < 50: continue

    ric, _ = spearmanr(valid['fusion'], valid['fwd_ret_5d'])
    print(f'  融合 RankIC: {ric:+.4f}')

    valid = valid.copy()
    try:
        valid['q'] = pd.qcut(valid['fusion'], 5, labels=['Q1(弱)','Q2','Q3','Q4','Q5(强)'], duplicates='drop')
        grp = valid.groupby('q')['fwd_ret_5d'].mean()
        if len(grp) >= 2:
            print(f'  Spread: {grp.iloc[-1]-grp.iloc[0]:+.4f}')
            for q, r in grp.items(): print(f'    {q}: {r:+.4f}')
    except: pass

    valid['month'] = valid['trade_date'].astype(str).str[:7]
    monthly = []
    for m, g in valid.groupby('month'):
        if len(g) > 10:
            c = g['fusion'].corr(g['fwd_ret_5d'])
            if not np.isnan(c): monthly.append(c)
    wr = np.mean([1 for x in monthly if x > 0]) if monthly else 0
    print(f'  月胜: {wr:.0%} ({sum(1 for x in monthly if x>0)}/{len(monthly)}月)')

# ── Step 4: 跨市场 ────────────────────────────────────
print('\n[Step 4] 跨市场有效因子...')
if len(all_data) >= 2:
    common = set(FACTOR_COLS)
    for m, ics in ic_results.items():
        common &= {k for k,v in ics.items() if abs(v['rank_ic'])>=0.02}
    print(f'  {len(common)} 个跨市场有效因子:')
    for f in sorted(common):
        vals = {m: ic_results[m][f]['rank_ic'] for m in ic_results if f in ic_results[m]}
        avg = np.mean(list(vals.values()))
        stars = '⭐' * max(1, int(abs(avg)*40))
        print(f'    {f:22s} avg={avg:+.4f} {stars}')
        for m, v in vals.items():
            print(f'      {m}: {v:+.4f}')

# ── Step 5: 保存 ─────────────────────────────────────
print('\n[Step 5] 保存...')
for mname, ics in ic_results.items():
    rows = [{'factor':k,'market':mname,'IC':v['ic'],'RankIC':v['rank_ic'],
             'absRankIC':abs(v['rank_ic']),'win_rate':v['win_rate'],'n_months':v['n']}
            for k,v in ics.items()]
    if rows:
        pd.DataFrame(rows).sort_values('absRankIC',ascending=False)\
            .to_csv(f'{OUT_DIR}\\ic_{mname.lower()}.csv',index=False,encoding='utf-8-sig')
        print(f'  ic_{mname.lower()}.csv')

print('\n' + '='*70)
print('完成!')
print('='*70)
