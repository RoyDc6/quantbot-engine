import pandas as pd, os

path = 'E:/quant/ml_alpha/hk_features_full.csv'
if os.path.exists(path):
    df = pd.read_csv(path)
    print(f'HK特征文件: {len(df)}行 x {len(df.columns)}列')
    print(f'股票数: {df["symbol"].nunique()}')
    print(f'列(前15): {list(df.columns[:15])}')
    print(f'日期: {df["trade_date"].min()} ~ {df["trade_date"].max()}')
    # 看下有哪些关键因子
    key_cols = [c for c in df.columns if any(x in c for x in ['rsi','sma','macd','fractal','chan'])]
    print(f'关键因子: {key_cols[:20]}')
else:
    print('文件不存在')
