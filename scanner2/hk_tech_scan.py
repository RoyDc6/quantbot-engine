# -*- coding: utf-8 -*-
"""
港股技术指标扫描器 (HK Tech Scan)
- 基于 OKX market_scanner.py 的 EMA/RSI/MACD/ATR 加权评分体系
- 数据源: Futu OpenD (主) + TickFlow (备)
- 核心标的: config.UNIVERSE_HK

信号评分范围: -80 ~ +80
  EMA 趋势  30%  (±15 × 2)
  RSI        25%  (±20)
  MACD       25%  (±20)
  动量        10%  (±5)
  成交量      10%  (±5+5)

信号分级:
  STRONG_BUY  >= 30
  BUY         >= 15
  NEUTRAL
  SELL        <= -15
  STRONG_SELL <= -30
"""
import os, sys, json, time, warnings
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta

warnings.filterwarnings('ignore')

BASE_DIR = Path('E:/quant')
CACHE_DIR = BASE_DIR / 'scanner2' / 'cache'
OUTPUT_DIR = BASE_DIR / 'output'
CACHE_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

sys.path.insert(0, str(BASE_DIR))
sys.stdout.reconfigure(encoding='utf-8')

import config

# === 数据获取 (Futu 优先, TickFlow 备选) ============================
try:
    from futu import OpenQuoteContext
    FUTU_AVAILABLE = True
except ImportError:
    FUTU_AVAILABLE = False

try:
    import tickflow_env
    from tickflow import TickFlow
    TF_API_KEY = os.environ.get('TICKFLOW_API_KEY', 'REDACTED_TICKFLOW_KEY_FILE')
    tf = TickFlow(api_key=TF_API_KEY)
    TICKFLOW_AVAILABLE = True
except ImportError:
    TICKFLOW_AVAILABLE = False


def download_klines_futu(futu_code, cache_file):
    """从 Futu OpenD 获取日K线 (约250根)"""
    if not FUTU_AVAILABLE:
        return None
    ctx = OpenQuoteContext(host=config.FUTU_HOST, port=config.FUTU_PORT)
    try:
        end_dt = datetime.now().strftime('%Y-%m-%d')
        start_dt = (datetime.now() - timedelta(days=370)).strftime('%Y-%m-%d')
        ret, data, _ = ctx.request_history_kline(
            futu_code, start=start_dt, end=end_dt,
            ktype='K_DAY', autype=None
        )
        if ret != 0 or not isinstance(data, pd.DataFrame) or len(data) < 50:
            return None
        data = data.sort_values('time_key')
        records = [{
            'date': pd.to_datetime(row['time_key']).strftime('%Y-%m-%d'),
            'open': float(row['open']), 'high': float(row['high']),
            'low': float(row['low']), 'close': float(row['close']),
            'volume': float(row.get('volume', 0)),
        } for _, row in data.iterrows()]
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(records, f)
        return records
    except Exception:
        return None
    finally:
        ctx.close()


def download_klines_tickflow(symbol, cache_file):
    """从 TickFlow 获取日K线 (备选)"""
    if not TICKFLOW_AVAILABLE:
        return None
    try:
        data = tf.klines.get(symbol, period='1d', count=252)
        close = data.get('close', [])
        if not close or len(close) < 50:
            return None
        ts = data.get('timestamp', [])
        vol = data.get('volume', [])
        records = [{
            'date': pd.to_datetime(ts[j], unit='ms').strftime('%Y-%m-%d'),
            'open': float(data['open'][j]), 'high': float(data['high'][j]),
            'low': float(data['low'][j]), 'close': float(close[j]),
            'volume': float(vol[j]) if j < len(vol) else 0.0,
        } for j in range(len(close))]
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(records, f)
        return records
    except Exception:
        return None


def load_kline(symbol_code):
    """加载K线 (缓存优先, 否则下载)"""
    clean_code = symbol_code.replace('.HK', '')
    cache_file = CACHE_DIR / f'{clean_code}_HK_1d.json'

    # 缓存 < 4h 直接使用
    if cache_file.exists():
        age_hours = (datetime.now().timestamp() - cache_file.stat().st_mtime) / 3600
        if age_hours < 4:
            with open(cache_file, 'r', encoding='utf-8') as f:
                records = json.load(f)
            if records and len(records) >= 50:
                return records

    # Futu 优先
    futu_code = f'HK.{clean_code}'
    records = download_klines_futu(futu_code, cache_file)
    if records and len(records) >= 50:
        return records

    # TickFlow 备选
    full_code = symbol_code  # e.g. '00700.HK'
    records = download_klines_tickflow(full_code, cache_file)
    if records and len(records) >= 50:
        return records

    return None


# === 技术指标计算 =====================================================
def calc_ema(prices, period):
    """指数移动平均"""
    if len(prices) < period:
        return None
    k = 2 / (period + 1)
    ema = prices[0]
    for p in prices[1:]:
        ema = p * k + ema * (1 - k)
    return ema


def calc_ema_series(prices, period):
    """EMA 序列 (返回完整列表, oldest first)"""
    if len(prices) < period:
        return []
    k = 2 / (period + 1)
    ema_vals = [sum(prices[:period]) / period]  # SMA seed
    for p in prices[period:]:
        ema_vals.append(p * k + ema_vals[-1] * (1 - k))
    return ema_vals


def calc_rsi(prices, period=14):
    """Wilder RSI (SMA 初始化)"""
    if len(prices) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    # Wilder smoothing: SMA 初始化, 之后递推
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def calc_macd(prices, fast=12, slow=26, signal=9):
    """MACD (DIF, DEA, Histogram)"""
    if len(prices) < slow + signal:
        return None, None, None
    # 完整 EMA 序列
    k_fast = 2 / (fast + 1)
    k_slow = 2 / (slow + 1)
    ema_f = prices[0]
    ema_s = prices[0]
    macd_line = []
    for p in prices:
        ema_f = p * k_fast + ema_f * (1 - k_fast)
        ema_s = p * k_slow + ema_s * (1 - k_slow)
        macd_line.append(ema_f - ema_s)
    # Signal line (EMA of MACD line)
    k_sig = 2 / (signal + 1)
    sig_ema = macd_line[0]
    for m in macd_line[1:]:
        sig_ema = m * k_sig + sig_ema * (1 - k_sig)
    # 用最后 signal*2 根做更精确的 signal line
    sig_ema2 = macd_line[-signal * 2]
    for m in macd_line[-signal * 2 + 1:]:
        sig_ema2 = m * k_sig + sig_ema2 * (1 - k_sig)
    macd_val = macd_line[-1]
    histogram = macd_val - sig_ema2
    return macd_val, sig_ema2, histogram


def calc_atr(highs, lows, closes, period=14):
    """ATR 平均真实波幅"""
    if len(closes) < period + 1:
        return None
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
        trs.append(tr)
    return sum(trs[-period:]) / period


def calc_bollinger(closes, period=20, num_std=2):
    """布林带 (中轨, 上轨, 下轨, %B)"""
    if len(closes) < period:
        return None, None, None, None
    sma = sum(closes[-period:]) / period
    variance = sum((c - sma) ** 2 for c in closes[-period:]) / period
    std = variance ** 0.5
    upper = sma + num_std * std
    lower = sma - num_std * std
    pct_b = (closes[-1] - lower) / (upper - lower) if upper != lower else 0.5
    return sma, upper, lower, pct_b


# === 信号引擎 =========================================================
def generate_signal(symbol_code, name=''):
    """生成单个港股标的的技术信号"""
    records = load_kline(symbol_code)
    if not records or len(records) < 50:
        return None

    closes = [r['close'] for r in records]
    highs = [r['high'] for r in records]
    lows = [r['low'] for r in records]
    volumes = [r['volume'] for r in records]
    last_date = records[-1]['date']

    # --- 技术指标 ---
    ema_7 = calc_ema(closes, 7)
    ema_25 = calc_ema(closes, 25)
    ema_99 = calc_ema(closes, 99) if len(closes) >= 99 else None
    rsi_14 = calc_rsi(closes, 14)
    macd_val, signal_line, histogram = calc_macd(closes)
    atr_14 = calc_atr(highs, lows, closes, 14)
    bb_mid, bb_upper, bb_lower, bb_pctb = calc_bollinger(closes)

    # --- 变化率 ---
    change_1d = (closes[-1] - closes[-2]) / closes[-2] * 100 if len(closes) > 1 else 0
    change_5d = (closes[-1] - closes[-6]) / closes[-6] * 100 if len(closes) > 5 else 0
    change_20d = (closes[-1] - closes[-21]) / closes[-21] * 100 if len(closes) > 20 else 0

    # --- 成交量 ---
    vol_avg_20 = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else 0
    vol_ratio = volumes[-1] / vol_avg_20 if vol_avg_20 > 0 else 1.0

    # --- ATR % ---
    atr_pct = (atr_14 / closes[-1] * 100) if atr_14 and closes[-1] else 0

    # ========================== 综合评分 ==========================
    score = 0
    reasons = []

    # (1) EMA 趋势 (权重 30%, max ±30)
    if ema_7 and ema_25:
        if ema_7 > ema_25:
            score += 15
            reasons.append("EMA7>EMA25 短期多头")
            if ema_99 and ema_7 > ema_99:
                score += 15
                reasons.append("EMA7>EMA99 强势排列")
        else:
            score -= 15
            reasons.append("EMA7<EMA25 短期空头")
            if ema_99 and ema_7 < ema_99:
                score -= 10
                reasons.append("EMA7<EMA99 弱势排列")

    # (2) RSI (权重 25%, max ±20)
    if rsi_14 is not None:
        if rsi_14 < 30:
            score += 20
            reasons.append(f"RSI={rsi_14:.0f} 超卖")
        elif rsi_14 < 40:
            score += 10
            reasons.append(f"RSI={rsi_14:.0f} 偏低")
        elif rsi_14 > 70:
            score -= 20
            reasons.append(f"RSI={rsi_14:.0f} 超买")
        elif rsi_14 > 60:
            score -= 5
            reasons.append(f"RSI={rsi_14:.0f} 偏高")
        else:
            reasons.append(f"RSI={rsi_14:.0f} 中性")

    # (3) MACD (权重 25%, max ±20)
    if histogram is not None:
        if histogram > 0 and macd_val > 0:
            score += 20
            reasons.append("MACD 多头共振")
        elif histogram > 0:
            score += 10
            reasons.append("MACD 金叉")
        elif histogram < 0 and macd_val < 0:
            score -= 20
            reasons.append("MACD 空头共振")
        elif histogram < 0:
            score -= 10
            reasons.append("MACD 死叉")

    # (4) 动量 (权重 10%, max ±5)
    if change_1d > 3:
        score += 5
        reasons.append(f"日涨{change_1d:+.1f}%")
    elif change_1d < -3:
        score -= 5
        reasons.append(f"日跌{change_1d:+.1f}%")

    # (5) 成交量 (权重 10%, max +10)
    if vol_ratio > 1.5:
        score += 5
        reasons.append(f"放量{vol_ratio:.1f}x")
        if change_1d > 0:
            score += 5
            reasons.append("放量上涨")

    # (6) 布林带位置加分
    if bb_pctb is not None:
        if bb_pctb < 0.05:
            score += 5
            reasons.append("触及布林下轨")
        elif bb_pctb > 0.95:
            score -= 5
            reasons.append("触及布林上轨")

    # --- 信号分级 ---
    if score >= 30:
        signal = "STRONG_BUY"
    elif score >= 15:
        signal = "BUY"
    elif score <= -30:
        signal = "STRONG_SELL"
    elif score <= -15:
        signal = "SELL"
    else:
        signal = "NEUTRAL"

    return {
        'code': symbol_code.replace('.HK', ''),
        'name': name,
        'last_date': last_date,
        'last_price': closes[-1],
        'change_1d': change_1d,
        'change_5d': change_5d,
        'change_20d': change_20d,
        'rsi': rsi_14,
        'ema_7': ema_7,
        'ema_25': ema_25,
        'ema_99': ema_99,
        'macd': macd_val,
        'macd_signal': signal_line,
        'macd_hist': histogram,
        'atr': atr_14,
        'atr_pct': atr_pct,
        'bb_mid': bb_mid,
        'bb_upper': bb_upper,
        'bb_lower': bb_lower,
        'bb_pctb': bb_pctb,
        'vol_ratio': vol_ratio,
        'score': score,
        'signal': signal,
        'reasons': reasons,
    }


# === 主扫描 ============================================================
def scan_hk_market():
    """扫描港股核心标的"""
    symbols = [(code, info.get('name', '')) for code, info in config.UNIVERSE_HK.items()]

    print("=" * 78)
    print(f"  HK 港股技术扫描 | {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 78)

    results = []
    total = len(symbols)
    for i, (code, name) in enumerate(symbols, 1):
        print(f"  [{i}/{total}] {code} {name}...", end=" ", flush=True)
        sig = generate_signal(code, name)
        if sig:
            sig_emoji = {"STRONG_BUY": "STRONG_BUY", "BUY": "BUY", "NEUTRAL": "NEUTRAL",
                         "SELL": "SELL", "STRONG_SELL": "STRONG_SELL"}.get(sig['signal'], "")
            print(f"{sig['signal']} (score={sig['score']:+d})")
            results.append(sig)
        else:
            print("FAIL")
        time.sleep(0.3)

    if not results:
        print("\n  无有效扫描结果")
        return []

    # 按 score 排序
    results.sort(key=lambda x: x['score'], reverse=True)

    # ======================== 输出报告 ========================
    print()
    print("=" * 78)
    print("  扫描结果汇总")
    print("=" * 78)
    print()

    # 总览表
    header = f"{'代码':<8} {'名称':<8} {'价格':>8} {'日涨跌':>7} {'5d%':>7} {'RSI':>5} {'MACD_H':>8} {'评分':>5} {'信号':<12}"
    print(header)
    print("-" * len(header))

    for r in results:
        rsi_str = f"{r['rsi']:.0f}" if r['rsi'] is not None else "N/A"
        hist_str = f"{r['macd_hist']:.3f}" if r['macd_hist'] is not None else "N/A"
        signal_icon = {"STRONG_BUY": " *** BUY", "BUY": " BUY",
                       "NEUTRAL": " ---", "SELL": " SELL", "STRONG_SELL": " *** SELL"}.get(r['signal'], "")
        print(f"{r['code']:<8} {r['name']:<8} {r['last_price']:>8.2f} "
              f"{r['change_1d']:>+6.1f}% {r['change_5d']:>+6.1f}% "
              f"{rsi_str:>5} {hist_str:>8} {r['score']:>+5} {signal_icon}")

    # ======================== 买入信号详情 ========================
    buys = [r for r in results if r['score'] >= 15]
    if buys:
        print()
        print("-" * 78)
        print("  BUY 信号详情")
        print("-" * 78)
        for r in buys:
            print(f"\n  {r['code']} {r['name']} | {r['signal']} | Score: {r['score']:+d}")
            print(f"    价格: HK${r['last_price']:.2f} | 1d: {r['change_1d']:+.1f}% | 5d: {r['change_5d']:+.1f}% | 20d: {r['change_20d']:+.1f}%")
            print(f"    RSI: {r['rsi']:.1f} | ATR: {r['atr_pct']:.2f}% | Vol: {r['vol_ratio']:.1f}x")
            if r['ema_7'] and r['ema_25']:
                print(f"    EMA7: {r['ema_7']:.2f} | EMA25: {r['ema_25']:.2f} | EMA99: {r.get('ema_99', 'N/A')}")
            if r['macd_hist'] is not None:
                print(f"    MACD: {r['macd']:.4f} | Signal: {r['macd_signal']:.4f} | Hist: {r['macd_hist']:.4f}")
            if r['bb_pctb'] is not None:
                print(f"    BB: [{r['bb_lower']:.2f} | {r['bb_mid']:.2f} | {r['bb_upper']:.2f}] %B={r['bb_pctb']:.2f}")
            print(f"    理由: {' | '.join(r['reasons'])}")

    # ======================== 卖出信号详情 ========================
    sells = [r for r in results if r['score'] <= -15]
    if sells:
        print()
        print("-" * 78)
        print("  SELL 信号详情")
        print("-" * 78)
        for r in sells:
            print(f"\n  {r['code']} {r['name']} | {r['signal']} | Score: {r['score']:+d}")
            print(f"    价格: HK${r['last_price']:.2f} | 1d: {r['change_1d']:+.1f}% | 5d: {r['change_5d']:+.1f}%")
            print(f"    理由: {' | '.join(r['reasons'])}")

    # ======================== 市场全景 ========================
    print()
    print("-" * 78)
    print("  市场全景")
    print("-" * 78)
    sb = len([r for r in results if r['signal'] == 'STRONG_BUY'])
    b = len([r for r in results if r['signal'] == 'BUY'])
    n = len([r for r in results if r['signal'] == 'NEUTRAL'])
    s = len([r for r in results if r['signal'] == 'SELL'])
    ss = len([r for r in results if r['signal'] == 'STRONG_SELL'])
    avg_score = sum(r['score'] for r in results) / len(results)
    avg_rsi = sum(r['rsi'] for r in results if r['rsi']) / len([r for r in results if r['rsi']]) if any(r['rsi'] for r in results) else 0

    print(f"  STRONG_BUY: {sb} | BUY: {b} | NEUTRAL: {n} | SELL: {s} | STRONG_SELL: {ss}")
    print(f"  平均评分: {avg_score:+.1f} | 平均RSI: {avg_rsi:.0f}")

    if avg_score > 10:
        print(f"  市场倾向: 偏多 ({avg_score:+.1f})")
    elif avg_score < -10:
        print(f"  市场倾向: 偏空 ({avg_score:+.1f})")
    else:
        print(f"  市场倾向: 中性 ({avg_score:+.1f})")

    # ======================== 保存 ========================
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    csv_file = OUTPUT_DIR / f'hk_tech_scan_{ts}.csv'
    df = pd.DataFrame(results)
    df.to_csv(csv_file, index=False, encoding='utf-8-sig')

    json_file = OUTPUT_DIR / f'hk_tech_scan_{ts}.json'
    output = {
        'timestamp': datetime.now().isoformat(),
        'summary': {
            'total': len(results), 'strong_buy': sb, 'buy': b,
            'neutral': n, 'sell': s, 'strong_sell': ss,
            'avg_score': round(avg_score, 1), 'avg_rsi': round(avg_rsi, 1),
        },
        'results': [{k: v for k, v in r.items()} for r in results]
    }
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, default=str, ensure_ascii=False)

    print(f"\n  [SAVED] {csv_file}")
    print(f"  [SAVED] {json_file}")
    print("=" * 78)

    return results


if __name__ == "__main__":
    results = scan_hk_market()
