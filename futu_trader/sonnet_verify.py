# -*- coding: utf-8 -*-
"""
Sonnet Verification Layer
XGBoost signal ?Sonnet cross-check ?Final signal with confidence

Logic:
- Both BUY ?STRONG_BUY (high confidence)
- XGBoost BUY + Sonnet HOLD ?BUY (medium confidence, Sonnet neutral is not opposition)
- XGBoost BUY + Sonnet SELL/EXIT ?HOLD (conflict, safety first)
- XGBoost SELL + Sonnet SELL ?STRONG_SELL (high confidence)
- XGBoost SELL + Sonnet HOLD ?SELL (proceed with caution)

Author: AiGobot
Date: 2026-04-24
"""
import sys, io, os, json, warnings
from datetime import datetime
import pandas as pd
import numpy as np

_IS_MAIN = __name__ == '__main__'
if _IS_MAIN:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
warnings.filterwarnings('ignore')

sys.path.insert(0, r'E:\quant\xmm-strategy')
from xmm_sonnet_model import XMMSonnetModel

# TickFlow fallback
sys.path.insert(0, r'C:\Users\RoyGoode\.workbuddy\skills\tickflow\python')

BASE_DIR = r'E:\quant\futu_trader'
OUTPUT_DIR = os.path.join(BASE_DIR, 'output')


def _classify_signal(signal):
    """Classify signal into bullish/bearish/neutral"""
    if signal in ('STRONG_BUY', 'BUY'):
        return 'bullish'
    elif signal in ('STRONG_SELL', 'SELL', 'EXIT'):
        return 'bearish'
    elif signal == 'REDUCE':
        return 'bearish'
    else:
        return 'neutral'


def _fuse_signals(xgb_signal, xgb_pred, sonnet_signal, sonnet_limit):
    """
    Fuse XGBoost prediction with Sonnet verification
    Returns: (final_signal, confidence, reason)
    """
    xgb_dir = _classify_signal(xgb_signal)
    son_dir = _classify_signal(sonnet_signal)

    # Both bullish ?high confidence
    if xgb_dir == 'bullish' and son_dir == 'bullish':
        confidence = 'HIGH'
        if xgb_signal == 'STRONG_BUY' and sonnet_signal == 'STRONG_BUY':
            final = 'STRONG_BUY'
        else:
            final = 'BUY'
        reason = '双系统共振买?

    # XGBoost bullish + Sonnet neutral ?medium confidence
    elif xgb_dir == 'bullish' and son_dir == 'neutral':
        confidence = 'MEDIUM'
        final = 'BUY'
        reason = 'XGB看多+Sonnet中?

    # XGBoost bullish + Sonnet bearish ?conflict, hold
    elif xgb_dir == 'bullish' and son_dir == 'bearish':
        confidence = 'LOW'
        final = 'HOLD'
        reason = 'XGB看多但Sonnet看空→冲突观?

    # Both bearish ?high confidence sell
    elif xgb_dir == 'bearish' and son_dir == 'bearish':
        confidence = 'HIGH'
        if xgb_signal == 'STRONG_SELL' and sonnet_signal in ('EXIT',):
            final = 'STRONG_SELL'
        else:
            final = 'SELL'
        reason = '双系统共振卖?

    # XGBoost bearish + Sonnet neutral ?medium sell
    elif xgb_dir == 'bearish' and son_dir == 'neutral':
        confidence = 'MEDIUM'
        final = 'SELL'
        reason = 'XGB看空+Sonnet中?

    # XGBoost bearish + Sonnet bullish ?conflict, hold
    elif xgb_dir == 'bearish' and son_dir == 'bullish':
        confidence = 'LOW'
        final = 'HOLD'
        reason = 'XGB看空但Sonnet看多→冲突观?

    # Both neutral
    else:
        confidence = 'NEUTRAL'
        final = 'HOLD'
        reason = '双系统均中?

    # Override: Sonnet EXIT always triggers exit (risk first)
    if sonnet_signal == 'EXIT':
        final = 'STRONG_SELL'
        confidence = 'HIGH'
        reason = 'Sonnet强制EXIT→清?

    return final, confidence, reason


def fetch_kline_for_sonnet(code, days=2500):
    """
    Fetch daily K-line data for Sonnet analysis
    Sonnet needs ~2500 days (10yr) for monthly EMA(90) warmup
    Priority: Futu (max 10000) ?TickFlow (max 10000)
    """
    # Try Futu first (long history)
    try:
        from futu import OpenQuoteContext, KLType, AuType, RET_OK
        from datetime import timedelta
        conn = OpenQuoteContext(host='127.0.0.1', port=11111)
        ret, data, _ = conn.request_history_kline(
            code=code,
            start=(datetime.now() - timedelta(days=7300)).strftime('%Y-%m-%d'),
            end=datetime.now().strftime('%Y-%m-%d'),
            ktype=KLType.K_DAY,
            autype=AuType.QFQ,
            max_count=10000
        )
        conn.close()
        if ret == RET_OK and len(data) >= 120:
            df = data.rename(columns={'time_key': 'datetime'})
            df['datetime'] = pd.to_datetime(df['datetime'])
            df = df.set_index('datetime').sort_index()
            for c in ['open', 'high', 'low', 'close', 'volume']:
                df[c] = pd.to_numeric(df[c], errors='coerce')
            df = df.dropna(subset=['close'])
            return df, 'Futu'
    except Exception:
        pass

    # TickFlow fallback (long history)
    tf_code = code.replace('HK.', '') + '.HK'
    try:
        import tickflow_env  # noqa: auto-inject TICKFLOW_API_KEY
from tickflow import TickFlow
        tf = TickFlow() if os.environ.get('TICKFLOW_API_KEY') else TickFlow.free()  #  API keyʹѲ
        df = tf.klines.get(tf_code, period='1d', count=10000, as_dataframe=True)
        if df is not None and len(df) >= 120:
            if 'trade_date' in df.columns:
                df['trade_date'] = pd.to_datetime(df['trade_date'])
                df.set_index('trade_date', inplace=True)
            elif 'timestamp' in df.columns:
                df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
                df.set_index('datetime', inplace=True)
            df.sort_index(inplace=True)
            for c in ['open', 'high', 'low', 'close', 'volume']:
                if c in df.columns:
                    df[c] = pd.to_numeric(df[c], errors='coerce')
            df = df.dropna(subset=['close'])
            return df, 'TickFlow'
    except Exception:
        pass

    return None, 'FAILED'


# ══?Sonnet参数自适应 ══?
# 标准模式: EMA(25, 90) 需 ~2500日线 ?月线~114??EMA90预热?
# 短周期模? EMA(12, 50) 需 ~1200日线 ?月线~55??EMA50预热?
# 极短模式: EMA(6, 25) 需 ~700日线 ?月线~32??EMA25预热?
SONNET_PROFILES = {
    'standard': {'sp': 25, 'lp': 90, 'min_days': 2500, 'min_monthly': 100, 'label': '标准EMA(25,90)'},
    'short':    {'sp': 12, 'lp': 50, 'min_days': 1600, 'min_monthly': 60,  'label': '短周期EMA(12,50)'},
    'ultrashort': {'sp': 6, 'lp': 25, 'min_days': 1100, 'min_monthly': 35,  'label': '极短EMA(6,25)'},
}


def _choose_sonnet_profile(n_days, n_monthly_est):
    """Auto-select Sonnet parameter profile based on data length
    n_days: daily bar count
    n_monthly_est: estimated monthly bar count (n_days // 21)
    """
    # Check monthly bars first (most restrictive)
    for key in ('standard', 'short', 'ultrashort'):
        profile = SONNET_PROFILES[key]
        if n_days >= profile['min_days'] and n_monthly_est >= profile['min_monthly']:
            return key, profile
    # Fallback: try by monthly bars alone
    for key in ('standard', 'short', 'ultrashort'):
        profile = SONNET_PROFILES[key]
        if n_monthly_est >= profile['min_monthly']:
            return key, profile
    return None, None


def run_sonnet_verification(signal_results):
    """
    Run Sonnet verification on XGBoost signal results
    Auto-selects parameter profile based on available data length
    Returns: list of fused signal dicts
    """
    fused_results = []

    for sig in signal_results:
        code = sig.get('code', '')
        xgb_signal = sig.get('signal', 'HOLD')
        xgb_pred = sig.get('pred', 0)

        print(f'\n  [Sonnet] 分析 {code}...', end=' ', flush=True)

        # Fetch data
        df, source = fetch_kline_for_sonnet(code)

        if df is None or len(df) < 400:
            print(f'数据不足({source}, {len(df) if df is not None else 0}?')
            fused_results.append({
                **sig,
                'sonnet_signal': 'DATA_INSUFFICIENT',
                'sonnet_limit': 0,
                'fused_signal': xgb_signal,  # fallback to XGBoost only
                'confidence': 'LOW',
                'fusion_reason': 'Sonnet数据不足，仅用XGB',
                'data_source': source,
            })
            continue

        # Auto-select Sonnet profile
        n_monthly_est = len(df) // 21  # estimate monthly bars
        profile_key, profile = _choose_sonnet_profile(len(df), n_monthly_est)
        if profile is None:
            print(f'数据不足(需?00? 仅{len(df)}?')
            fused_results.append({
                **sig,
                'sonnet_signal': 'DATA_INSUFFICIENT',
                'sonnet_limit': 0,
                'fused_signal': xgb_signal,
                'confidence': 'LOW',
                'fusion_reason': f'Sonnet数据不足({len(df)}?600), 仅用XGB',
                'data_source': source,
            })
            continue

        print(f'{source}({len(df)}? [{profile["label"]}]', end=' ', flush=True)

        # Create model with appropriate parameters
        model = XMMSonnetModel(sp=profile['sp'], lp=profile['lp'])

        # Run Sonnet analysis
        try:
            sonnet_result = model.analyze(df)
            sonnet_signal = sonnet_result.get('signal', 'HOLD')
            sonnet_limit = sonnet_result.get('position_limit', 0)
            monthly_trend = sonnet_result.get('monthly_trend', '?')
            weekly_trend = sonnet_result.get('weekly_trend', '?')
            layer2 = sonnet_result.get('layer2', '-')

            # Fuse signals
            final_signal, confidence, reason = _fuse_signals(
                xgb_signal, xgb_pred, sonnet_signal, sonnet_limit
            )

            # Downgrade confidence for non-standard profiles
            if profile_key != 'standard':
                if confidence == 'HIGH':
                    confidence = 'MEDIUM'
                    reason += '(短周期降?'
                elif confidence == 'MEDIUM':
                    confidence = 'LOW-MED'
                    reason += '(短周期降?'

            print(f'XGB={xgb_signal} + Sonnet={sonnet_signal} ?{final_signal}({confidence})')

            fused_results.append({
                **sig,
                'sonnet_signal': sonnet_signal,
                'sonnet_limit': round(sonnet_limit, 3),
                'sonnet_monthly': monthly_trend,
                'sonnet_weekly': weekly_trend,
                'sonnet_layer2': layer2,
                'sonnet_profile': profile_key,
                'fused_signal': final_signal,
                'confidence': confidence,
                'fusion_reason': reason,
                'data_source': source,
            })

        except ValueError as e:
            # Sonnet needs more data (e.g., monthly EMA warmup)
            print(f'Sonnet异常: {e}')
            fused_results.append({
                **sig,
                'sonnet_signal': 'ERROR',
                'sonnet_limit': 0,
                'fused_signal': xgb_signal,
                'confidence': 'LOW',
                'fusion_reason': f'Sonnet计算异常: {str(e)[:40]}',
                'data_source': source,
            })

    return fused_results


if __name__ == '__main__':
    # Test with latest signal file
    signal_files = sorted([f for f in os.listdir(OUTPUT_DIR)
                           if f.startswith('signal_') and f.endswith('.json')])
    if not signal_files:
        print('No signal files found in {}'.format(OUTPUT_DIR))
        sys.exit(1)

    latest = os.path.join(OUTPUT_DIR, signal_files[-1])
    print('Loading: {}'.format(latest))
    with open(latest, 'r', encoding='utf-8') as f:
        data = json.load(f)

    results = data.get('results', [])
    print('Signals: {} stocks'.format(len(results)))

    fused = run_sonnet_verification(results)

    # Summary
    print('\n' + '=' * 60)
    print('FUSION SUMMARY')
    print('=' * 60)
    for r in fused:
        mark = '? if r['confidence'] == 'HIGH' else ('~' if r['confidence'] == 'MEDIUM' else '?)
        print(f'  {mark} {r["code"]:>12s}  XGB={r.get("signal","?"):>12s}  Sonnet={r.get("sonnet_signal","?"):>12s}  ?{r["fused_signal"]:>12s}  [{r["confidence"]}] {r["fusion_reason"]}')

    # Save
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    save_path = os.path.join(OUTPUT_DIR, 'fused_signal_{}.json'.format(ts))
    with open(save_path, 'w', encoding='utf-8') as f:
        json.dump({'timestamp': datetime.now().isoformat(), 'results': fused}, f, indent=2, ensure_ascii=False)
    print(f'\nSaved: {save_path}')
