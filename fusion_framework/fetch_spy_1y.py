# -*- coding: utf-8 -*-
import os
os.chdir(r'E:\quant\fusion_framework')
import sys
sys.path.insert(0, 'E:/quant')

import warnings
warnings.filterwarnings('ignore')

import tickflow_env  # noqa: auto-inject TICKFLOW_API_KEY
from tickflow import TickFlow
tf = TickFlow() if os.environ.get('TICKFLOW_API_KEY') else TickFlow.free()  # 有 API key 用付费版

print("获取 SPY.US 1年日K线数据...")
try:
    df = tf.klines.get("SPY.US", period="1d", count=500, as_dataframe=True)
    print(f"获取行数: {len(df)}")
    print(f"起始: {df['trade_date'].iloc[0]}")
    print(f"结束: {df['trade_date'].iloc[-1]}")
    
    close = df['close'].values
    print(f"价格范围: {close.min():.2f} ~ {close.max():.2f}")
    print(f"收益: +{(close[-1]/close[0]-1)*100:.1f}%")
    
    # 保存
    cache_dir = "E:/quant/scanner/cache"
    os.makedirs(cache_dir, exist_ok=True)
    save_path = f"{cache_dir}/SPY_US_1y.json"
    df.to_json(save_path, orient='records', date_format='iso')
    print(f"已保存: {save_path}")
    
except Exception as e:
    print(f"错误: {e}")
    import traceback; traceback.print_exc()
