# -*- coding: utf-8 -*-
"""
融合框架扫描 v2 - 缠论分型 + 徐小�?�?融合框架引擎（含真实VIX风控�?
�?scanner 管线直接对接 fusion_framework �?FusionEngine
"""
import os, sys, json
from pathlib import Path
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

import pandas as pd
import numpy as np

BASE_DIR   = Path('E:/quant')
CACHE_DIR  = BASE_DIR / 'scanner2' / 'cache'
OUTPUT_DIR = BASE_DIR / 'output'
FW_DIR     = BASE_DIR / 'fusion_framework'
SKILL_DIR  = Path('C:/Users/RoyGoode/.workbuddy\skills/xmm-strategy/scripts')

sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(FW_DIR))
sys.path.insert(0, str(SKILL_DIR))
sys.stdout.reconfigure(encoding='utf-8')

from chan.fractal import mark_fractals
from xmm_signals import XMMSignalEngine
from fusion_engine import FusionEngine as FrameworkFusionEngine
from signal_types import FusionModelSignal, XMMSignal, SignalLevel

OUTPUT_DIR.mkdir(exist_ok=True)

# ══════════════════════════════════════════════════════════════�?
# 1. Load VIX data
# ══════════════════════════════════════════════════════════════�?
vxx_path = BASE_DIR / 'scanner' / 'cache' / 'VXX_US.json'
vix_date_map = {}
if vxx_path.exists():
    with open(vxx_path) as f:
        vxx_data = json.load(f)
    for r in vxx_data:
        d = datetime.fromtimestamp(r['date']/1000).strftime('%Y-%m-%d')
        vix_date_map[d] = float(r['close'])
    print(f'VIX: {len(vix_date_map)} days loaded')

# ══════════════════════════════════════════════════════════════�?
# 2. Analysis functions (from scan pipeline)
# ══════════════════════════════════════════════════════════════�?
def load_kline(fp):
    try:
        with open(fp, 'r', encoding='utf-8') as f:
            data = json.load(f)
        df = pd.DataFrame(data) if isinstance(data, list) else None
        if df is None: return None
        # Normalize: trade_date �?date
        if 'trade_date' in df.columns and 'date' not in df.columns:
            df['date'] = df['trade_date']
        for c in ['open','high','low','close']:
            if c in df.columns: df[c] = pd.to_numeric(df[c], errors='coerce')
        return df.dropna(subset=['open','high','low','close'])
    except:
        return None

def rsi(close, n=14):
    d = np.diff(close)
    g = np.where(d > 0, d, 0.0)
    lo = np.where(d < 0, -d, 0.0)
    r = np.full(len(close), 50.0)
    if len(close) <= n: return r
    ag, al = np.mean(g[:n]), np.mean(lo[:n])
    for i in range(n + 1, len(close)):
        ag = (ag * (n - 1) + g[i - 1]) / n
        al = (al * (n - 1) + lo[i - 1]) / n
        r[i] = 50.0 if al < 1e-10 else 100 - 100 / (1 + ag / (al + 1e-10))
    return r

def weekly_rsi_scalar(close, period=4):
    """简化版周RSI: 取末点做样本"""
    n = len(close)
    if n < 30: return 50.0
    step = 5
    weekly = [close[i] for i in range(step-1, n, step)]
    if len(weekly) < period + 1: return 50.0
    w_close = np.array(weekly, dtype=float)
    try:
        wr = rsi(w_close, period)
        return float(wr[-1])
    except:
        return 50.0

def analyze_fractal_to_fm(df, code, name):
    """缠论分型 �?FusionModelSignal"""
    if df is None or len(df) < 30: return None
    df2 = mark_fractals(df.copy())
    top_cnt = int((df2['fractal'] == 1).sum())
    bot_cnt = int((df2['fractal'] == -1).sum())
    lt = df2[df2['fractal'] == 1].tail(1)
    lb = df2[df2['fractal'] == -1].tail(1)
    
    close = df['close'].values.astype(float)
    w_rsi = weekly_rsi_scalar(close, 4)
    rsi_d = rsi(close, 14)
    m_rsi = weekly_rsi_scalar(close, 20)  # approx monthly
    
    # SMA50 for trend
    sma50 = np.full(len(close), np.nan)
    for i in range(49, len(close)):
        sma50[i] = np.mean(close[i-49:i+1])
    trend_up = close[-1] > sma50[-1] if not np.isnan(sma50[-1]) else False
    
    date = str(df['date'].iloc[-1])[:10]
    
    # Direction from latest fractal
    if len(lt) and len(lb):
        ftype = 'TOP' if lt.index[-1] > lb.index[-1] else 'BOTTOM'
    elif len(lt):
        ftype = 'TOP'
    elif len(lb):
        ftype = 'BOTTOM'
    else:
        return None
    
    # Score: bottom=positive, top=negative, weighted by count
    if ftype == 'BOTTOM':
        score = bot_cnt * 2.0
        sig = 'BUY'
        conf = min(0.88, 0.5 + bot_cnt * 0.05)
    else:
        score = -top_cnt * 2.0
        sig = 'SELL'
        conf = min(0.88, 0.5 + top_cnt * 0.05)
    
    return FusionModelSignal(
        f'{code}.HK', date, float(score),
        float(w_rsi), float(m_rsi),
        0.0, sig, float(conf),
        {'trend_up': trend_up, 'sma50_above': trend_up,
         'fractal_type': ftype, 'name': name})

def analyze_xmm_to_ta(df, code, name):
    """��С���ź� �� XMMSignal"""
    if df is None or len(df) < 35: return None
    engine = XMMSignalEngine()
    sig = engine.analyze(df)
    close_arr = df['close'].values.astype(float)
    rsi_d = rsi(close_arr, 14)
    
    date = str(df['date'].iloc[-1])[:10]
    action = sig['signal']
    
    # Confidence from strength
    strength_map = {3: 0.85, 2: 0.72, 1: 0.60, 0: 0.50}
    conf = strength_map.get(sig['strength'], 0.50)
    
    # SMA position
    sma20 = np.full(len(close_arr), np.nan)
    for i in range(19, len(close_arr)):
        sma20[i] = np.mean(close_arr[i-19:i+1])
    sma_pos = float(close_arr[-1] / sma20[-1]) if not np.isnan(sma20[-1]) else 1.0
    
    return XMMSignal(
        f'{code}.HK', date, action, conf,
        float(rsi_d[-1]), float(sma_pos),
        float(sig.get('td9_count', 0)),
        str(sig.get('reason','')),
        '',
        str(sig.get('macd_desc','')))

# ══════════════════════════════════════════════════════════════�?
# 3. Run scan
# ══════════════════════════════════════════════════════════════�?
print('=' * 70)
print(f'  融合框架 HK 扫描  {datetime.now().strftime("%Y-%m-%d %H:%M")}')
print('=' * 70)

fw_engine = FrameworkFusionEngine()
if vix_date_map:
    fw_engine.set_vix_data(vix_date_map)

files = sorted(CACHE_DIR.glob('*_HK_1d.json'))
results = []

for fp in files:
    code = fp.name.replace('_HK_1d.json', '')
    df = load_kline(fp)
    if df is None or len(df) < 35:
        continue
    
    name = ''
    # Extract name from first record if available
    try:
        with open(fp, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        if isinstance(raw, list) and len(raw) > 0:
            name = raw[0].get('name', '')
    except:
        pass
    
    fm_sig = analyze_fractal_to_fm(df, code, name)
    xmm_sig = analyze_xmm_to_ta(df, code, name)
    
    if fm_sig is None and xmm_sig is None:
        continue
    
    fusion = fw_engine.fuse(fm_sig, xmm_sig, 'HK')
    results.append({
        'code': code,
        'name': name or '',
        'level': fusion.level.value,
        'score': fusion.score,
        'confidence': fusion.confidence,
        'position': fusion.position_pct,
        'risk': fusion.risk_level,
        'warnings': '; '.join(fusion.warnings) if fusion.warnings else '',
        'reasoning': fusion.reasoning,
        'close': float(df['close'].iloc[-1]),
        'date': str(df['date'].iloc[-1])[:10],
        'fm_signal': fm_sig.raw_signal if fm_sig else 'N/A',
        'fm_score': fm_sig.score if fm_sig else 0,
        'xmm_signal': xmm_sig.action if xmm_sig else 'N/A',
        'xmm_conf': xmm_sig.confidence if xmm_sig else 0,
        'rsi_d': float(rsi(df['close'].values, 14)[-1]),
    })

results.sort(key=lambda x: x['score'], reverse=True)

# ══════════════════════════════════════════════════════════════�?
# 4. Output
# ══════════════════════════════════════════════════════════════�?
df_r = pd.DataFrame(results)
ts = datetime.now().strftime('%Y%m%d_%H%M%S')
df_r.to_csv(OUTPUT_DIR / f'fusion_framework_HK_{ts}.csv', index=False, encoding='utf-8-sig')

# Distribution
buy_sig = sum(1 for r in results if r['level'] in ('STRONG_BUY','BUY'))
sell_sig = sum(1 for r in results if r['level'] in ('STRONG_SELL','SELL'))
reduced = sum(1 for r in results if r['level'] == 'REDUCED')
hold = sum(1 for r in results if r['level'] == 'HOLD')

print(f'  Total: {len(results)} stocks analyzed')
print(f'  BUY: {buy_sig}  SELL: {sell_sig}  REDUCED: {reduced}  HOLD: {hold}')
print()

# Top BUY signals
strong_buy = [r for r in results if r['level'] == 'STRONG_BUY']
buy = [r for r in results if r['level'] == 'BUY']
sell_list = [r for r in results if r['level'] in ('STRONG_SELL','SELL')]

if strong_buy:
    print(f'  🔥 STRONG_BUY ({len(strong_buy)}):')
    for r in strong_buy[:5]:
        print(f'    {r["code"]:<8} {r["name"][:12]:<12} '
              f'conf={r["confidence"]:.1%} score={r["score"]:+.1f} '
              f'pos={r["position"]:.0%} rsi={r["rsi_d"]:.1f}')
        print(f'      fm={r["fm_signal"]} xmm={r["xmm_signal"]} | {r["reasoning"][:80]}')

if buy:
    print(f'\n  🟢 BUY ({len(buy)}):')
    for r in buy[:5]:
        print(f'    {r["code"]:<8} {r["name"][:12]:<12} '
              f'conf={r["confidence"]:.1%} score={r["score"]:+.1f} '
              f'pos={r["position"]:.0%} rsi={r["rsi_d"]:.1f}')
        print(f'      fm={r["fm_signal"]} xmm={r["xmm_signal"]} | {r["reasoning"][:80]}')

if sell_list:
    print(f'\n  🔴 SELL ({len(sell_list)}):')
    for r in sell_list[:5]:
        print(f'    {r["code"]:<8} {r["name"][:12]:<12} '
              f'conf={r["confidence"]:.1%} score={r["score"]:+.1f} '
              f'rsi={r["rsi_d"]:.1f} risk={r["risk"]}')
        print(f'      fm={r["fm_signal"]} xmm={r["xmm_signal"]} | {r["reasoning"][:80]}')

if reduced:
    print(f'\n  ⚠️ REDUCED ({reduced}):')
    for r in [x for x in results if x['level']=='REDUCED'][:5]:
        print(f'    {r["code"]:<8} {r["name"][:12]:<12} '
              f'fm={r["fm_signal"]} xmm={r["xmm_signal"]} | {r["warnings"]}')

print(f'\n  [SAVED] E:/quant/output/fusion_framework_HK_{ts}.csv')
print('=' * 70)
