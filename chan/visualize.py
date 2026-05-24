# -*- coding: utf-8 -*-
"""
visualize.py - 缠论结构可视化
"""
import pandas as pd
import numpy as np
from typing import List, Optional

try:
    import mplfinance as mpf
    import matplotlib.pyplot as plt
    HAS_MPLFINANCE = True
except ImportError:
    HAS_MPLFINANCE = False
    print("[WARNING] mplfinance not installed, visualization disabled")


def plot_chan_structure(
    df: pd.DataFrame,
    strokes: Optional[List] = None,
    segments: Optional[List] = None,
    pivots: Optional[List] = None,
    signals: Optional[List] = None,
    title: str = "Chan Theory Structure",
    save_path: Optional[str] = None,
    show: bool = True,
) -> None:
    """
    绘制K线+缠论结构图
    
    参数:
        df: 原始K线数据（需包含date, open, high, low, close, volume）
        strokes: 笔列表
        segments: 线段列表
        pivots: 中枢列表
        signals: 信号列表
        title: 图表标题
        save_path: 保存路径（可选）
        show: 是否显示图表
    """
    if not HAS_MPLFINANCE:
        print("[ERROR] mplfinance not available")
        return
    
    # 确保date列存在且为datetime类型
    df = df.copy()
    if 'date' in df.columns:
        df['date'] = pd.to_datetime(df['date'])
    df = df.set_index('date')
    
    # 准备附加图层
    apds = []
    
    # 绘制笔（连线）
    if strokes and len(strokes) > 0:
        stroke_dates = []
        stroke_prices = []
        for s in strokes:
            stroke_dates.extend([s.start_date, s.end_date])
            stroke_prices.extend([s.start_price, s.end_price])
            stroke_dates.append(None)  # 断开
            stroke_prices.append(np.nan)
        
        stroke_series = pd.Series(stroke_prices, index=pd.to_datetime(stroke_dates))
        stroke_series = stroke_series.sort_index()
        
        apds.append(mpf.make_addplot(
            stroke_series,
            type='line',
            color='blue',
            width=1.5,
            label='Stroke'
        ))
    
    # 绘制中枢（矩形框）
    # mplfinance不直接支持矩形，用水平线近似
    
    # 买卖点标记
    if signals and len(signals) > 0:
        buy_dates = []
        buy_prices = []
        sell_dates = []
        sell_prices = []
        
        for sig in signals:
            date = pd.to_datetime(sig.date)
            if 'buy' in sig.signal_type:
                buy_dates.append(date)
                buy_prices.append(sig.price)
            elif 'sell' in sig.signal_type:
                sell_dates.append(date)
                sell_prices.append(sig.price)
        
        if buy_dates:
            buy_series = pd.Series(buy_prices, index=buy_dates)
            apds.append(mpf.make_addplot(
                buy_series,
                type='scatter',
                markersize=100,
                marker='^',
                color='red',
                label='Buy'
            ))
        
        if sell_dates:
            sell_series = pd.Series(sell_prices, index=sell_dates)
            apds.append(mpf.make_addplot(
                sell_series,
                type='scatter',
                markersize=100,
                marker='v',
                color='green',
                label='Sell'
            ))
    
    # 绘制K线图
    kwargs = dict(
        type='candle',
        style='yahoo',
        title=title,
        ylabel='Price',
        volume=True,
        figsize=(14, 8),
    )
    
    if len(apds) > 0:
        kwargs['addplot'] = apds
    
    if save_path:
        kwargs['savefig'] = save_path
    
    if show:
        mpf.plot(df, **kwargs)
    else:
        mpf.plot(df, **{k: v for k, v in kwargs.items() if k != 'savefig'})


def plot_simple_chan(
    df: pd.DataFrame,
    strokes: List,
    output_dir: str = 'E:/quant/output',
) -> str:
    """
    简化版可视化（仅K线+笔）
    
    返回:
        保存的文件路径
    """
    if not HAS_MPLFINANCE:
        return ""
    
    import os
    os.makedirs(output_dir, exist_ok=True)
    
    latest_date = df['date'].iloc[-1].strftime('%Y%m%d')
    save_path = os.path.join(output_dir, f'chan_{latest_date}.png')
    
    plot_chan_structure(
        df=df,
        strokes=strokes,
        title=f"Chan Theory - {latest_date}",
        save_path=save_path,
        show=False,
    )
    
    return save_path


if __name__ == '__main__':
    print("Use test_chan.py for full testing")
