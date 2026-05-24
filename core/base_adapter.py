# -*- coding: utf-8 -*-
"""
core/base_adapter.py — QuantBot 数据适配器标准接口

所有数据源（Futu / OKX / TickFlow / 未来其他）必须实现此接口。
FusionController 通过 AdapterFactory 获取适配器，不直接依赖具体实现。

架构定位：底层基建层，AdapterFactory 的依赖
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, List, Tuple

import pandas as pd


class BaseAdapter(ABC):
    """
    数据适配器基类 — 统一的 K 线 + 报价接口。

    核心契约:
        fetch_kline(symbol, count, ktype) -> Optional[pd.DataFrame]
            DataFrame 列: [date, open, high, low, close, volume]
            date 为字符串 'YYYY-MM-DD' 格式
            返回 None 表示数据不可用

        available() -> bool
            适配器是否能正常工作

    扩展方法（可选实现）:
        fetch_quote(symbol) -> Dict — 实时报价
        test_connection() -> Tuple[bool, str] — 连接测试
    """

    @abstractmethod
    def fetch_kline(self, symbol: str, count: int = 252,
                    ktype: str = 'K_DAY', **kwargs) -> Optional[pd.DataFrame]:
        """
        获取单个标的 K 线数据。

        Args:
            symbol: 标准符号 '00700.HK' / 'BTC.USDT'
            count: K 线根数
            ktype: 'K_DAY' / 'K_WEEK' / 'K_MON' / 'K_1M' / 'K_5M' / 'K_15M'
            **kwargs: 适配器特定参数

        Returns:
            pd.DataFrame | None: [date, open, high, low, close, volume]
        """
        ...

    @abstractmethod
    def available(self) -> bool:
        """适配器是否可用。"""
        ...

    def fetch_quote(self, symbol: str) -> Optional[Dict]:
        """
        获取单个标的实时报价。

        Returns:
            {price, high, low, open, volume, ...} | None
        """
        return None

    def fetch_quotes(self, symbols: List[str]) -> Dict[str, Dict]:
        """
        批量获取实时报价。

        Returns:
            {symbol: {price, high, low, open, volume, name}}
        """
        result = {}
        for sym in symbols:
            q = self.fetch_quote(sym)
            if q:
                result[sym] = q
        return result

    def test_connection(self) -> Tuple[bool, str]:
        """测试适配器连接。"""
        return (self.available(), 'OK' if self.available() else '不可用')


class NullAdapter(BaseAdapter):
    """
    空适配器 — 当请求的适配器类型未注册或不可用时使用。
    所有方法返回 None / 空，永不抛异常。
    """

    def fetch_kline(self, symbol: str, count: int = 252,
                    ktype: str = 'K_DAY', **kwargs) -> Optional[pd.DataFrame]:
        return None

    @property
    def available(self) -> bool:
        return False

    def fetch_quote(self, symbol: str) -> Optional[Dict]:
        return None

    def test_connection(self) -> Tuple[bool, str]:
        return (False, 'NullAdapter: 未注册的适配器类型')