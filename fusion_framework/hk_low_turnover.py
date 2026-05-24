"""
港股融合框架 - 低换手版本 v5
关键修复: weekly_rsi使用for+concat方法赋值(pandas 2.x兼容)
"""
import pandas as pd, numpy as np, os, warnings
warnings.filterwarnings('ignore')

REBAL_FREQ    = 5
MIN_SCORE     = 3
COST_PER      = 0.003
INIT_CASH     = 100_0000
MAX_POSITIONS = 15
HOLD_MAX      = 8

os.chdir(r'E:\quant\fusion_framework')
LOG = open('E:/quant/fusion_framework/low_log.txt','w',encoding='utf-8')
def W(msg):
    print(msg); LOG.write(msg+'\n'); LOG.flush()

# ── 加载 ──────────────────────────────────────────────────────
df = pd.read_csv('E:/quant/ml_alpha/hk_features_full.csv')
df['trade_date'] = pd.to_datetime(df['trade_date'])
df = df[df['volume']>0].copy()
df = df[df['close']>0.5].copy()
df = df.sort_values(['symbol','trade_date']).reset_index(drop=True)

# ── weekly_rsi（方法A：for+concat，pandas 2.x兼容）───────────
W("计算weekly_rsi...")
weekly_list = []
for sym, g in df.groupby('symbol'):
    g = g.sort_values('trade_date').reset_index(drop=True)
    close = g['close'].values; n = len(close)
    wrsi = np.full(n, 50.0)
    weekly_close = [close[i*5+4] for i in range(n//5) if i*5+4 < n]
    if len(weekly_close) >= 15:
        d = np.diff(weekly_close)
        g2 = np.where(d > 0, d, 0.0); l2 = np.where(d < 0, -d, 0.0)
        period = 4
        ag, al = np.mean(g2[:period]), np.mean(l2[:period])
        weekly_rsi_arr = np.full(len(weekly_close), 50.0)
        weekly_rsi_arr[period] = 50.0 if al < 1e-10 else 100-100/(1+ag/(al+1e-10))
        for i in range(period+1, len(weekly_close)):
            ag = (ag*(period-1)+g2[i-1])/period
            al = (al*(period-1)+l2[i-1])/period
            weekly_rsi_arr[i] = 100-100/(1+ag/(al+1e-10)) if al>1e-10 else 100.0
        for i in range(n):
            w = i // 5
            if w < len(weekly_rsi_arr): wrsi[i] = weekly_rsi_arr[w]
    tmp = g.copy()
    tmp['weekly_rsi'] = wrsi
    weekly_list.append(tmp)
df = pd.concat(weekly_list, ignore_index=True)

df['weekly_rsi'] = pd.to_numeric(df['weekly_rsi'], errors='coerce').fillna(50.0)
df['future_ret_5d'] = df.groupby('symbol')['close'].pct_change(5)

# ── IC 验证 ──────────────────────────────────────────────────
from scipy.stats import spearmanr
recent = df[df['trade_date']>'2025-01-01'].dropna(subset=['weekly_rsi','future_ret_5d'])
recent2 = recent.dropna(subset=['resonance']).copy()
recent2['score'] = 0.202*(recent2['weekly_rsi']-50) + (-0.152)*recent2['resonance']
ic_w, pw = spearmanr(recent['weekly_rsi'], recent['future_ret_5d'])
ic_r, pr = spearmanr(recent2['resonance'], recent2['future_ret_5d'])
ic_s, ps = spearmanr(recent2['score'], recent2['future_ret_5d'])
W(f"weekly_rsi IC={ic_w:.4f} (p={pw:.2e}) {'***' if pw<0.001 else ''}")
W(f"resonance IC={ic_r:.4f} (p={pr:.2e})")
W(f"融合打分IC={ic_s:.4f} (p={ps:.2e}) {'***' if ps<0.001 else ''}")
W(f"BUY(score>=3): {(recent2['score']>=3).sum()} SELL(score<=-3): {(recent2['score']<=-3).sum()}")
W(f"wrsi分布: <30:{(recent2['weekly_rsi']<30).sum()} 30-50:{(recent2['weekly_rsi']<50)&(recent2['weekly_rsi']>=30).sum()} 50-70:{(recent2['weekly_rsi']<70)&(recent2['weekly_rsi']>=50).sum()} >=70:{(recent2['weekly_rsi']>=70).sum()}")

# ── 回测 ──────────────────────────────────────────────────────
ALL_DATES = sorted(df['trade_date'].unique())
recent_dates = ALL_DATES[-500:]

W(f"\n开始回测 ({len(recent_dates)}天)...")
cash = float(INIT_CASH)
positions = {}
day_trades = []
daily_nav = []

for i, date in enumerate(recent_dates):
    today = df[df['trade_date']==date]

    # 更新持仓
    for sym, pos in list(positions.items()):
        pr = today[today['symbol']==sym]
        pos['cur_price'] = float(pr.iloc[0]['close']) if len(pr)>0 else float(
            df[(df['symbol']==sym)&(df['trade_date']<=date)].tail(1).iloc[0]['close'])
        pos['hold_days'] = pos.get('hold_days',0) + 1
        if pos['hold_days'] >= HOLD_MAX:
            proceeds = pos['shares']*pos['cur_price']*(1-COST_PER)
            cash += proceeds
            day_trades.append(('SELL_MAX', sym))
            del positions[sym]

    # 生成候选信号
    candidates = []
    for _, row in today.iterrows():
        sym = row['symbol']
        hist = df[(df['symbol']==sym)&(df['trade_date']<=date)].tail(60)
        if len(hist) < 60: continue
        wrsi = float(row.get('weekly_rsi', 50.0))
        rsi_d = float(row.get('rsi_14', 50.0))
        macd_h = float(row.get('macd_hist', 0.0))
        ma20r = float(row.get('ma20_ratio', 1.0))
        ma60r = float(row.get('ma60_ratio', 1.0))
        res = float(row.get('resonance', 0.0))
        for v in [wrsi, rsi_d, macd_h, ma20r, ma60r, res]:
            if np.isnan(v): v = 0.0
        score = 0.202*(wrsi-50) + (-0.152)*res
        trend_up = bool(ma20r>1.0) and bool(ma60r>1.0)
        above20 = bool(ma20r>1.0); above60 = bool(ma60r>1.0)
        macd_pos = bool(macd_h>0)
        if above20 and above60 and macd_pos and rsi_d<70: ta='BUY'
        elif rsi_d>=78 or (not above20 and rsi_d>65): ta='SELL'
        else: ta='HOLD'
        fm = 'BUY' if score>=MIN_SCORE else ('SELL' if score<=-MIN_SCORE else 'HOLD')
        if trend_up:
            mat={('BUY','BUY'):'STRONG_BUY',('BUY','HOLD'):'BUY',('BUY','SELL'):'BUY',
                 ('HOLD','BUY'):'BUY',('HOLD','HOLD'):'HOLD',('HOLD','SELL'):'HOLD',
                 ('SELL','BUY'):'BUY',('SELL','HOLD'):'HOLD',('SELL','SELL'):'SELL'}
        else:
            mat={('BUY','BUY'):'STRONG_BUY',('BUY','HOLD'):'BUY',('BUY','SELL'):'REDUCED',
                 ('HOLD','BUY'):'HOLD',('HOLD','HOLD'):'HOLD',('HOLD','SELL'):'SELL',
                 ('SELL','BUY'):'REDUCED',('SELL','HOLD'):'SELL',('SELL','SELL'):'SELL'}
        level = mat.get((fm,ta),'HOLD')
        candidates.append({'symbol':sym,'close':float(row['close']),'score':score,'level':level})

    # 调仓日
    if i % REBAL_FREQ == 0 and candidates:
        for c in candidates:
            if c['level'] in ['SELL','STRONG_SELL'] and c['symbol'] in positions:
                pos = positions.pop(c['symbol'])
                cash += pos['shares']*c['close']*(1-COST_PER)
        buys = sorted([c for c in candidates if c['level'] in ['BUY','STRONG_BUY']],
                      key=lambda x: x['score'], reverse=True)[:MAX_POSITIONS]
        cur_nav = cash + sum(p['shares']*p['cur_price'] for p in positions.values())
        target = cur_nav/len(buys) if buys else 0
        for c in buys:
            sym, px = c['symbol'], c['close']
            if sym in positions or target<=0: continue
            n = int(target/px)
            if n>0 and n*px*(1+COST_PER) <= cash:
                cash -= n*px*(1+COST_PER)
                positions[sym] = {'shares':n,'entry_price':px,'hold_days':0,'cur_price':px}

    day_trades = []
    nav = cash + sum(p['shares']*p['cur_price'] for p in positions.values())
    daily_nav.append({'date':date,'nav':nav,'cash':cash,'n_pos':len(positions)})
    if (i+1)%100==0: W(f"  {i+1}/500  NAV={nav/1e4:.1f}万  持仓={len(positions)}只")

# ── 结果 ──────────────────────────────────────────────────────
nav_df = pd.DataFrame(daily_nav)
nav_df['ret'] = nav_df['nav'].pct_change().fillna(0)
total_ret = (nav_df['nav'].iloc[-1]/INIT_CASH-1)*100
ann_ret   = ((1+total_ret/100)**(250/500)-1)*100
sharpe = nav_df['ret'].mean()/nav_df['ret'].std()*np.sqrt(250) if nav_df['ret'].std()>1e-10 else 0
peak=INIT_CASH; max_dd=0.0
for nav_v in nav_df['nav']:
    if nav_v>peak: peak=nav_v
    dd=(nav_v-peak)/peak
    if dd<max_dd: max_dd=dd

W(f"\n{'='*55}")
W(f"低换手融合框架 回测结果")
W(f"{'='*55}")
W(f"调仓: 每{REBAL_FREQ}日  门槛: score>={MIN_SCORE}")
W(f"成本: {COST_PER*100:.2f}%/笔")
W(f"总收益:   {total_ret:+.2f}%")
W(f"年化:     {ann_ret:+.2f}%")
W(f"Sharpe:   {sharpe:.2f}")
W(f"最大回撤: {max_dd*100:.2f}%")
W(f"基准:     +64.99%")
W(f"Alpha:    {total_ret-64.99:+.2f}%")

nav_df.to_csv('E:/quant/fusion_framework/low_turnover_nav.csv', index=False)
LOG.close()
print("完成!")
