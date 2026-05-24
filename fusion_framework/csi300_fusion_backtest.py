# -*- coding: utf-8 -*-
"""
CSI300 融合框架回测 v3.0 - 极简高性能版
单表内联计算 → 截面排序 → 回测
"""
import sys, io, warnings, numpy as np, pandas as pd
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stdout.flush()
warnings.filterwarnings('ignore')

TOP_N, MAX_EXP, SINGLE_MAX = 15, 0.80, 0.10
FIXED_STOP, TRAIL_STOP, CD_DAYS = -0.08, -0.08, 5
COMM_B, COMM_S, REBAL = 0.0003, 0.0013, 5
THR = 0.25

W = {'wrsi': 0.202, 'res': -0.152, 'fb10': 0.107, 'mrsi': 0.094, 'fb20': 0.088, 'rsi14': 0.029}

def rsi(p, n=14):
    d = np.diff(p); g = np.where(d>0,d,0.0); l = np.where(d<0,-d,0.0)
    r = np.full(len(p), 50.0)
    if len(p) <= n: return r
    ag, al = np.mean(g[:n]), np.mean(l[:n])
    r[n] = 50.0 if al<1e-10 else 100-100/(1+ag/(al+1e-10))
    for i in range(n+1, len(p)):
        ag=(ag*(n-1)+g[i-1])/n; al=(al*(n-1)+l[i-1])/n
        r[i]=50.0 if al<1e-10 else 100-100/(1+ag/(al+1e-10))
    return r

t0 = datetime.now()
print("="*60)
print("CSI300 Fusion Backtest v3")
print(f"Start: {t0.strftime('%H:%M:%S')}")
print("="*60)

# ── Load ──
print("\nLoading...")
df = pd.read_csv(r'E:\quant\ml_alpha\csi300_features_raw.csv')
df['trade_date'] = pd.to_datetime(df['trade_date'])
df = df.sort_values(['symbol','trade_date']).reset_index(drop=True)
syms = sorted(df['symbol'].unique())
ns = len(syms)
dates = sorted(df['trade_date'].unique())
nd = len(dates)
print(f"  {len(df)} rows, {ns} stocks, {nd} days, {df['trade_date'].min().date()}~{df['trade_date'].max().date()}")
sys.stdout.flush()

# ── Precompute weekly/monthly rsi per stock, store as dict ──
print("\nPrecomputing weekly/monthly RSI...")
wrsi_map, mrsi_map, close_map = {}, {}, {}
for i, sym in enumerate(syms):
    if i % 25 == 0:
        print(f"  {i}/{ns}...")
        sys.stdout.flush()
    s = df[df['symbol']==sym].sort_values('trade_date')
    c = s['close'].values.astype(float)
    ret_ = s['ret_1d'].values.astype(float)
    n = len(c)
    wv = np.full(n, 50.0); mv = np.full(n, 50.0)
    for j in range(25, n):
        wc = np.cumprod(1+ret_[max(0,j-24):j+1])
        ws = wc[::5]
        if len(ws)>=4: wv[j]=rsi(ws,4)[-1]
    for j in range(60, n):
        mc = np.cumprod(1+ret_[max(0,j-59):j+1])
        ms = mc[::20]
        if len(ms)>=3: mv[j]=rsi(ms,3)[-1]
    wrsi_map[sym] = dict(zip(s['trade_date'].values, wv))
    mrsi_map[sym] = dict(zip(s['trade_date'].values, mv))
    close_map[sym] = dict(zip(s['trade_date'].values, c))

print(f"  Done. {sum(len(v) for v in wrsi_map.values())} entries")
sys.stdout.flush()

# ── Build daily score table (slim) ──
print("\nBuilding daily scores...")
score_rows = []
for di, d in enumerate(dates):
    day = df[df['trade_date']==d]
    if len(day) < 30: continue
    vals = {'wrsi':[], 'res':[], 'fb10':[], 'mrsi':[], 'fb20':[], 'rsi14':[]}
    row_syms = []
    for _, rx in day.iterrows():
        sym = rx['symbol']
        wv = wrsi_map[sym].get(d, 50.0)
        mv = mrsi_map[sym].get(d, 50.0)
        r14 = float(rx.get('rsi_14', 50.0))
        fb10 = float(rx.get('frac_balance_10', 0.5))
        fb20 = float(rx.get('frac_balance_20', 0.5))
        # resonance
        res_v = 0
        if r14<30: res_v+=1
        elif r14>70: res_v-=1
        if wv<30: res_v+=0.5
        elif wv>70: res_v-=0.5
        if mv<30: res_v+=0.25
        elif mv>70: res_v-=0.25
        row_syms.append(sym)
        vals['wrsi'].append(wv); vals['res'].append(res_v)
        vals['fb10'].append(fb10); vals['mrsi'].append(mv)
        vals['fb20'].append(fb20); vals['rsi14'].append(r14)

    # Z-score + fusion
    for k in vals:
        arr = np.array(vals[k])
        m, s_ = arr.mean(), arr.std()
        vals[k] = (arr-m)/s_ if s_>1e-10 else np.zeros_like(arr)

    f_score = (W['wrsi']*vals['wrsi'] + W['res']*vals['res'] +
               W['fb10']*vals['fb10'] + W['mrsi']*vals['mrsi'] +
               W['fb20']*vals['fb20'] + W['rsi14']*vals['rsi14'])

    # Rank by fusion_score, store top 30
    idx = np.argsort(f_score)[::-1]
    for rk, ii in enumerate(idx[:30]):
        score_rows.append({'date': d, 'symbol': row_syms[ii],
                           'score': f_score[ii], 'rank': rk+1,
                           'close': day.iloc[ii]['close']})

    if di % 500 == 0:
        print(f"  {di}/{nd} days...")
        sys.stdout.flush()

scores = pd.DataFrame(score_rows)
print(f"  Score table: {len(scores)} rows ({scores['date'].nunique()} days)")
sys.stdout.flush()

# ── Backtest ──
print("\nBacktesting...")
score_dates = sorted(scores['date'].unique())
warmup = 120
initial = 1000000.0
cash = initial
positions = {}      # sym → {'shares', 'cost', 'high'}
cooldown = {}        # sym → last_stop_day_idx
trades_log = []
nav_arr = []

for di, d in enumerate(score_dates):
    if di < warmup:
        nav_arr.append(initial)
        continue

    td_scores = scores[scores['date']==d].sort_values('rank')

    # REBALANCE: pick top N
    rebal = (di % REBAL == 0)
    if rebal:
        top = set(td_scores.head(TOP_N)['symbol'])
        # Sell non-top
        for sym in list(positions.keys()):
            if sym not in top:
                pos  = positions[sym]
                px = close_map[sym].get(d)
                if not pd.isna(px) and px > 0:
                    cash += pos['shares'] * px * (1 - COMM_S)
                    trades_log.append((str(d)[:10], 'SELL', sym, px, 'REBAL'))
                del positions[sym]

        # Buy new
        n_new = len(top - set(positions.keys()))
        if n_new > 0 and len(td_scores) > 0:
            budget = min(cash * MAX_EXP / max(n_new,1), initial * SINGLE_MAX)
            for _, sr in td_scores.iterrows():
                sym = sr['symbol']
                if sym in positions: continue
                px = float(sr['close'])
                if px <= 0: continue
                sh = int(budget / px)
                if sh == 0: continue
                cost = sh * px * (1 + COMM_B)
                if cost > cash:
                    sh = int(cash * 0.95 / px)
                    cost = sh * px * (1 + COMM_B)
                if sh == 0: continue
                cash -= cost
                positions[sym] = {'shares': sh, 'cost': px, 'high': px}
                trades_log.append((str(d)[:10], 'BUY', sym, px, f'{sr.score:+.2f}'))

    # STOP-LOSS
    for sym in list(positions.keys()):
        px = close_map[sym].get(d)
        if pd.isna(px) or px <= 0: continue
        pos = positions[sym]
        pos['high'] = max(pos['high'], px)
        pnl = px / pos['cost'] - 1
        trail_dd = px / pos['high'] - 1
        if pnl <= FIXED_STOP or trail_dd <= TRAIL_STOP:
            cash += pos['shares'] * px * (1 - COMM_S)
            reason = 'FIXED' if pnl <= FIXED_STOP else 'TRAIL'
            trades_log.append((str(d)[:10], 'STOP', sym, px, f'{pnl*100:+.1f}%|{reason}'))
            cooldown[sym] = di
            del positions[sym]

    # NAV
    nav = cash
    for sym, pos in positions.items():
        px = close_map[sym].get(d)
        if not pd.isna(px) and px > 0:
            nav += pos['shares'] * px
    nav_arr.append(nav)

# ── Benchmark: equal-weight buy-hold ──
print("Benchmark...")
warmup_date = score_dates[warmup]
base = {}
for sym in syms:
    px = close_map[sym].get(warmup_date)
    if not pd.isna(px) and px > 0:
        base[sym] = px

bh = []
for di, d in enumerate(score_dates):
    if di < warmup:
        bh.append(initial); continue
    tot, cnt = 0, 0
    for sym, bp in base.items():
        px = close_map[sym].get(d)
        if not pd.isna(px) and px > 0:
            tot += px/bp; cnt += 1
    bh.append(initial * tot / cnt if cnt > 0 else bh[-1])

# ── Stats ──
def stats(nav, initial):
    arr = np.array(nav)
    tr = (arr[-1]/initial-1)*100
    ar = tr/(len(arr)/252)
    rets = np.diff(arr)/arr[:-1]
    rets = rets[np.isfinite(rets)]
    sh = np.mean(rets)/np.std(rets)*np.sqrt(252) if len(rets)>1 and np.std(rets)>1e-10 else 0
    pk = np.maximum.accumulate(arr)
    mdd = np.min((arr-pk)/pk)*100
    return tr, ar, sh, mdd, arr[-1]

s_tr,s_ar,s_sh,s_mdd,s_fv = stats(nav_arr, initial)
b_tr,b_ar,b_sh,b_mdd,b_fv = stats(bh, initial)

# ── Output ──
print("\n" + "="*60)
print("CSI300 Fusion Framework Results")
print("="*60)
print(f"{'':<16} {'Fusion':>12} {'B&H EW':>14} {'Alpha':>12}")
print("-"*56)
print(f"{'Total Ret':<16} {s_tr:>+11.2f}% {b_tr:>+13.2f}% {s_tr-b_tr:>+11.2f}%")
print(f"{'Annual Ret':<16} {s_ar:>+11.2f}% {b_ar:>+13.2f}% {s_ar-b_ar:>+11.2f}%")
print(f"{'Sharpe':<16} {s_sh:>12.2f} {b_sh:>14.2f} {'':>12}")
print(f"{'Max DD':<16} {s_mdd:>+11.2f}% {b_mdd:>+13.2f}% {'':>12}")
print(f"{'Final Val':<16} {s_fv:>12,.0f} {b_fv:>14,.0f} {'':>12}")

buys = [t for t in trades_log if t[1]=='BUY']
stops = [t for t in trades_log if t[1]=='STOP']
sells = [t for t in trades_log if t[1]=='SELL']
print(f"\nTrades: {len(trades_log)} | BUY:{len(buys)} SELL:{len(sells)} STOP:{len(stops)}")

elapsed = (datetime.now()-t0).total_seconds()
print(f"\n{elapsed:.1f}s | {datetime.now().strftime('%H:%M:%S')}")
print("="*60)
