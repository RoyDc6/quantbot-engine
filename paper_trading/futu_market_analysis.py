"""
使用 Futu API 获取市场数据
"""
from futu import OpenQuoteContext, KLType, Market, QKType
import pandas as pd
from datetime import datetime, timedelta

print("=" * 80)
print("市场核心驱动力分析 (Futu API)")
print("=" * 80)
print(f"分析时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print()

quote_ctx = OpenQuoteContext(host='127.0.0.1', port=11111)

# 1. 美股 SPY
print("【美股】SPY")
print("-" * 80)
try:
    # 获取最近60天的日线数据
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')

    print(f"获取数据范围: {start_date} 至 {end_date}")

    ret, data = quote_ctx.get_history_kline(
        'US.SPY',
        start=start_date,
        end=end_date,
        ktype=KLType.K_DAY,
        autype=QKType.QK_NONE
    )

    print(f"返回码: {ret}, 数据条数: {len(data) if ret == 0 else 0}")

    if ret == 0 and len(data) > 0:
        data = data.sort_values('time_key')
        latest = data.iloc[-1]

        # 计算 MA20 和 MA60
        data['ma20'] = data['close'].rolling(20).mean()
        data['ma60'] = data['close'].rolling(60).mean()

        print(f"最新价格: {latest['close']:.2f}")
        print(f"MA20: {latest['ma20']:.2f}")
        print(f"MA60: {latest['ma60']:.2f}")

        if latest['close'] > latest['ma20'] > latest['ma60']:
            print("趋势: 强势上涨")
        elif latest['close'] < latest['ma20'] < latest['ma60']:
            print("趋势: 弱势下跌")
        else:
            print("趋势: 震荡整理")
    else:
        print(f"数据获取失败: {ret}")
except Exception as e:
    print(f"错误: {e}")
    import traceback
    traceback.print_exc()

print()

# 2. 港股 00700
print("【港股】腾讯控股 (00700.HK)")
print("-" * 80)
try:
    ret, data = quote_ctx.get_history_kline(
        'HK.00700',
        start=start_date,
        end=end_date,
        ktype=KLType.K_DAY,
        autype=QKType.QK_NONE
    )

    print(f"返回码: {ret}, 数据条数: {len(data) if ret == 0 else 0}")

    if ret == 0 and len(data) > 0:
        data = data.sort_values('time_key')
        latest = data.iloc[-1]

        data['ma20'] = data['close'].rolling(20).mean()

        print(f"最新价格: {latest['close']:.2f}")
        print(f"MA20: {latest['ma20']:.2f}")

        if latest['close'] > latest['ma20']:
            print("位置: 站上 MA20")
        else:
            print("位置: 跌破 MA20")
    else:
        print(f"数据获取失败: {ret}")
except Exception as e:
    print(f"错误: {e}")
    import traceback
    traceback.print_exc()

print()

# 3. A股 上证指数
print("【A股】上证指数 (000001.SH)")
print("-" * 80)
try:
    ret, data = quote_ctx.get_history_kline(
        'SH.000001',
        start=start_date,
        end=end_date,
        ktype=KLType.K_DAY,
        autype=QKType.QK_NONE
    )

    print(f"返回码: {ret}, 数据条数: {len(data) if ret == 0 else 0}")

    if ret == 0 and len(data) > 0:
        data = data.sort_values('time_key')
        latest = data.iloc[-1]

        data['ma20'] = data['close'].rolling(20).mean()
        data['ma60'] = data['close'].rolling(60).mean()

        print(f"最新点位: {latest['close']:.2f}")
        print(f"MA20: {latest['ma20']:.2f}")
        print(f"MA60: {latest['ma60']:.2f}")

        if latest['close'] > latest['ma20'] > latest['ma60']:
            print("趋势: 偏强")
        elif latest['close'] < latest['ma20'] < latest['ma60']:
            print("趋势: 偏弱")
        else:
            print("趋势: 震荡")
    else:
        print(f"数据获取失败: {ret}")
except Exception as e:
    print(f"错误: {e}")
    import traceback
    traceback.print_exc()

print()
print("=" * 80)
print("分析完成")
print("=" * 80)

quote_ctx.close()
