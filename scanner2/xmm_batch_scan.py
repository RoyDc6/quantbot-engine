# -*- coding: utf-8 -*-
"""
xmm_batch_scan.py - 徐小明策略批量扫描港股全市场
TD9序列 + MACD结构 + 多周期共�?"""
import os, sys, json
import pandas as pd
from pathlib import Path
from datetime import datetime

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, 'C:/Users/RoyGoode/.workbuddy\skills/xmm-strategy/scripts')
from xmm_signals import XMMSignalEngine

from core.paths import SCANNER2_CACHE, OUTPUT_DIR
CACHE_DIR = str(SCANNER2_CACHE)
OUTPUT_DIR = str(_ROOT / 'output')


def load_json_kline(filepath):
    """Load K-line data from JSON cache"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, list):
            df = pd.DataFrame(data)
        elif isinstance(data, dict):
            for key in ['data', 'items', 'klines', 'candlesticks']:
                if key in data:
                    df = pd.DataFrame(data[key])
                    break
            else:
                df = pd.DataFrame([data])
        else:
            return None

        if 'trade_date' in df.columns:
            df = df.rename(columns={'trade_date': 'date'})
        elif 'trade_time' in df.columns:
            df = df.rename(columns={'trade_time': 'date'})
        elif 'datetime' in df.columns:
            df = df.rename(columns={'datetime': 'date'})

        ohlcv_mapping = {'o': 'open', 'h': 'high', 'l': 'low', 'c': 'close', 'v': 'volume', 'vol': 'volume'}
        df = df.rename(columns={k: v for k, v in ohlcv_mapping.items() if k in df.columns})

        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'], format='%Y-%m-%d', errors='coerce')
            if df['date'].isna().all():
                df['date'] = pd.to_datetime(df['date'], errors='coerce')

        df = df.dropna(subset=['open', 'high', 'low', 'close'])
        return df
    except Exception as e:
        return None


def batch_xmm_scan(cache_dir=CACHE_DIR, market='HK'):
    cache_path = Path(cache_dir)
    pattern = f'*_{market}_1d.json'
    stock_files = list(cache_path.glob(pattern))

    if not stock_files:
        print(f"[ERROR] No {market} stock files found")
        return None

    print(f"\n[INFO] Found {len(stock_files)} {market} stock files")
    print("[INFO] Running XMM strategy (TD9 + MACD + Resonance)...\n")

    engine = XMMSignalEngine()
    results = []

    for i, filepath in enumerate(stock_files, 1):
        code = filepath.name.replace(f'_{market}_1d.json', '').replace(f'_{market}_', '.')
        df = load_json_kline(filepath)

        if df is None or len(df) < 35:
            continue

        # XMM analysis
        sig = engine.analyze(df)

        # RSI for additional context
        delta = df['close'].diff()
        gain = delta.where(delta > 0, 0.0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
        rs = gain / loss.replace(0, 0.0001)
        rsi = (100 - (100 / (1 + rs))).iloc[-1]

        # Recent momentum
        mom_5d = (df['close'].iloc[-1] / df['close'].iloc[-6] - 1) * 100 if len(df) >= 6 else 0
        mom_20d = (df['close'].iloc[-1] / df['close'].iloc[-21] - 1) * 100 if len(df) >= 21 else 0

        results.append({
            'code': code,
            'signal': sig['signal'],
            'strength': sig['strength'],
            'td9_count': sig['td9_count'],
            'macd_desc': sig['macd_desc'],
            'resonance': sig['resonance'],
            'reason': sig['reason'],
            'rsi': round(float(rsi), 1) if not pd.isna(rsi) else None,
            'momentum_5d': round(float(mom_5d), 2),
            'momentum_20d': round(float(mom_20d), 2),
            'latest_close': float(df['close'].iloc[-1]),
            'latest_date': str(df['date'].iloc[-1].date()) if pd.notna(df['date'].iloc[-1]) else None,
            'total_bars': len(df),
        })

        if i % 50 == 0:
            print(f"  Processed {i}/{len(stock_files)}...")

    print(f"\n[OK] Scanned {len(results)} stocks\n")
    return pd.DataFrame(results)


def classify_xmm(df):
    """Classify by XMM signals"""
    buy = df[df['signal'] == 'BUY'].copy()
    sell = df[df['signal'] == 'SELL'].copy()
    hold = df[df['signal'] == 'HOLD'].copy()

    buy = buy.sort_values('strength', ascending=False)
    sell = sell.sort_values('strength', ascending=False)

    # TD9 advanced counts (>=7 is strong)
    td9_strong_buy = df[df['td9_count'] <= -7].copy()
    td9_strong_sell = df[df['td9_count'] >= 7].copy()

    # MACD classification
    macd_bull = df[df['macd_desc'].str.contains('上MACD', na=False)].copy()
    macd_bear = df[df['macd_desc'].str.contains('下MACD', na=False)].copy()

    return {
        'all': df,
        'buy': buy,
        'sell': sell,
        'hold': hold,
        'td9_strong_buy': td9_strong_buy.sort_values('td9_count'),
        'td9_strong_sell': td9_strong_sell.sort_values('td9_count', ascending=False),
        'macd_bull': macd_bull,
        'macd_bear': macd_bear,
        'summary': {
            'total': len(df),
            'buy_count': len(buy),
            'sell_count': len(sell),
            'hold_count': len(hold),
            'td9_buy_advanced': len(td9_strong_buy),
            'td9_sell_advanced': len(td9_strong_sell),
            'macd_bull_count': len(macd_bull),
            'macd_bear_count': len(macd_bear),
        }
    }


def print_report(cls, market='HK'):
    s = cls['summary']
    print("=" * 72)
    print(f"  徐小明策�?· 港股全市场扫描报�?)
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 72)

    print(f"\n【总览】扫�?{s['total']} 只股�?)
    print(f"  买入信号 BUY : {s['buy_count']:3d} ({s['buy_count']/s['total']*100:.1f}%)")
    print(f"  卖出信号 SELL: {s['sell_count']:3d} ({s['sell_count']/s['total']*100:.1f}%)")
    print(f"  观望 HOLD    : {s['hold_count']:3d} ({s['hold_count']/s['total']*100:.1f}%)")

    print(f"\n【TD9序列�?)
    print(f"  买入计数(�?7): {s['td9_buy_advanced']:3d} �?)
    print(f"  卖出计数(�?7): {s['td9_sell_advanced']:3d} �?)

    print(f"\n【MACD结构�?)
    print(f"  上MACD(多头): {s['macd_bull_count']:3d} �?)
    print(f"  下MACD(空头): {s['macd_bear_count']:3d} �?)

    # BUY signals
    print(f"\n【BUY信号 {s['buy_count']}只�?按强度排�?")
    print("-" * 72)
    buy_df = cls['buy'][['code','signal','strength','td9_count','macd_desc','rsi','momentum_5d','momentum_20d','latest_close']].head(20)
    if len(buy_df):
        print(buy_df.to_string(index=False))

    # SELL signals
    print(f"\n【SELL信号 {s['sell_count']}只�?按强度排�?")
    print("-" * 72)
    sell_df = cls['sell'][['code','signal','strength','td9_count','macd_desc','rsi','momentum_5d','momentum_20d','latest_close']].head(20)
    if len(sell_df):
        print(sell_df.to_string(index=False))

    # TD9 advanced
    if len(cls['td9_strong_buy']):
        print(f"\n【TD9买入计数(�?7) {s['td9_buy_advanced']}只�?)
        print("-" * 72)
        t9b = cls['td9_strong_buy'][['code','td9_count','rsi','momentum_5d','momentum_20d','latest_close']].head(15)
        print(t9b.to_string(index=False))

    if len(cls['td9_strong_sell']):
        print(f"\n【TD9卖出计数(�?7) {s['td9_sell_advanced']}只�?)
        print("-" * 72)
        t9s = cls['td9_strong_sell'][['code','td9_count','rsi','momentum_5d','momentum_20d','latest_close']].head(15)
        print(t9s.to_string(index=False))

    print("\n" + "=" * 72)


def save_results(cls, output_dir=OUTPUT_DIR, market='HK'):
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')

    all_csv = os.path.join(output_dir, f'xmm_all_{market}_{ts}.csv')
    cls['all'].to_csv(all_csv, index=False, encoding='utf-8-sig')

    buy_csv = os.path.join(output_dir, f'xmm_buy_{market}_{ts}.csv')
    if len(cls['buy']):
        cls['buy'].to_csv(buy_csv, index=False, encoding='utf-8-sig')

    sell_csv = os.path.join(output_dir, f'xmm_sell_{market}_{ts}.csv')
    if len(cls['sell']):
        cls['sell'].to_csv(sell_csv, index=False, encoding='utf-8-sig')

    td9_csv = os.path.join(output_dir, f'xmm_td9_{market}_{ts}.csv')
    td9_df = pd.concat([cls['td9_strong_buy'], cls['td9_strong_sell']])
    td9_df.to_csv(td9_csv, index=False, encoding='utf-8-sig')

    print(f"\n[SAVED]")
    print(f"  全量: {all_csv}")
    if len(cls['buy']):
        print(f"  买入: {buy_csv}")
    if len(cls['sell']):
        print(f"  卖出: {sell_csv}")
    print(f"  TD9:  {td9_csv}")


if __name__ == '__main__':
    df = batch_xmm_scan(market='HK')
    if df is not None:
        cls = classify_xmm(df)
        print_report(cls, market='HK')
        save_results(cls, market='HK')
