"""
港股融合框架 - 季频版（最终优化版）
- 调仓周期: 60日（季频，每年约4次）
- 持仓上限: 30只
- 止损: -8%
- 数据: 预计算signal缓存（避免重复计算）
"""
import pandas as pd, numpy as np, os, warnings
warnings.filterwarnings('ignore')

REBAL_FREQ    = 60   # 季频：每60交易日（约1季度）
MIN_SCORE     = 3    # 融合打分门槛
INIT_CASH     = 100_0000
MAX_POSITIONS = 30   # 持仓上限扩大
HOLD_MAX      = 30  # 最多持有30天
STOP_LOSS     = 0.92 # 止损-8%
CACHE_DIR     = r'E:\quant\fusion_framework'

LOG = open(rf'{CACHE_DIR}\low_log_quarterly.txt','w',encoding='utf-8')
def W(msg):
    print(msg); LOG.write(msg+'\n'); LOG.flush()

# ── 加载缓存 ──────────────────────────────────────────────
wrsi_cache = rf'{CACHE_DIR}\df_wrsi_cache.csv'
if os.path.exists(wrsi_cache):
    W(f"加载weekly_rsi缓存...")
    df = pd.read_csv(wrsi_cache)
else:
    raise FileNotFoundError(f"需要先运行 hk_low_monthly.py 生成缓存")

df['trade_date'] = pd.to_datetime(df['trade_date'])
df['weekly_rsi'] = pd.to_numeric(df['weekly_rsi'], errors='coerce').fillna(50.0)
df['future_ret_5d'] = df.groupby('symbol')['close'].pct_change(5)

from scipy.stats import spearmanr
recent = df[df['trade_date']>'2025-01-01'].dropna(subset=['weekly_rsi','future_ret_5d'])
recent2 = recent.dropna(subset=['resonance']).copy()
recent2['score'] = 0.202*(recent2['weekly_rsi']-50) + (-0.152)*recent2['resonance']
ic_w = spearmanr(recent['weekly_rsi'], recent['future_ret_5d'])[0]
ic_s = spearmanr(recent2['score'], recent2['future_ret_5d'])[0]
W(f"weekly_rsi IC={ic_w:.4f}  融合打分IC={ic_s:.4f}")

# ── 预计算全部日期的候选信号 ─────────────────────────────────
ALL_DATES = sorted(df['trade_date'].unique())
recent_dates = ALL_DATES[-500:]

W(f"预计算全部日期信号（向量化）...")
date_to_buys = {}
date_to_sells = {}

for i, date in enumerate(recent_dates):
    today = df[df['trade_date']==date].copy()
    today = today.dropna(subset=['weekly_rsi','resonance','rsi_14','macd_hist','ma20_ratio','ma60_ratio'])
    if len(today) < 5: continue

    wrsi  = today['weekly_rsi'].values.copy()
    res   = today['resonance'].values.copy()
    rsi_d = today['rsi_14'].values.copy()
    macd_h = today['macd_hist'].values.copy()
    ma20r  = today['ma20_ratio'].values.copy()
    ma60r  = today['ma60_ratio'].values.copy()

    for arr, fill in [(wrsi,50.0),(rsi_d,50.0),(macd_h,0.0),(ma20r,1.0),(ma60r,1.0),(res,0.0)]:
        arr[np.isnan(arr)] = fill

    score = 0.202*(wrsi-50) + (-0.152)*res
    trend_up = (ma20r > 1.0) & (ma60r > 1.0)
    above20 = ma20r > 1.0
    above60 = ma60r > 1.0
    macd_pos = macd_h > 0

    ta = np.where(above20 & above60 & macd_pos & (rsi_d < 70), 'BUY',
             np.where((rsi_d >= 78) | (~above20 & (rsi_d > 65)), 'SELL', 'HOLD'))
    fm = np.where(score >= MIN_SCORE, 'BUY',
            np.where(score <= -MIN_SCORE, 'SELL', 'HOLD'))

    # 融合矩阵
    level = np.full(len(today), 'HOLD')
    buy_mat_t  = {('BUY','BUY'):'STRONG_BUY',('BUY','HOLD'):'BUY',('BUY','SELL'):'BUY',
                  ('HOLD','BUY'):'BUY',('HOLD','HOLD'):'HOLD',('HOLD','SELL'):'HOLD',
                  ('SELL','BUY'):'BUY',('SELL','HOLD'):'HOLD',('SELL','SELL'):'SELL'}
    sell_mat_t = {('BUY','BUY'):'STRONG_BUY',('BUY','HOLD'):'BUY',('BUY','SELL'):'REDUCED',
                  ('HOLD','BUY'):'HOLD',('HOLD','HOLD'):'HOLD',('HOLD','SELL'):'SELL',
                  ('SELL','BUY'):'REDUCED',('SELL','HOLD'):'SELL',('SELL','SELL'):'SELL'}

    for j in range(len(today)):
        mat = buy_mat_t if bool(trend_up[j]) else sell_mat_t
        level[j] = mat.get((str(fm[j]), str(ta[j])), 'HOLD')

    sym_arr   = today['symbol'].values
    close_arr = today['close'].values
    buys = [(sym_arr[k], score[k], close_arr[k])
            for k in range(len(today)) if level[k] in ['BUY','STRONG_BUY']]
    sells = [sym_arr[k] for k in range(len(today)) if level[k] in ['SELL','STRONG_SELL']]
    date_to_buys[date] = sorted(buys, key=lambda x: -x[1])[:MAX_POSITIONS]
    date_to_sells[date] = sells

    if (i+1) % 100 == 0: W(f"  信号预处理 {i+1}/500 完成")

W("预处理完成，开始回测...")

# ── 季频回测 ───────────────────────────────────────────────
def run_backtest(cost_per_trade, label):
    cash = float(INIT_CASH)
    positions = {}  # {symbol: {'shares', 'cur_price', 'hold_days', 'entry_price'}}
    daily_nav = []
    n_buys = n_sells = n_stop = 0
    equity_curve = []

    for i, date in enumerate(recent_dates):
        today = df[df['trade_date']==date]

        # 更新持仓现价 + 止损
        for sym in list(positions.keys()):
            pr = today[today['symbol']==sym]
            cur_px = float(pr.iloc[0]['close']) if len(pr)>0 else float(
                df[(df['symbol']==sym)&(df['trade_date']<=date)].tail(1).iloc[0]['close'])
            positions[sym]['cur_price'] = cur_px
            positions[sym]['hold_days'] = positions[sym].get('hold_days', 0) + 1

            # 止损检查
            entry = positions[sym]['entry_price']
            cur_nav = positions[sym]['shares'] * cur_px
            cost   = positions[sym]['shares'] * entry
            ret_pct = cur_nav / cost - 1
            if ret_pct <= -(1 - STOP_LOSS):
                cash += cur_nav * (1 - cost_per_trade)
                n_stop += 1
                del positions[sym]
                continue

            # 超长持有
            if positions[sym]['hold_days'] >= HOLD_MAX:
                cash += cur_nav * (1 - cost_per_trade)
                n_sells += 1
                del positions[sym]

        # 季频调仓日
        if i % REBAL_FREQ == 0:
            # 清SELL
            for sym in date_to_sells.get(date, []):
                if sym in positions:
                    pr = today[today['symbol']==sym]
                    px = float(pr.iloc[0]['close']) if len(pr)>0 else positions[sym]['cur_price']
                    cash += positions[sym]['shares'] * px * (1 - cost_per_trade)
                    n_sells += 1
                    del positions[sym]

            # 买入BUY
            buys = date_to_buys.get(date, [])
            cur_nav = cash + sum(p['shares']*p['cur_price'] for p in positions.values())
            target = cur_nav / len(buys) if buys else 0
            for sym, sc, px in buys:
                if sym in positions or target <= 0: continue
                n = int(target / px)
                if n > 0 and n * px * (1 + cost_per_trade) <= cash:
                    cash -= n * px * (1 + cost_per_trade)
                    positions[sym] = {'shares': n, 'cur_price': px,
                                      'hold_days': 0, 'entry_price': px}
                    n_buys += 1

        nav = cash + sum(p['shares']*p['cur_price'] for p in positions.values())
        daily_nav.append({'date': date, 'nav': nav, 'n_pos': len(positions)})

        if (i+1) % 100 == 0:
            W(f"  {i+1}/500  NAV={nav/1e4:.1f}万  持仓={len(positions)}只")

    # 统计
    nav_df = pd.DataFrame(daily_nav)
    nav_df['ret'] = nav_df['nav'].pct_change().fillna(0)
    total_ret = (nav_df['nav'].iloc[-1]/INIT_CASH-1)*100
    ann_ret   = ((1+total_ret/100)**(250/500)-1)*100
    sharpe = nav_df['ret'].mean()/nav_df['ret'].std()*np.sqrt(250) if nav_df['ret'].std()>1e-10 else 0
    peak=INIT_CASH; max_dd=0.0
    for v in nav_df['nav']:
        if v > peak: peak = v
        dd = (v - peak) / peak
        if dd < max_dd: max_dd = dd

    W(f"  [{label}] 换手:{n_buys+n_sells} 止损:{n_stop}次 "
      f"毛总:{total_ret:+.1f}% 年化:{ann_ret:+.1f}% Sharpe:{sharpe:.2f} 回撤:{max_dd*100:.2f}%")
    return nav_df, dict(total=total_ret, ann=ann_ret, sharpe=sharpe,
                        dd=max_dd, buys=n_buys, sells=n_sells, stops=n_stop)

# ── 多情景对比 ──────────────────────────────────────────────
W(f"\n{'='*60}")
W("港股融合框架 - 季频调仓版")
W(f"调仓周期: 每{REBAL_FREQ}日  持仓上限:{MAX_POSITIONS}只  止损:-8%")
W(f"weekly_rsi IC={ic_w:.4f}  融合IC={ic_s:.4f}\n")
W("情景       成本    换手  止损  毛收益   年化    Sharpe  最大回撤")

results = {}
for cost, label in [
    (0.003, "乐观0.3%"),
    (0.005, "基准0.5%"),
    (0.007, "现实0.7%"),
]:
    try:
        nav_df, r = run_backtest(cost, label)
        results[label] = r
        if cost == 0.007:
            nav_df.to_csv(rf'{CACHE_DIR}\quarterly_nav.csv', index=False)
    except Exception as e:
        import traceback; W(f"  [{label}] 错误: {e}\n{traceback.format_exc()}")

# ── 基准对比 ──────────────────────────────────────────────
W(f"\n基准对比:")
W(f"  买入持有top20(无成本): +65.0%  年化+29.6%")
W(f"  月频策略(0.7%现实):   +38.7%  年化+17.8%  (跑输-26.3%)")
if '现实0.7%' in results:
    r = results['现实0.7%']
    gap = r['total'] - 65.0
    W(f"  季频策略(0.7%现实):   {r['total']:+.1f}%  年化{r['ann']:+.1f}%  (跑输{gap:+.1f}%)")

LOG.close()
print("完成!")
