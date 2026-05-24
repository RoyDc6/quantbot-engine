# -*- coding: utf-8 -*-
"""港股融合回测 - 输出到文件"""
import os, sys, warnings, traceback
os.chdir(r'E:\quant\fusion_framework')
sys.path.insert(0, 'E:/quant/fusion_framework')
sys.path.insert(0, 'E:/quant')
warnings.filterwarnings('ignore')
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

LOG = open('E:/quant/fusion_framework/hk_log.txt', 'w', encoding='utf-8')

def log(msg):
    print(msg)
    LOG.write(str(msg) + '\n')
    LOG.flush()

try:
    log("="*60)
    log("港股融合回测 开始")
    log("="*60)

    # 加载数据
    log("加载数据...")
    df = pd.read_csv('E:/quant/ml_alpha/hk_features_full.csv')
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df[df['volume'] > 0].copy()
    df = df[df['close'] > 0.5].copy()
    log(f"数据: {len(df)}行 x {len(df.columns)}列, {df['symbol'].nunique()}只股票")

    # 计算周RSI
    log("计算周RSI...")
    ALL_SYMBOLS = sorted(df['symbol'].unique())
    ALL_DATES = sorted(df['trade_date'].unique())
    log(f"股票: {len(ALL_SYMBOLS)}只, 日期: {len(ALL_DATES)}天")

    weekly_list = []
    for sym, g in df.groupby('symbol'):
        g = g.sort_values('trade_date')
        close = g['close'].values
        n = len(close)
        wrsi = np.full(n, np.nan)
        weekly_close = [close[i*5+4] for i in range(n//5) if i*5+4 < n]
        weekly_rsi = np.full(len(weekly_close), 50.0)
        if len(weekly_close) >= 15:
            d = np.diff(weekly_close)
            g2 = np.where(d > 0, d, 0.0)
            l2 = np.where(d < 0, -d, 0.0)
            period = 4
            ag, al = np.mean(g2[:period]), np.mean(l2[:period])
            weekly_rsi[period] = 50.0 if al < 1e-10 else 100 - 100 / (1 + ag / (al + 1e-10))
            for i in range(period+1, len(weekly_close)):
                ag = (ag * (period-1) + g2[i-1]) / period
                al = (al * (period-1) + l2[i-1]) / period
                weekly_rsi[i] = 100 - 100 / (1 + ag / (al + 1e-10)) if al > 1e-10 else 100.0
        for i in range(n):
            w = i // 5
            if w < len(weekly_rsi): wrsi[i] = weekly_rsi[w]
        tmp = g.copy()
        tmp['weekly_rsi'] = wrsi
        weekly_list.append(tmp)

    df = pd.concat(weekly_list, ignore_index=True)
    log(f"周RSI完成, 有效: {df['weekly_rsi'].notna().sum()}条")

    # 信号生成
    def gen_sigs(date):
        sigs = []
        today_df = df[df['trade_date'] == date].copy()
        if len(today_df) < 3: return sigs
        for _, row in today_df.iterrows():
            sym = row['symbol']
            hist = df[(df['symbol'] == sym) & (df['trade_date'] <= date)].tail(60)
            if len(hist) < 60: continue
            rsi_w = row.get('weekly_rsi', 50.0)
            rsi_d = row.get('rsi_14', 50.0)
            macd_h = row.get('macd_hist', 0.0)
            ma20r = row.get('ma20_ratio', 1.0)
            ma60r = row.get('ma60_ratio', 1.0)
            resonance = row.get('resonance', 0.0)
            if np.isnan(rsi_w): rsi_w = 50.0
            if np.isnan(rsi_d): rsi_d = 50.0
            if np.isnan(macd_h): macd_h = 0.0
            if np.isnan(ma20r): ma20r = 1.0
            if np.isnan(ma60r): ma60r = 1.0
            if np.isnan(resonance): resonance = 0.0
            # 港股是动量市场：高wrsi→高收益，IC(wrsi)=+0.2156
            # 融合打分：wrsi动量 + resonance反转（高resonance=没alpha）
            score = 0.202 * (rsi_w - 50) + (-0.152) * resonance
            trend_up = ma20r > 1.0 and ma60r > 1.0
            fm_sig = 'BUY' if score >= 5 else ('SELL' if score <= -5 else 'HOLD')
            above20 = ma20r > 1.0; above60 = ma60r > 1.0; macd_pos = macd_h > 0
            if above20 and above60 and macd_pos and rsi_d < 70:
                xmm_sig = 'BUY'
            elif rsi_d >= 78 or (not above20 and rsi_d > 65):
                xmm_sig = 'SELL'
            else:
                xmm_sig = 'HOLD'
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
                'fm_sig': fm_sig, 'xmm_sig': xmm_sig,
                'score': score, 'rsi_w': rsi_w, 'rsi_d': rsi_d,
                'trend_up': trend_up, 'level': level_str, 'position': pos,
            })
        return sigs

    # IC验证（最近500天）
    log("\nIC验证（最近500天）...")
    recent_dates = ALL_DATES[-500:]
    log(f"回测区间: {recent_dates[0].date()} ~ {recent_dates[-1].date()}")

    for factor in ['weekly_rsi', 'resonance', 'macd_hist', 'ma20_ratio']:
        vals = df[df['trade_date'].isin(recent_dates)].dropna(subset=[factor, 'future_ret_5d'])
        if len(vals) > 50:
            ic, _ = spearmanr(vals[factor], vals['future_ret_5d'], nan_policy='omit')
            if not np.isnan(ic):
                stars = '★' * min(5, max(1, int(abs(ic) * 20)))
                sign = '+' if ic > 0 else '-'
                log(f"  {factor:<18} IC={sign}{abs(ic):.4f}{stars}  n={len(vals)}")

    # 融合打分IC
    log("生成融合打分...")
    sig_scores = []
    for date in recent_dates[:50]:  # 先测50天
        sigs = gen_sigs(date)
        for s in sigs:
            if not np.isnan(s['future_ret_5d']):
                sig_scores.append({'score': s['score'], 'future_ret_5d': s['future_ret_5d']})
    log(f"打分IC样本: {len(sig_scores)}条")
    if len(sig_scores) > 50:
        tmp_df = pd.DataFrame(sig_scores)
        ic2, _ = spearmanr(tmp_df['score'], tmp_df['future_ret_5d'], nan_policy='omit')
        log(f"  融合打分IC={ic2:+.4f}")

    # 回测
    log("\n开始回测（500天）...")
    port_rets = []; bench_rets = []; n_holds = []
    for i, date in enumerate(recent_dates):
        if i % 50 == 0:
            log(f"  进度: {i}/{len(recent_dates)} ({date.date()})")
        sigs = gen_sigs(date)
        if not sigs: continue
        buy_df = pd.DataFrame([s for s in sigs if s['level'] in ('BUY','STRONG_BUY') and not np.isnan(s['future_ret_5d'])])
        bench_df = pd.DataFrame([s for s in sigs if not np.isnan(s['future_ret_5d'])])
        if len(buy_df) > 0:
            port_rets.append(buy_df['future_ret_5d'].mean() / 5)
            n_holds.append(len(buy_df))
        else:
            port_rets.append(0.0)
            n_holds.append(0)
        if len(bench_df) > 0:
            bench_rets.append(bench_df['future_ret_5d'].mean() / 5)
        if i > 520: break  # 安全检查

    port_rets = np.array(port_rets, dtype=float)
    bench_rets = np.array(bench_rets, dtype=float)
    port_rets = port_rets[np.isfinite(port_rets)]
    bench_rets = bench_rets[np.isfinite(bench_rets)]

    port_cum = (1 + port_rets).cumprod()
    bench_cum = (1 + bench_rets).cumprod()
    port_total = (port_cum[-1] - 1) * 100 if len(port_cum) > 0 else 0
    bench_total = (bench_cum[-1] - 1) * 100 if len(bench_cum) > 0 else 0

    def sharpe(r):
        r = r[np.isfinite(r)]
        return (np.mean(r)/(np.std(r)+1e-10))*np.sqrt(252) if len(r)>1 and np.std(r)>1e-10 else 0.0
    def max_dd(c):
        peak = np.maximum.accumulate(c)
        return np.min((c - peak)/peak) * 100

    psh = sharpe(port_rets); bsh = sharpe(bench_rets)
    pdd = max_dd(port_cum); bdd = max_dd(bench_cum)

    log("\n" + "="*60)
    log("回测结果")
    log("="*60)
    log(f"融合框架  收益={port_total:+.2f}%  夏普={psh:.2f}  回撤={pdd:+.2f}%  持仓={np.mean(n_holds):.0f}只/天")
    log(f"等权持有  收益={bench_total:+.2f}%  夏普={bsh:.2f}  回撤={bdd:+.2f}%")
    log(f"Alpha: {port_total-bench_total:+.2f}%")
    log("\n完成!")

except Exception as e:
    log(f"\n错误: {e}")
    log(traceback.format_exc())
finally:
    LOG.close()
