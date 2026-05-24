"""
research/data/cache.py — K 线本地缓存管理

提供内存 + CSV 两级缓存，避免重复获取相同数据。
研究环境下频繁迭代因子时减少 API 调用。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from .base import KLineData


class KLineCache:
    """K 线缓存 — 内存优先，CSV 持久化

    缓存键: {market}:{symbol}:{timeframe}
    """

    def __init__(self, cache_dir: Optional[str] = None, max_age_days: int = 1):
        self._memory: Dict[str, KLineData] = {}
        self.cache_dir = Path(cache_dir or
                              Path(__file__).resolve().parent / "_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_age_days = max_age_days

    def _key(self, symbol: str, market: str, timeframe: str) -> str:
        return f"{market}:{symbol}:{timeframe}"

    def _csv_path(self, key: str) -> Path:
        # 文件名转义冒号
        safe = key.replace(":", "_")
        return self.cache_dir / f"{safe}.csv"

    def get(self, symbol: str, market: str, timeframe: str) -> Optional[KLineData]:
        """从缓存获取（内存 → CSV）"""
        key = self._key(symbol, market, timeframe)

        # 内存缓存
        if key in self._memory:
            return self._memory[key]

        # CSV 缓存
        csv_path = self._csv_path(key)
        if csv_path.exists():
            # 检查文件年龄
            age = pd.Timestamp.now() - pd.Timestamp.fromtimestamp(
                os.path.getmtime(csv_path))
            if age.days < self.max_age_days:
                df = pd.read_csv(csv_path, parse_dates=['date'])
                kdata = KLineData(
                    symbol=symbol, market=market,
                    timeframe=timeframe, df=df, provider="cache"
                )
                self._memory[key] = kdata
                return kdata

        return None

    def set(self, data: KLineData):
        """写入缓存（内存 + CSV）"""
        key = self._key(data.symbol, data.market, data.timeframe)
        self._memory[key] = data

        # 写 CSV
        csv_path = self._csv_path(key)
        data.df.to_csv(csv_path, index=False)

    def invalidate(self, symbol: str, market: str, timeframe: str):
        """清除特定缓存"""
        key = self._key(symbol, market, timeframe)
        self._memory.pop(key, None)
        csv_path = self._csv_path(key)
        if csv_path.exists():
            csv_path.unlink()

    def clear_all(self):
        """清空所有缓存"""
        self._memory.clear()
        for f in self.cache_dir.glob("*.csv"):
            f.unlink()

    @property
    def size(self) -> int:
        return len(self._memory)