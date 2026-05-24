"""
复杂度因子 IC验证 - TickFlow免费版
使用 TickFlow 获取港股200只K线，计算复杂度因子验证IC
"""

import os, sys, time, warnings, io
import numpy as np
import pandas as pd
from tqdm import tqdm

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, r'E:\quant\ml_alpha')
from complexity_factors import compute_complexity_factors

warnings.filterwarnings('ignore')

OUT_DIR     = r'E:\quant\ml_alpha'
RESULT_FILE = os.path.join(OUT_DIR, 'complexity_partial_results.csv')

FACTOR_COLS = [
    'hurst_20', 'hurst_60', 'higuchi_fd_20', 'higuchi_fd_60',
    'apen_20', 'apen_60', 'sampen_20', 'sampen_60',
    'entropy_20', 'entropy_60',
]

# ── 股票池 ──────────────────────────────────────────────
UNIVERSE = [
    '00700.HK','09988.HK','00941.HK','00939.HK','00992.HK',
    '00388.HK','00005.HK','02318.HK','02628.HK','01398.HK',
    '00777.HK','01109.HK','06690.HK','01810.HK','03888.HK',
    '00291.HK','00292.HK','01088.HK','01093.HK','02269.HK',
    '02382.HK','06160.HK','09618.HK','06618.HK','09888.HK',
    '00175.HK','02333.HK','02020.HK','06098.HK','09961.HK',
    '09987.HK','03968.HK','03988.HK','03993.HK','02899.HK',
    '02328.HK','03692.HK','00836.HK','00857.HK','00883.HK',
    '00914.HK','00981.HK','01299.HK','01336.HK','01658.HK',
    '01766.HK','01776.HK','01787.HK','01800.HK','01816.HK',
    '01898.HK','01919.HK','01929.HK','01988.HK','02009.HK',
    '02313.HK','02128.HK','02158.HK','02202.HK','02314.HK',
    '02319.HK','02359.HK','02386.HK','02392.HK','02518.HK',
    '02569.HK','02611.HK','02688.HK','02883.HK','03328.HK',
    '03606.HK','03669.HK','03690.HK','03759.HK','03800.HK',
    '03908.HK','03928.HK','03933.HK','03990.HK','06030.HK',
    '06049.HK','06055.HK','06118.HK','06169.HK','06185.HK',
    '06655.HK','06680.HK','06688.HK','06696.HK','06772.HK',
    '06818.HK','06865.HK','06869.HK','06888.HK','06900.HK',
    '06913.HK','06919.HK','06950.HK','06969.HK','06988.HK',
    '06996.HK','09911.HK','09992.HK','09995.HK','09996.HK',
    '09998.HK','01833.HK','06186.HK','01579.HK','01847.HK',
    '02600.HK','02245.HK','02586.HK','02669.HK','02768.HK',
    '03383.HK','03393.HK','03808.HK','03868.HK','03898.HK',
    '06158.HK','06178.HK','06603.HK','06606.HK','06608.HK',
    '06613.HK','06628.HK','06828.HK','06858.HK','06898.HK',
    '06908.HK','06958.HK','06998.HK',
]
UNIVERSE = list(dict.fromkeys(UNIVERSE))

# ── IC ──────────────────────────────────────────────────
def calc_ic(pred, actual):
    mask = ~(np.isnan(pred) | np.isnan(actual))
    if mask.sum() < 30:
        return np.nan, np.nan
    from scipy.stats import spearmanr
    ic = np.corrcoef(pred[mask], actual[mask])[0, 1]
    rank_ic = spearmanr(pred[mask], actual[mask])[0]
    return ic, rank_ic

# ── 主流程 ──────────────────────────────────────────────
import tickflow_env  # noqa: auto-inject TICKFLOW_API_KEY
from tickflow import TickFlow
tf = TickFlow() if os.environ.get('TICKFLOW_API_KEY') else TickFlow.free()

# 断点
if os.path.exists(RESULT_FILE):
    df_all = pd.read_csv(RESULT_FILE, parse_dates=['date'])
    done_symbols = set(df_all['symbol'].unique())
    print(f"[Resume] {len(done_symbols)} stocks already done, {len(df_all)} records")
else:
    df_all = pd.DataFrame()
    done_symbols = set()

todo = [s for s in UNIVERSE if s not in done_symbols]
print(f"Total: {len(UNIVERSE)}, To process: {len(todo)}, Done: {len(done_symbols)}")

# ── 主循环 ──────────────────────────────────────────────
for symbol in tqdm(todo, desc='K-line'):
    try:
        df = tf.klines.get(symbol, period='1d', count=200, as_dataframe=True)
    except Exception as e:
        tqdm.write(f'[ERR DL] {symbol}: {e}')
        time.sleep(2)
        continue

    if df is None or len(df) < 80:
        tqdm.write(f'[SKIP] {symbol} (no data)')
        time.sleep(0.2)
        continue

    # 整理列名
    if 'trade_date' in df.columns:
        df['date'] = pd.to_datetime(df['trade_date'])
    elif 'date' in df.columns:
        df['date'] = pd.to_datetime(df['date'])
    
    df['close'] = pd.to_numeric(df['close'], errors='coerce')
    df['ret'] = df['close'].pct_change()
    df['future_5d'] = df['close'].shift(-5) / df['close'] - 1

    records = []
    for i in range(65, len(df) - 5):
        window = df['ret'].iloc[:i + 1].dropna().values
        if len(window) < 60:
            continue
        factors = compute_complexity_factors(window)
        if any(
            (v is None) or (isinstance(v, float) and np.isnan(v))
            for v in factors.values()
        ):
            continue
        records.append({
            'symbol': symbol,
            'date': df['date'].iloc[i],
            'close': df['close'].iloc[i],
            'future_5d': df['future_5d'].iloc[i],
            **factors
        })

    if records:
        new_df = pd.DataFrame(records)
        df_all = pd.concat([df_all, new_df], ignore_index=True)
        # 实时写盘
        df_all.to_csv(RESULT_FILE, index=False, encoding='utf-8-sig')

    time.sleep(0.2)

# ── IC ──────────────────────────────────────────────────
if df_all.empty:
    print("No data!")
    sys.exit(1)

df_all['date'] = pd.to_datetime(df_all['date'])
print(f"\nTotal: {len(df_all)} records, {df_all['symbol'].nunique()} stocks")
print(f"Period: {df_all['date'].min().date()} ~ {df_all['date'].max().date()}")

ic_results = []
for col in FACTOR_COLS:
    ic, rank_ic = calc_ic(df_all[col].values, df_all['future_5d'].values)
    ic_results.append({'factor': col, 'IC': ic, 'RankIC': rank_ic,
                        'valid': df_all[col].notna().sum()})

ic_df = pd.DataFrame(ic_results).sort_values('RankIC', key=abs, ascending=False)

print("\n" + "="*65)
print("  Complexity Factor IC (label: 5-day forward return)")
print("="*65)
print(f"  {'Factor':22s}  {'IC':>8s}  {'RankIC':>8s}  {'N':>6s}  Rating")
print("-"*65)
for _, row in ic_df.iterrows():
    stars = "⭐⭐⭐" if abs(row['RankIC']) > 0.05 else (
        "⭐⭐" if abs(row['RankIC']) > 0.03 else (
            "⭐" if abs(row['RankIC']) > 0.01 else ""))
    print(f"  {row['factor']:22s}  {row['IC']:+8.4f}  {row['RankIC']:+8.4f}  {row['valid']:>6d}  {stars}")

ic_df.to_csv(os.path.join(OUT_DIR, 'complexity_ic_results.csv'),
             index=False, encoding='utf-8-sig')
df_all.to_csv(os.path.join(OUT_DIR, 'complexity_factor_data.csv'),
              index=False, encoding='utf-8-sig')
print(f"\nSaved: complexity_ic_results.csv, complexity_factor_data.csv")
