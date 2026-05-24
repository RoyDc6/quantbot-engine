"""
港股融合框架 - 月频版（向量化解法）
修复: iterrows O(n²) -> 布尔索引 O(1)
"""
import pandas as pd, numpy as np, os, warnings
warnings.filterwarnings('ignore')

REBAL_FREQ    = 21
MIN_SCORE     = 3
INIT_CASH     = 100_0000
MAX_POSITIONS = 15
HOLD_MAX      = 15

LOG = open(r'E:\quant\fusion_framework\low_log_v8.txt','w',encoding='utf-8')
def W(msg):
    print(msg); LOG.write(msg+'\n'); LOG.flush()

# ── 加载数据 ─────────────────────────────────────────────────
CACHE = r'E:\quant\fusion_framework\df_wrsi_cache.csv'
if os.path.exists(CACHE):
    W(f"加载缓存...")
    df = pd.read_csv(CACHE)
else:
    W("计算weekly_rsi...")
    df = pd.read_csv('E:/quant/ml_alpha/hk_features_full.csv')
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df[df['volume']>0].copy(); df = df[df['close']>0.5].copy()
    df = df.sort_values(['symbol','trade_date']).reset_index(drop=True)
    wl = []
    for sym, g in df.groupby('symbol'):
        g = g.sort_values('trade_date').reset_index(drop=True)
        close = g['close'].values; n = len(close)
        wrsi = np.full(n, 50.0)
        wc = [close[i*5+4] for i in range(n//5) if i*5+4 < n]
        if len(wc) >= 15:
            d = np.diff(wc); g2 = np.where(d > 0, d, 0.0); l2 = np.where(d < 0, -d, 0.0)
            period = 4
            ag, al = np.mean(g2[:period]), np.mean(l2[:period])
            warr = np.full(len(wc), 50.0)
            warr[period] = 50.0 if al < 1e-10 else 100-100/(1+ag/(al+1e-10))
            for i in range(period+1, len(wc)):
                ag = (ag*(period-1)+g2[i-1])/period
                al = (al*(period-1)+l2[i-1])/period
                warr[i] = 100-100/(1+ag/(al+1e-10)) if al>1e-10 else 100.0
            for i in range(n):
                w = i // 5
                if w < len(warr): wrsi[i] = warr[w]
        tmp = g.copy(); tmp['weekly_rsi'] = wrsi
        wl.append(tmp)
    df = pd.concat(wl, ignore_index=True)
    df.to_csv(CACHE, index=False)

df['trade_date'] = pd.to_datetime(df['trade_date'])
df['weekly_rsi'] = pd.to_numeric(df['weekly_rsi'], errors='coerce').fillna(50.0)
df['future_ret_5d'] = df.groupby('symbol')['close'].pct_change(5)

from scipy.stats import spearmanr
recent = df[df['trade_date']>'2025-01-01'].dropna(subset=['weekly_rsi','future_ret_5d'])
recent2 = recent.dropna(subset=['resonance']).copy()
recent2['score'] = 0.202*(recent2['weekly_rsi']-50) + (-0.152)*recent2['resonance']
ic_w = spearmanr(recent['weekly_rsi'], recent['future_ret_5d'])[0]
ic_s = spearmanr(recent2['score'], recent2['future_ret_5d'])[0]
W(f"weekly_rsi IC={ic_w:.4f}  融合IC={ic_s:.4f}")

# ── 预处理：向量化计算所有信号 ─────────────────────────────────
# 一次性计算每只股票每天的信号（避免每日期循环）
W("预处理全部信号（向量化）...")
ALL_DATES = sorted(df['trade_date'].unique())
recent_dates = ALL_DATES[-500:]

# 每个日期的候选BUY列表（预计算）
date_to_buys = {}  # {date: [(symbol, score, close), ...]}
date_to_sells = {}

for i, date in enumerate(recent_dates):
    today = df[df['trade_date']==date].copy()
    today = today.dropna(subset=['weekly_rsi','resonance','rsi_14','macd_hist','ma20_ratio','ma60_ratio'])
    if len(today) < 10: continue

    wrsi  = today['weekly_rsi'].values.copy()
    res   = today['resonance'].values.copy()
    rsi_d = today['rsi_14'].values.copy()
    macd_h = today['macd_hist'].values.copy()
    ma20r  = today['ma20_ratio'].values.copy()
    ma60r  = today['ma60_ratio'].values.copy()

    # 填充NaN
    wrsi[np.isnan(wrsi)]  = 50.0
    rsi_d[np.isnan(rsi_d)] = 50.0
    macd_h[np.isnan(macd_h)] = 0.0
    ma20r[np.isnan(ma20r)]  = 1.0
    ma60r[np.isnan(ma60r)]  = 1.0
    res[np.isnan(res)]      = 0.0

    score = 0.202*(wrsi-50) + (-0.152)*res
    trend_up = (ma20r > 1.0) & (ma60r > 1.0)
    above20 = ma20r > 1.0
    above60 = ma60r > 1.0
    macd_pos = macd_h > 0

    ta = np.where(above20 & above60 & macd_pos & (rsi_d < 70), 'BUY',
             np.where((rsi_d >= 78) | (~above20 & (rsi_d > 65)), 'SELL', 'HOLD'))

    fm = np.where(score >= MIN_SCORE, 'BUY',
            np.where(score <= -MIN_SCORE, 'SELL', 'HOLD'))

    # 融合矩阵（向量化）
    level = np.full(len(today), 'HOLD')
    for j in range(len(today)):
        tu = bool(trend_up[j])
        ts = (str(fm[j]), str(ta[j]))
        if tu:
            level[j] = {('BUY','BUY'):'STRONG_BUY',('BUY','HOLD'):'BUY',('BUY','SELL'):'BUY',
                         ('HOLD','BUY'):'BUY',('HOLD','HOLD'):'HOLD',('HOLD','SELL'):'HOLD',
                         ('SELL','BUY'):'BUY',('SELL','HOLD'):'HOLD',('SELL','SELL'):'SELL'}.get(ts, 'HOLD')
        else:
            level[j] = {('BUY','BUY'):'STRONG_BUY',('BUY','HOLD'):'BUY',('BUY','SELL'):'REDUCED',
                         ('HOLD','BUY'):'HOLD',('HOLD','HOLD'):'HOLD',('HOLD','SELL'):'SELL',
                         ('SELL','BUY'):'REDUCED',('SELL','HOLD'):'SELL',('SELL','SELL'):'SELL'}.get(ts, 'HOLD')

    sym_arr = today['symbol'].values
    close_arr = today['close'].values

    buys = [(sym_arr[j], score[j], close_arr[j])
            for j in range(len(today)) if level[j] in ['BUY','STRONG_BUY']]
    sells = [sym_arr[j] for j in range(len(today)) if level[j] in ['SELL','STRONG_SELL']]

    date_to_buys[date] = sorted(buys, key=lambda x: -x[1])[:MAX_POSITIONS]
    date_to_sells[date] = sells

    if (i+1) % 100 == 0: W(f"  信号预处理 {i+1}/500 完成")

W("预处理完成，开始回测...")

# ── 向量化回测 ──────────────────────────────────────────────
def run_backtest(cost_per_trade, label):
    cash = float(INIT_CASH)
    positions = {}  # {symbol: {'shares':N,'cur_price':P,'hold_days':N}}
    daily_nav = []
    n_buys = n_sells = 0

    for i, date in enumerate(recent_dates):
        today = df[df['trade_date']==date]
        # 更新持仓现价 + 持有天数
        for sym in list(positions.keys()):
            pr = today[today['symbol']==sym]
            if len(pr) > 0:
                positions[sym]['cur_price'] = float(pr.iloc[0]['close'])
            else:
                positions[sym]['cur_price'] = float(
                    df[(df['symbol']==sym)&(df['trade_date']<=date)].tail(1).iloc[0]['close'])
            positions[sym]['hold_days'] = positions[sym].get('hold_days',0) + 1
            # 超长持有
            if positions[sym]['hold_days'] >= HOLD_MAX:
                pos = positions.pop(sym)
                cash += pos['shares']*pos['cur_price']*(1-cost_per_trade)
                n_sells += 1

        # 调仓日
        if i % REBAL_FREQ == 0:
            # 清SELL
            sell_list = date_to_sells.get(date, [])
            for sym in sell_list:
                if sym in positions:
                    pos = positions.pop(sym)
                    pr = today[today['symbol']==sym]
                    px = float(pr.iloc[0]['close']) if len(pr)>0 else positions[sym]['cur_price']
                    cash += pos['shares']*px*(1-cost_per_trade)
                    n_sells += 1

            # 买BUY
            buy_list = date_to_buys.get(date, [])
            cur_nav = cash + sum(p['shares']*p['cur_price'] for p in positions.values())
            target = cur_nav/len(buy_list) if buy_list else 0
            for sym, sc, px in buy_list:
                if sym in positions or target<=0: continue
                n = int(target/px)
                if n>0 and n*px*(1+cost_per_trade) <= cash:
                    cash -= n*px*(1+cost_per_trade)
                    positions[sym] = {'shares':n,'cur_price':px,'hold_days':0}
                    n_buys += 1

        nav = cash + sum(p['shares']*p['cur_price'] for p in positions.values())
        daily_nav.append({'date':date,'nav':nav,'n_pos':len(positions)})

        if (i+1) % 100 == 0: W(f"  {i+1}/500  NAV={nav/1e4:.1f}万  持仓={len(positions)}只")

    nav_df = pd.DataFrame(daily_nav)
    nav_df['ret'] = nav_df['nav'].pct_change().fillna(0)
    total_ret = (nav_df['nav'].iloc[-1]/INIT_CASH-1)*100
    ann_ret = ((1+total_ret/100)**(250/500)-1)*100
    sharpe = nav_df['ret'].mean()/nav_df['ret'].std()*np.sqrt(250) if nav_df['ret'].std()>1e-10 else 0
    peak=INIT_CASH; max_dd=0.0
    for v in nav_df['nav']:
        if v>peak: peak=v
        dd=(v-peak)/peak
        if dd<max_dd: max_dd=dd
    W(f"  [{label}] 换手:{n_buys+n_sells} 毛总:{total_ret:+.1f}% 年化:{ann_ret:+.1f}% Sharpe:{sharpe:.2f} 回撤:{max_dd*100:.2f}%")
    return nav_df

# ── 运行 ──────────────────────────────────────────────────────
W(f"\n{'='*60}")
W("港股融合框架 - 月频多情景分析")
W(f"weekly_rsi IC={ic_w:.4f}  融合IC={ic_s:.4f}")
W(f"调仓: 每{REBAL_FREQ}日  持仓上限:{MAX_POSITIONS}只\n")
W("情景       成本    换手  毛收益   年化    Sharpe  回撤")

for cost, label in [
    (0.003, "乐观0.3%"),
    (0.005, "基准0.5%"),
    (0.007, "现实0.7%"),
    (0.010, "谨慎1.0%"),
]:
    try:
        nav_df = run_backtest(cost, label)
        if cost == 0.007:
            nav_df.to_csv(r'E:\quant\fusion_framework\low_monthly_nav.csv', index=False)
    except Exception as e:
        import traceback; W(f"  [{label}] 错误: {e}\n{traceback.format_exc()}")

W(f"\n基准: 等权持有top20 同期 +64.99%")
LOG.close()
print("完成!")
