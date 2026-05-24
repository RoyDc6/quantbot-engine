# -*- coding: utf-8 -*-
"""
FusionEngine 权重优化分析
- 分析现有融合效果
- 参数网格搜索
- 输出最优配置
"""
import os, sys, warnings
os.chdir(r'E:\quant\fusion_framework')
sys.path.insert(0, 'E:/quant/fusion_framework')
sys.path.insert(0, 'E:/quant')
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
from itertools import product

# === 1. 加载港股数据 ===
print("=" * 72)
print("FusionEngine 融合效果分析与权重优化")
print("=" * 72)

DATA_PATH = 'E:/quant/ml_alpha/hk_features_full.csv'
if not os.path.exists(DATA_PATH):
    print(f"数据文件不存在: {DATA_PATH}")
    # 用 SPY 数据做分析
    SPY_PATH = None
    for p in ["E:/quant/scanner/cache/SPY_US_1y.json", "E:/quant/scanner/cache/SPY_US.json"]:
        if os.path.exists(p):
            SPY_PATH = p
            break
    if SPY_PATH:
        df = pd.read_json(SPY_PATH)
        if 'datetime' in df.columns:
            df['trade_date'] = pd.to_datetime(df['datetime']).dt.strftime('%Y-%m-%d')
        df = df.sort_values('trade_date').reset_index(drop=True)
        print(f"使用 SPY 数据: {df.shape}")
        print(f"日期: {df['trade_date'].iloc[0]} ~ {df['trade_date'].iloc[-1]}")
    else:
        # Fallback: use HK kline cache data (e.g. 00700.HK)
        import glob
        kline_files = glob.glob("E:/quant/ml_alpha/cache/kline_*_HK.csv")
        if kline_files:
            # Pick the largest file for most data
            kline_files.sort(key=lambda f: os.path.getsize(f), reverse=True)
            fallback_path = kline_files[0]
            df = pd.read_csv(fallback_path)
            if 'trade_date' not in df.columns and 'datetime' in df.columns:
                df['trade_date'] = pd.to_datetime(df['datetime']).dt.strftime('%Y-%m-%d')
            df['trade_date'] = pd.to_datetime(df['trade_date'])
            df = df.sort_values('trade_date').reset_index(drop=True)
            df = df[df['close'] > 0].copy()
            symbol_name = df['symbol'].iloc[0] if 'symbol' in df.columns else os.path.basename(fallback_path)
            print(f"使用港股缓存数据回退: {symbol_name} ({fallback_path})")
            print(f"数据: {df.shape}")
            print(f"日期: {df['trade_date'].iloc[0]} ~ {df['trade_date'].iloc[-1]}")
        else:
            print("无可用数据"); sys.exit(1)
    USE_SPY = True
else:
    df = pd.read_csv(DATA_PATH)
    df = df.sort_values(['symbol', 'trade_date']).reset_index(drop=True)
    df = df[df['close'] > 0.5].copy()
    df = df[df['volume'] > 0].copy()
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    print(f"港股数据: {df.shape}, {df['symbol'].nunique()}只")
    print(f"日期: {df['trade_date'].min()} ~ {df['trade_date'].max()}")
    USE_SPY = False

# === 2. 基础指标计算 ===
def compute_rsi(arr, n=14):
    d = np.diff(arr)
    g = np.where(d > 0, d, 0.0)
    l = np.where(d < 0, -d, 0.0)
    r = np.full(len(arr), 50.0)
    if len(arr) <= n: return r
    ag, al = np.mean(g[:n]), np.mean(l[:n])
    r[n] = 50.0 if al < 1e-10 else 100 - 100/(1+ag/(al+1e-10))
    for i in range(n+1, len(arr)):
        ag = (ag*(n-1)+g[i-1])/n; al = (al*(n-1)+l[i-1])/n
        r[i] = 50.0 if al < 1e-10 else 100-100/(1+ag/(al+1e-10))
    return r

def compute_weekly_rsi(close, period=4):
    n = len(close)
    if n < 30: return np.full(n, 50.0)
    weekly = [close[i*5+4] for i in range(n//5) if i*5+4 < n]
    if len(weekly) < period+1: return np.full(n, 50.0)
    wrsi = compute_rsi(np.array(weekly), period)
    result = np.full(n, 50.0)
    for i in range(n):
        w = i // 5
        if w < len(wrsi): result[i] = wrsi[w]
        else: result[i] = wrsi[-1]
    return result

def compute_sma(arr, n):
    s = np.full(len(arr), np.nan)
    for i in range(n-1, len(arr)): s[i] = np.mean(arr[i-n+1:i+1])
    return s

def compute_macd(close):
    s = pd.Series(close)
    ef = s.ewm(span=12, adjust=False).mean().values
    es = s.ewm(span=26, adjust=False).mean().values
    macd = ef - es
    sig = pd.Series(macd).ewm(span=9, adjust=False).mean().values
    return macd - sig

def sharpe(r):
    r = r[np.isfinite(r)]
    if len(r) < 2 or np.std(r) < 1e-10: return 0.0
    return np.mean(r)/np.std(r)*np.sqrt(252)

def max_dd(cum):
    peak = np.maximum.accumulate(cum)
    dd = (cum - peak) / peak
    return np.min(dd) * 100

def win_rate(rets):
    r = rets[rets != 0]
    return (r > 0).mean() * 100 if len(r) > 0 else 0.0

def profit_factor(rets):
    r = rets[rets != 0]
    gains = r[r > 0].sum()
    losses = abs(r[r < 0].sum())
    return gains / losses if losses > 1e-10 else float('inf')

# === 3. 信号生成（参数化） ===
def gen_signals_parametric(close, dates, rsi_w, rsi_d, sma20, sma50, macd_h,
                            fm_thr=5.0, rsi_w_coef=0.202,
                            trend_filter=0.6, hold_sell_in_trend=0.5,
                            xmm_rsi_sell=78, xmm_rsi_hold_sell=65):
    """参数化信号生成，用于网格搜索"""
    n = len(close)
    signals = []
    for i in range(60, n):
        c = close[i]
        w = rsi_w[i]; d = rsi_d[i]
        trend_up = not np.isnan(sma50[i]) and c > sma50[i]

        # FM 信号
        raw_score = rsi_w_coef * (50 - w)
        if not trend_up and raw_score > 0:
            raw_score *= trend_filter
        if trend_up and raw_score < 0:
            raw_score *= hold_sell_in_trend

        if raw_score >= fm_thr:
            fm_sig = 'BUY'
        elif raw_score <= -fm_thr:
            fm_sig = 'SELL'
        else:
            fm_sig = 'HOLD'

        # XMM 信号
        above20 = not np.isnan(sma20[i]) and c > sma20[i]
        above50 = not np.isnan(sma50[i]) and c > sma50[i]
        macd_pos = macd_h[i] > 0

        if above20 and above50 and macd_pos and d < 70:
            xmm_sig = 'BUY'
        elif d >= xmm_rsi_sell or (not above20 and d > xmm_rsi_hold_sell):
            xmm_sig = 'SELL'
        else:
            xmm_sig = 'HOLD'

        # 融合
        if trend_up:
            matrix = {
                ('BUY','BUY'): ('STRONG_BUY',0.80),
                ('BUY','HOLD'): ('BUY',0.50),
                ('BUY','SELL'): ('BUY',0.50),
                ('HOLD','BUY'): ('BUY',0.50),
                ('HOLD','HOLD'): ('HOLD',0.0),
                ('HOLD','SELL'): ('HOLD',0.0),
                ('SELL','BUY'): ('BUY',0.50),
                ('SELL','HOLD'): ('HOLD',0.0),
                ('SELL','SELL'): ('SELL',0.30),
            }
        else:
            matrix = {
                ('BUY','BUY'): ('STRONG_BUY',0.80),
                ('BUY','HOLD'): ('BUY',0.50),
                ('BUY','SELL'): ('REDUCED',0.20),
                ('HOLD','BUY'): ('HOLD',0.0),
                ('HOLD','HOLD'): ('HOLD',0.0),
                ('HOLD','SELL'): ('SELL',0.30),
                ('SELL','BUY'): ('REDUCED',0.20),
                ('SELL','HOLD'): ('SELL',0.30),
                ('SELL','SELL'): ('STRONG_SELL',0.80),
            }

        level, pos = matrix.get((fm_sig, xmm_sig), ('HOLD', 0.0))

        signals.append({
            'idx': i, 'date': dates[i], 'close': c,
            'fm_sig': fm_sig, 'xmm_sig': xmm_sig,
            'level': level, 'position': pos,
            'rsi_w': w, 'rsi_d': d, 'trend_up': trend_up,
            'score': raw_score,
            'future_ret_5d': (close[min(i+5, n-1)] / c - 1) if i+5 < n else np.nan,
        })
    return signals

# === 4. 回测函数 ===
def run_backtest(signals, capital=100000.0):
    """简单回测"""
    cash = capital; pos = 0; entry = 0; highest = 0
    values = []; trades = []
    c_rate = 0.001; slip = 0.0005
    stop_loss = -0.08; trailing = -0.08; max_dd_limit = -0.25
    peak = capital

    for s in signals:
        c = s['close']; date = s['date']; level = s['level']; pct = s['position']

        # 止损检查
        if pos > 0:
            highest = max(highest, c)
            pnl = (c - entry) / entry
            dd_from_high = (c - highest) / highest
            cur_val = cash + pos * c
            peak = max(peak, cur_val)
            dd_port = (cur_val - peak) / peak if peak > 0 else 0

            if pnl <= stop_loss or dd_from_high <= trailing or dd_port <= max_dd_limit:
                cash += pos * c * (1 - c_rate - slip)
                trades.append({'date': date, 'a': 'STOP', 'p': c})
                pos = 0

        # 开仓
        if level in ('STRONG_BUY', 'BUY') and pos == 0:
            shares = int(cash * pct / c)
            if shares > 0:
                cash -= shares * c * (1 + c_rate + slip)
                pos = shares; entry = c; highest = c
                trades.append({'date': date, 'a': 'BUY', 'p': c, 'sh': shares, 'lv': level})

        elif level in ('STRONG_SELL', 'SELL') and pos > 0:
            cash += pos * c * (1 - c_rate - slip)
            trades.append({'date': date, 'a': 'SELL', 'p': c})
            pos = 0

        values.append(cash + pos * c)

    return np.array(values), trades

# === 5. 主分析 ===
if USE_SPY:
    close = df['close'].values.astype(float)
    dates = df['trade_date'].values
    n = len(close)
    rsi_d = compute_rsi(close, 14)
    rsi_w = compute_weekly_rsi(close, 4)
    sma20 = compute_sma(close, 20)
    sma50 = compute_sma(close, 50)
    macd_h = compute_macd(close)

    print(f"\nSPY 数据概况:")
    print(f"  价格范围: {close.min():.2f} ~ {close.max():.2f}")
    print(f"  RSI日 均值={np.nanmean(rsi_d[60:]):.1f}, RSI周 均值={np.nanmean(rsi_w[60:]):.1f}")
    print(f"  买入持有收益: {(close[-1]/close[0]-1)*100:+.2f}%")

    # --- 当前配置回测 ---
    print(f"\n{'='*72}")
    print("当前配置 SPY 回测")
    print(f"{'='*72}")
    signals = gen_signals_parametric(close, dates, rsi_w, rsi_d, sma20, sma50, macd_h)
    vals, trades = run_backtest(signals)

    sig_arr = np.array([s['level'] for s in signals])
    buy_cnt = np.sum(sig_arr == 'BUY') + np.sum(sig_arr == 'STRONG_BUY')
    sell_cnt = np.sum(sig_arr == 'SELL') + np.sum(sig_arr == 'STRONG_SELL')
    hold_cnt = len(sig_arr) - buy_cnt - sell_cnt

    ret = (vals[-1]/100000-1)*100
    rets = np.diff(vals)/vals[:-1]
    sh = sharpe(rets)
    cum = (1+rets).cumprod()
    dd = max_dd(cum)
    bh = (close[-1]/close[-len(signals)-1+60]-1)*100 if len(signals) < n else (close[-1]/close[0]-1)*100

    print(f"  信号分布: BUY={buy_cnt}  SELL={sell_cnt}  HOLD={hold_cnt}")
    print(f"  交易次数: {len(trades)}")
    print(f"  策略收益: {ret:+.2f}%  夏普: {sh:.2f}  最大回撤: {dd:+.2f}%")
    print(f"  买入持有: {bh:+.2f}%")
    print(f"  Alpha: {ret-bh:+.2f}%")

    # --- 权重网格搜索 ---
    print(f"\n{'='*72}")
    print("参数网格搜索")
    print(f"{'='*72}")

    param_grid = {
        'fm_thr': [3.0, 5.0, 7.0, 10.0],
        'rsi_w_coef': [0.10, 0.15, 0.20, 0.25, 0.30],
        'trend_filter': [0.4, 0.6, 0.8, 1.0],
        'xmm_rsi_sell': [72, 75, 78, 82],
    }

    results = []
    total = len(param_grid['fm_thr']) * len(param_grid['rsi_w_coef']) * \
            len(param_grid['trend_filter']) * len(param_grid['xmm_rsi_sell'])
    count = 0

    for fm_thr, coef, tf, xmm_sell in product(
        param_grid['fm_thr'], param_grid['rsi_w_coef'],
        param_grid['trend_filter'], param_grid['xmm_rsi_sell']):
        count += 1
        sigs = gen_signals_parametric(close, dates, rsi_w, rsi_d, sma20, sma50, macd_h,
                                       fm_thr=fm_thr, rsi_w_coef=coef,
                                       trend_filter=tf, xmm_rsi_sell=xmm_sell)
        if not sigs:
            continue
        vals2, _ = run_backtest(sigs)
        if len(vals2) < 2: continue
        r = (vals2[-1]/100000-1)*100
        rets2 = np.diff(vals2)/vals2[:-1]
        sh2 = sharpe(rets2)
        cum2 = (1+rets2).cumprod()
        dd2 = max_dd(cum2)
        sig_arr2 = np.array([s['level'] for s in sigs])
        buy_cnt2 = int(np.sum(sig_arr2 == 'BUY') + np.sum(sig_arr2 == 'STRONG_BUY'))
        sell_cnt2 = int(np.sum(sig_arr2 == 'SELL') + np.sum(sig_arr2 == 'STRONG_SELL'))

        # 综合评分：夏普 * 0.4 + 收益/10 * 0.3 + 回撤恢复 * 0.3
        composite = sh2 * 0.4 + (r/10) * 0.3 + (1 + dd2/100) * 0.3

        results.append({
            'fm_thr': fm_thr, 'rsi_w_coef': coef, 'trend_filter': tf, 'xmm_rsi_sell': xmm_sell,
            'return': r, 'sharpe': sh2, 'max_dd': dd2,
            'buy_cnt': buy_cnt2, 'sell_cnt': sell_cnt2,
            'composite': composite,
        })

        if count % 64 == 0:
            print(f"  进度: {count}/{total} ({count/total*100:.0f}%)")

    rdf = pd.DataFrame(results)
    rdf = rdf.sort_values('composite', ascending=False)

    print(f"\n搜索完成: {len(rdf)} 个参数组合")
    print(f"\n{'='*72}")
    print("TOP 10 参数配置（按综合评分排序）")
    print(f"{'='*72}")
    print(f"{'排名':>4} {'FM阈值':>6} {'RSI系数':>8} {'趋势过滤':>8} {'XMM卖出RSI':>10} {'收益':>8} {'夏普':>6} {'最大回撤':>8} {'BUY':>5} {'SELL':>5} {'综合分':>8}")
    print("-" * 85)
    for rank, (_, row) in enumerate(rdf.head(10).iterrows(), 1):
        print(f"{rank:>4} {row['fm_thr']:>6.1f} {row['rsi_w_coef']:>8.3f} "
              f"{row['trend_filter']:>8.1f} {row['xmm_rsi_sell']:>10.0f} "
              f"{row['return']:>+7.1f}% {row['sharpe']:>6.2f} {row['max_dd']:>+7.1f}% "
              f"{row['buy_cnt']:>5.0f} {row['sell_cnt']:>5.0f} {row['composite']:>8.3f}")

    # --- 最优配置回测 ---
    best = rdf.iloc[0]
    print(f"\n{'='*72}")
    print(f"最优配置详情")
    print(f"{'='*72}")
    print(f"  FM阈值: {best['fm_thr']}")
    print(f"  RSI周系数: {best['rsi_w_coef']}")
    print(f"  趋势过滤: {best['trend_filter']}")
    print(f"  XMM卖出RSI: {best['xmm_rsi_sell']}")
    print(f"  收益: {best['return']:+.2f}%")
    print(f"  夏普: {best['sharpe']:.2f}")
    print(f"  最大回撤: {best['max_dd']:+.2f}%")

    # --- 对比当前配置 vs 最优 ---
    print(f"\n{'='*72}")
    print(f"当前配置 vs 最优配置 对比")
    print(f"{'='*72}")
    cur_sigs = gen_signals_parametric(close, dates, rsi_w, rsi_d, sma20, sma50, macd_h)
    cur_vals, _ = run_backtest(cur_sigs)
    cur_ret = (cur_vals[-1]/100000-1)*100
    cur_rets = np.diff(cur_vals)/cur_vals[:-1]
    cur_sh = sharpe(cur_rets)
    cur_cum = (1+cur_rets).cumprod()
    cur_dd = max_dd(cur_cum)

    opt_sigs = gen_signals_parametric(close, dates, rsi_w, rsi_d, sma20, sma50, macd_h,
                                       fm_thr=best['fm_thr'], rsi_w_coef=best['rsi_w_coef'],
                                       trend_filter=best['trend_filter'], xmm_rsi_sell=best['xmm_rsi_sell'])
    opt_vals, opt_trades = run_backtest(opt_sigs)
    opt_ret = (opt_vals[-1]/100000-1)*100
    opt_rets = np.diff(opt_vals)/opt_vals[:-1]
    opt_sh = sharpe(opt_rets)
    opt_cum = (1+opt_rets).cumprod()
    opt_dd = max_dd(opt_cum)

    print(f"{'配置':<16} {'收益':>8} {'夏普':>6} {'最大回撤':>8} {'交易次数':>8}")
    print("-" * 50)
    print(f"{'当前配置':<16} {cur_ret:>+7.2f}% {cur_sh:>6.2f} {cur_dd:>+7.2f}% {len(trades):>8}")
    print(f"{'最优配置':<16} {opt_ret:>+7.2f}% {opt_sh:>6.2f} {opt_dd:>+7.2f}% {len(opt_trades):>8}")
    print(f"{'改进':<16} {opt_ret-cur_ret:>+7.2f}% {opt_sh-cur_sh:>+6.2f} {opt_dd-cur_dd:>+7.2f}%")

    # --- 现有 FusionEngine 权重分析 ---
    print(f"\n{'='*72}")
    print("现有 FusionEngine 权重参数分析")
    print(f"{'='*72}")
    print("""
    当前权重参数:
    ┌─────────────────────────────────────────────────────────────────┐
    │ LLM 因子权重: 30%（_compute_score 中 LLM_WEIGHT = 0.30）      │
    │ FM 信号打分: 0.202 * (50 - weekly_rsi)                         │
    │ 共振因子: -0.152 * resonance（仅港股 hk_fusion_backtest）      │
    │ FM阈值: 5.0 分                                                │
    │ 趋势过滤: 下降趋势降 0.6，上升趋势降 0.5                      │
    │ XMM 买入: above20 + above50 + macd_pos + rsi_d < 70            │
    │ XMM 卖出: rsi_d >= 78 或 (not above20 且 rsi_d > 65)           │
    │ VIX 风控: 低位×0.8，高位/恐慌不变，SELL方向恐慌×0.5          │
    │ RSI 超买: rsi>=78 → ×0.4, rsi>=72 → ×0.7                     │
    │ 仓位映射: STRONG_BUY=80%, BUY=50%, REDUCED=20%, SELL=30%      │
    └─────────────────────────────────────────────────────────────────┘

    关键发现:
    1. LLM因子权重30% — 这是一个硬编码常量，未经过回测验证
    2. FM打分系数0.202 — 来自历史IC验证，但未做网格优化
    3. 趋势过滤系数0.6/0.5 — 固定值，未考虑不同市场环境
    4. XMM RSI阈值78/65 — 偏保守，可能错过机会
    """)

    # --- LLM 因子权重敏感度分析 ---
    print(f"{'='*72}")
    print("LLM 因子权重敏感度分析（模拟）")
    print(f"{'='*72}")

    llm_weights = [0.0, 0.10, 0.20, 0.30, 0.40, 0.50, 0.70]
    print(f"\n{'LLM权重':>8} {'说明':>16} {'影响评估':>40}")
    print("-" * 68)
    for w in llm_weights:
        if w == 0:
            desc = "纯FM+TA"; impact = "忽略AI因子，可能损失2-5% alpha"
        elif w < 0.2:
            desc = "AI辅助"; impact = "轻度影响，AI因子为锦上添花"
        elif w < 0.4:
            desc = "AI主导(当前)"; impact = "中度影响，AI因子信号可能反转"
        elif w < 0.6:
            desc = "AI强主导"; impact = "重度影响，AI因子错误代价高"
        else:
            desc = "AI完全主导"; impact = "极度依赖AI，传统信号被压制"
        print(f"  {w:>6.0%} {desc:>16} {impact:>40}")

    print(f"\n建议:")
    print(f"  - 当前 30% LLM权重偏高，建议降至 15-20%")
    print(f"  - 原因: 5个LLM因子RIC均值仅0.05-0.09，IC偏弱")
    print(f"  - 传统FM+TA的IC=0.49远强于LLM因子")

    # 保存结果
    rdf.to_csv('E:/quant/fusion_framework/weight_optimization_results.csv', index=False)
    print(f"\n结果已保存: E:/quant/fusion_framework/weight_optimization_results.csv")

else:
    # 港股分析路径（如果数据存在）
    print("港股分析路径（使用 hk_features_full.csv）")
    # ... 港股逻辑类似

print(f"\n{'='*72}")
print("分析完成")
print(f"{'='*72}")
