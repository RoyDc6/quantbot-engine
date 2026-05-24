# -*- coding: utf-8 -*-
import os
os.chdir(r'E:\quant\fusion_framework')
import sys
sys.path.insert(0, 'E:/quant')

import warnings
warnings.filterwarnings('ignore')

from tickflow import TickFlow

tf = TickFlow.free()  # 无 API key，使用免费层

# 测试SPY获取能力
print("测试 TickFlow SPY.US 获取能力...")
try:
    df = tf.kline('SPY.US', period='1d', limit=500)
    print(f"获取行数: {len(df)}")
    if len(df) > 0:
        first = df.iloc[0]
        last = df.iloc[-1]
        print(f"起始: {first}")
        print(f"结束: {last}")
        # 计算天数
        from datetime import datetime
        try:
            d1 = datetime.strptime(str(df['trade_date'].iloc[0]), '%Y-%m-%d')
            d2 = datetime.strptime(str(df['trade_date'].iloc[-1]), '%Y-%m-%d')
            print(f"跨度: {(d2-d1).days} 天")
        except:
            pass
except Exception as e:
    print(f"错误: {e}")
    import traceback; traceback.print_exc()
