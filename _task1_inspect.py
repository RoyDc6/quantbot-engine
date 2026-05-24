import pandas as pd
df = pd.read_csv('ml_alpha/fusion_model_results.csv')
print('Shape:', df.shape)
print('Columns:', list(df.columns))
print()
print(df.head(3).to_string())
print()
print('Symbols:', df['symbol'].unique()[:20] if 'symbol' in df.columns else 'N/A')
print('Date range:', df['trade_date'].min(), '~', df['trade_date'].max() if 'trade_date' in df.columns else 'N/A')
