"""
research/factors/base.py — 因子抽象基类

所有因子继承 BaseFactor，实现 compute() 方法。
通过 FactorMeta 声明元数据，由 FactorRegistry 统一管理。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

import pandas as pd

from ..data.base import KLineData


class BaseFactor(ABC):
    """因子抽象基类

    子类需实现:
    - meta() 类方法: 返回因子元数据
    - compute() 方法: 输入 KLineData，输出 pd.Series
    """

    @classmethod
    @abstractmethod
    def meta(cls) -> "FactorMeta":
        """因子元数据"""
        ...

    @abstractmethod
    def compute(self, data: KLineData, **params) -> pd.Series:
        """计算因子值

        Args:
            data: 统一 K 线数据
            params: 因子特定参数（覆盖 meta 中的默认值）

        Returns:
            与 data.df 等长的 pd.Series，index 与 data.df 一致
        """
        ...

    def validate(self, data: KLineData) -> bool:
        """验证数据是否满足因子计算要求"""
        if data.df is None or data.df.empty:
            return False
        if len(data.df) < 30:  # 最少需要 30 根 K 线
            return False
        return True