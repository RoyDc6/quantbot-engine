"""
research/data/base.py — 市场无关数据抽象层

定义 UnifiedDataProvider 抽象基类和 KLineData 数据容器。
所有市场的数据提供者（Futu/OKX）都实现此接口，确保上层因子计算代码与数据源解耦。
"""

from __future__ import annotations

import pandas as pd
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class KLineData:
    """统一 K 线数据容器

    所有数据提供者返回此格式，因子计算层只依赖此结构。
    """
    symbol: str
    market: str                 # "US" | "HK" | "CN" | "CRYPTO"
    timeframe: str              # "1d" | "1h" | "5m" | "15m" | ...
    df: pd.DataFrame            # 标准列: open, high, low, close, volume
    provider: str = ""          # 数据来源标识

    def __post_init__(self):
        """确保 DataFrame 有标准列名"""
        required = {'open', 'high', 'low', 'close', 'volume'}
        missing = required - set(self.df.columns)
        if missing:
            raise ValueError(f"KLineData.df 缺少必要列: {missing}")

    @property
    def n_bars(self) -> int:
        return len(self.df)

    @property
    def date_range(self) -> tuple:
        if 'date' in self.df.columns:
            return (self.df['date'].iloc[0], self.df['date'].iloc[-1])
        if isinstance(self.df.index, pd.DatetimeIndex):
            return (self.df.index[0], self.df.index[-1])
        return (None, None)


class UnifiedDataProvider(ABC):
    """统一数据提供者抽象基类

    所有市场的数据获取都实现此接口。
    """

    @abstractmethod
    def fetch_klines(self, symbol: str, count: int = 500,
                     timeframe: str = "1d") -> KLineData:
        """获取 K 线数据

        Args:
            symbol: 标的代码（如 "00700", "SPY", "BTC-USDT"）
            count: K 线数量
            timeframe: 时间周期

        Returns:
            KLineData 统一数据容器
        """
        ...

    @abstractmethod
    def list_available_symbols(self, market: str) -> List[str]:
        """列出某市场可用的标的列表"""
        ...

    def fetch_multiple(self, symbols: List[str], count: int = 500,
                       timeframe: str = "1d") -> dict[str, KLineData]:
        """批量获取多个标的 K 线（默认串行，子类可重写为并行）"""
        result = {}
        for sym in symbols:
            try:
                result[sym] = self.fetch_klines(sym, count, timeframe)
            except Exception as e:
                print(f"[WARN] 获取 {sym} 失败: {e}")
        return result