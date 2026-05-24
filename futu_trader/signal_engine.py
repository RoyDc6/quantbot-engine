# -*- coding: utf-8 -*-
"""
Futu Simulated Trading Signal Engine v1.1
Pull K-lines from Futu (fallback to TickFlow) -> Compute 36 factors -> XGBoost inference -> Output signals

Author: AiGobot
Date: 2026-04-24
"""
import sys, io, os, json, warnings
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
import xgboost as xgb
import pickle

# Only redirect stdout when run directly, not when imported as module
_IS_MAIN = __name__ == '__main__'
if _IS_MAIN:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
warnings.filterwarnings('ignore')

# === Config ===
FUTU_HOST = '127.0.0.1'
FUTU_PORT = 11111
MODEL_PATH = r'E:\quant\ml_alpha\ultimate_xgb_model.pkl'
OUTPUT_DIR = r'E:\quant\futu_trader\output'

WATCHLIST = [
    'HK.00700', 'HK.09988', 'HK.01810', 'HK.03690',
    'HK.09618', 'HK.02318', 'HK.00005', 'HK.00941',
    'HK.09999', 'HK.09688', 'HK.02318', 'HK.00941',
]

# TickFlow codes for stocks where Futu may lack data quota
TICKFLOW_MAP = {
    'HK.09999': '09999.HK',  # 网易
    'HK.09688': '09688.HK',  # 京东
    'HK.02318': '02318.HK',  # 平安
    'HK.00941': '00941.HK',  # 中国移动
}
# All HK stocks also have TickFlow codes as fallback
for _c in WATCHLIST:
    if _c not in TICKFLOW_MAP:
        TICKFLOW_MAP[_c] = _c.replace('HK.', '') + '.HK'

BUY_THRESHOLD = 0.005
SELL_THRESHOLD = -0.005
STRONG_BUY = 0.015
STRONG_SELL = -0.015

os.makedirs(OUTPUT_DIR, exist_ok=True)


# === Factor Calculations ===
def calc_rsi(close, n=14):
    d = np.diff(close)
    g = np.where(d > 0, d, 0.0)
    l_ = np.where(d < 0, -d, 0.0)
    r = np.full(len(close), 50.0)
    if len(close) <= n:
        return r
    ag, al = np.mean(g[:n]), np.mean(l_[:n])
    r[n] = 50.0 if al < 1e-10 else 100 - 100 / (1 + ag / (al + 1e-10))
    for i in range(n + 1, len(close)):
        ag = (ag * (n - 1) + g[i - 1]) / n
        al = (al * (n - 1) + l_[i - 1]) / n
        r[i] = 50.0 if al < 1e-10 else 100 - 100 / (1 + ag / (al + 1e-10))
    return r


def calc_macd(close, fast=12, slow=26, signal=9):
    ema_f = pd.Series(close).ewm(span=fast, adjust=False).mean().values
    ema_s = pd.Series(close).ewm(span=slow, adjust=False).mean().values
    dif = ema_f - ema_s
    dea = pd.Series(dif).ewm(span=signal, adjust=False).mean().values
    hist = 2 * (dif - dea)
    macd_bull = 1 if dif[-1] > dea[-1] else 0
    return dif[-1], dea[-1], hist[-1], macd_bull


def calc_atr(high, low, close, n=14):
    tr = np.maximum(high[1:] - low[1:],
                    np.maximum(np.abs(high[1:] - close[:-1]),
                               np.abs(low[1:] - close[:-1])))
    atr = pd.Series(tr).rolling(n).mean().values
    if len(atr) > 0 and not np.isnan(atr[-1]) and atr[-1] > 0:
        return atr[-1] / close[-1]
    return 0.0


def calc_bollinger(close, n=20):
    if len(close) < n:
        return 0.5
    ma = np.mean(close[-n:])
    std = np.std(close[-n:])
    if std < 1e-10:
        return 0.5
    return (close[-1] - ma) / (2 * std)


def calc_fractals(high, low, close, window=5):
    n = len(close)
    top_5 = bottom_5 = top_10 = bottom_10 = 0
    top_count_10 = bottom_count_10 = top_count_20 = bottom_count_20 = 0
    for i in range(max(window, n - 20), n - 1):
        is_top = (i > 0 and high[i] > high[i-1] and high[i] > high[i+1])
        is_bottom = (i > 0 and low[i] < low[i-1] and low[i] < low[i+1])
        if is_top:
            top_count_20 += 1
            if i >= n - 10:
                top_count_10 += 1
            if i >= n - 5:
                top_5 = 1
            if i >= n - 10:
                top_10 = 1
        if is_bottom:
            bottom_count_20 += 1
            if i >= n - 10:
                bottom_count_10 += 1
            if i >= n - 5:
                bottom_5 = 1
            if i >= n - 10:
                bottom_10 = 1
    return {
        'fractal_top_5': top_5, 'fractal_bottom_5': bottom_5,
        'fractal_top_10': top_10, 'fractal_bottom_10': bottom_10,
        'top_count_10': top_count_10, 'bottom_count_10': bottom_count_10,
        'frac_balance_10': bottom_count_10 - top_count_10,
        'top_count_20': top_count_20, 'bottom_count_20': bottom_count_20,
        'frac_balance_20': bottom_count_20 - top_count_20,
    }


def calc_td9(close):
    n = len(close)
    sell_count = buy_count = 0
    for i in range(max(4, n - 20), n):
        if close[i] > close[i - 4]:
            sell_count += 1
            buy_count = 0
        else:
            buy_count += 1
            sell_count = 0
    td9_count = sell_count - buy_count
    td9_abs = abs(td9_count)
    return {
        'td9_count': td9_count, 'td9_abs': td9_abs,
        'td9_buy_zone': 1 if td9_count <= -7 else 0,
        'td9_sell_zone': 1 if td9_count >= 7 else 0,
        'td9_extreme': 1 if td9_abs >= 9 else 0,
    }


def calc_bull_divergence(close, low):
    n = len(close)
    if n < 30:
        return 0
    rsi = calc_rsi(close, 14)
    recent_low = np.min(low[-20:])
    prev_low = np.min(low[-40:-20]) if n >= 40 else recent_low
    if recent_low < prev_low and rsi[-1] > rsi[-20]:
        return 1
    return 0


def compute_all_features(close, high, low, volume):
    n = len(close)
    if n < 60:
        return None
    f = {}
    f['ret_1d'] = (close[-1] / close[-2] - 1) if n >= 2 else 0
    f['ret_3d'] = (close[-1] / close[-4] - 1) if n >= 4 else 0
    f['ret_5d'] = (close[-1] / close[-6] - 1) if n >= 6 else 0
    f['ret_10d'] = (close[-1] / close[-11] - 1) if n >= 11 else 0
    f['ret_20d'] = (close[-1] / close[-21] - 1) if n >= 21 else 0
    f['ma5_ratio'] = close[-1] / np.mean(close[-5:]) - 1 if n >= 5 else 0
    f['ma10_ratio'] = close[-1] / np.mean(close[-10:]) - 1 if n >= 10 else 0
    f['ma20_ratio'] = close[-1] / np.mean(close[-20:]) - 1 if n >= 20 else 0
    f['ma60_ratio'] = close[-1] / np.mean(close[-60:]) - 1 if n >= 60 else 0
    rsi_arr = calc_rsi(close, 14)
    f['rsi_14'] = rsi_arr[-1]
    macd_val, sig_val, hist_val, macd_bull = calc_macd(close)
    f['macd'] = macd_val
    f['macd_signal'] = sig_val
    f['macd_hist'] = hist_val
    f['macd_bull'] = macd_bull
    f['atr_ratio'] = calc_atr(high, low, close)
    f['bb_ratio'] = calc_bollinger(close)
    if n >= 20 and np.mean(volume[-20:]) > 0:
        f['vol_ratio'] = volume[-1] / np.mean(volume[-20:])
    else:
        f['vol_ratio'] = 1.0
    f['mom_5d'] = close[-1] / close[-6] - 1 if n >= 6 else 0
    f['mom_10d'] = close[-1] / close[-11] - 1 if n >= 11 else 0
    f['mom_20d'] = close[-1] / close[-21] - 1 if n >= 21 else 0
    f.update(calc_fractals(high, low, close))
    f.update(calc_td9(close))
    f['bull_divergence'] = calc_bull_divergence(close, low)
    return f


# === TickFlow Fallback ===
def fetch_kline_tickflow(tf_code, days=120):
    """Fetch K-line data from TickFlow API as fallback when Futu fails."""
    try:
        sys.path.insert(0, r'C:\Users\RoyGoode\.workbuddy\skills\tickflow\python')
        import tickflow_env  # noqa: auto-inject TICKFLOW_API_KEY
from tickflow import TickFlow
        tf = TickFlow() if os.environ.get('TICKFLOW_API_KEY') else TickFlow.free()  # 有 API key 用付费版
        df = tf.klines.get(tf_code, period='1d', count=days, as_dataframe=True)
        if df is None or len(df) < 60:
            return None
        # Normalize columns to match Futu format
        result = pd.DataFrame()
        result['close'] = df['close'].values.astype(float)
        result['high'] = df['high'].values.astype(float)
        result['low'] = df['low'].values.astype(float)
        result['volume'] = df['volume'].values.astype(float)
        return result
    except Exception as e:
        print('TickFlow fallback failed for {}: {}'.format(tf_code, e))
        return None


# === Signal Generation ===
def load_model(model_path):
    with open(model_path, 'rb') as fh:
        m = pickle.load(fh)
    if isinstance(m, dict):
        return m['model'], m['feature_cols']
    if hasattr(m, 'feature_names_in_'):
        return m.get_booster(), list(m.feature_names_in_)
    return m, None


def generate_signal(features, booster, feature_cols):
    x = np.array([features.get(col, 0.0) for col in feature_cols]).reshape(1, -1)
    dm = xgb.DMatrix(x, feature_names=feature_cols)
    pred = booster.predict(dm)[0]
    if pred >= STRONG_BUY:
        signal = 'STRONG_BUY'
    elif pred >= BUY_THRESHOLD:
        signal = 'BUY'
    elif pred <= STRONG_SELL:
        signal = 'STRONG_SELL'
    elif pred <= SELL_THRESHOLD:
        signal = 'SELL'
    else:
        signal = 'HOLD'
    return signal, pred


def run_scan():
    from futu import OpenQuoteContext, KLType, AuType, RET_OK

    booster, feature_cols = load_model(MODEL_PATH)
    print('Model: {}'.format(MODEL_PATH))
    print('Features: {}'.format(len(feature_cols)))
    print()

    quote_ctx = OpenQuoteContext(host=FUTU_HOST, port=FUTU_PORT)
    results = []

    for code in WATCHLIST:
        ret, data, _page_req = quote_ctx.request_history_kline(
            code,
            start=(datetime.now() - timedelta(days=180)).strftime('%Y-%m-%d'),
            end=datetime.now().strftime('%Y-%m-%d'),
            ktype=KLType.K_DAY,
            autype=AuType.QFQ,
            max_count=120
        )

        use_tickflow = False
        if ret != RET_OK or len(data) < 60:
            # Try TickFlow fallback
            tf_code = TICKFLOW_MAP.get(code)
            if tf_code:
                print('{}: Futu failed, trying TickFlow ({})...'.format(code, tf_code))
                tf_data = fetch_kline_tickflow(tf_code)
                if tf_data is not None and len(tf_data) >= 60:
                    data = tf_data
                    use_tickflow = True
                    print('{}: TickFlow OK ({} bars)'.format(code, len(data)))
                else:
                    print('{}: BOTH FAIL'.format(code))
                    results.append({'code': code, 'signal': 'ERROR', 'pred': 0, 'price': 0})
                    continue
            else:
                print('{}: FAIL - {}'.format(code, data))
                results.append({'code': code, 'signal': 'ERROR', 'pred': 0, 'price': 0})
                continue

        close = data['close'].values.astype(float)
        high = data['high'].values.astype(float)
        low = data['low'].values.astype(float)
        volume = data['volume'].values.astype(float)

        features = compute_all_features(close, high, low, volume)
        if features is None:
            print('{}: FACTOR_ERROR'.format(code))
            continue

        signal, pred = generate_signal(features, booster, feature_cols)
        name = str(data.iloc[-1].get('stock_name', code)) if not use_tickflow else tf_code

        results.append({
            'code': code, 'name': name, 'signal': signal,
            'pred': float(pred),
            'price': float(close[-1]), 'rsi_14': float(features['rsi_14']),
            'macd_bull': int(features['macd_bull']),
            'frac_balance_20': int(features['frac_balance_20']),
            'td9_count': int(features['td9_count']),
            'bull_divergence': int(features['bull_divergence']),
        })

        macd_str = 'B' if features['macd_bull'] else 'S'
        print('{} {:>12s}  price={:>8.2f}  signal={:>12s}  pred={:+.4f}  RSI={:.1f}  MACD={}  FracBal={:+d}  TD9={:+d}'.format(
            code, name, close[-1], signal, pred,
            features['rsi_14'], macd_str,
            features['frac_balance_20'], features['td9_count']))

    quote_ctx.close()

    # Save results
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    df = pd.DataFrame(results)
    csv_path = os.path.join(OUTPUT_DIR, 'signal_{}.csv'.format(ts))
    df.to_csv(csv_path, index=False, encoding='utf-8-sig')

    summary = {
        'timestamp': datetime.now().isoformat(),
        'model': 'ultimate_xgb_model',
        'results': results,
        'buy_signals': [r for r in results if 'BUY' in r.get('signal', '')],
        'sell_signals': [r for r in results if 'SELL' in r.get('signal', '')],
    }
    json_path = os.path.join(OUTPUT_DIR, 'signal_{}.json'.format(ts))
    with open(json_path, 'w', encoding='utf-8') as fh:
        json.dump(summary, fh, indent=2, ensure_ascii=False)

    print('\nSaved: {}'.format(csv_path))
    print('Saved: {}'.format(json_path))
    return results


if __name__ == '__main__':
    print('=' * 70)
    print('Futu Signal Engine v1.0')
    print('Time: {}'.format(datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    print('=' * 70)

    results = run_scan()

    buys = [r for r in results if 'BUY' in r.get('signal', '')]
    sells = [r for r in results if 'SELL' in r.get('signal', '')]
    holds = [r for r in results if r.get('signal') == 'HOLD']

    print('\n' + '=' * 70)
    print('SIGNAL SUMMARY')
    print('=' * 70)
    if buys:
        print('\nBUY ({}):'.format(len(buys)))
        for r in buys:
            print('  {} -> {} pred={:+.4f}'.format(r['code'], r['signal'], r['pred']))
    if sells:
        print('\nSELL ({}):'.format(len(sells)))
        for r in sells:
            print('  {} -> {} pred={:+.4f}'.format(r['code'], r['signal'], r['pred']))
    if holds:
        print('\nHOLD ({}):'.format(len(holds)))
        for r in holds:
            print('  {} -> HOLD'.format(r['code']))
    print('=' * 70)
