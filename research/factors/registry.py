"""
research/factors/registry.py — 因子注册系统

中心化的因子管理：
- FactorMeta: 因子元数据定义
- FactorRegistry: 因子注册、查询、计算调度

新增因子只需:
1. 创建因子类继承 BaseFactor
2. 实现 meta() 和 compute()
3. 调用 FactorRegistry.register(MyFactor)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Type, TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from .base import BaseFactor
    from ..data.base import KLineData


@dataclass
class FactorMeta:
    """因子元数据"""
    name: str                       # 因子唯一标识名，如 "rsi_14"
    category: str                   # "technical" | "fundamental" | "llm" | "alternative"
    markets: List[str]              # 适用市场 ["US", "HK", "CN", "CRYPTO"]
    frequencies: List[str]          # 适用频率 ["1d", "1h", "5m"]
    description: str                # 因子描述
    default_params: dict = field(default_factory=dict)  # 默认参数
    version: str = "1.0.0"          # 版本号


class FactorRegistry:
    """因子注册表 — 单例模式"""

    _registry: Dict[str, Type[BaseFactor]] = {}
    _metas: Dict[str, FactorMeta] = {}

    @classmethod
    def register(cls, factor_cls: Type[BaseFactor]):
        """注册一个因子类"""
        meta = factor_cls.meta()
        cls._registry[meta.name] = factor_cls
        cls._metas[meta.name] = meta

    @classmethod
    def list_factors(cls, market: Optional[str] = None,
                     category: Optional[str] = None) -> List[FactorMeta]:
        """按条件列出因子

        Args:
            market: 过滤市场
            category: 过滤类别

        Returns:
            匹配的因子元数据列表
        """
        results = []
        for name, meta in cls._metas.items():
            if market and market not in meta.markets:
                continue
            if category and category != meta.category:
                continue
            results.append(meta)
        return results

    @classmethod
    def get_factor(cls, name: str) -> Optional[Type[BaseFactor]]:
        """按名称获取因子类"""
        return cls._registry.get(name)

    @classmethod
    def compute(cls, name: str, data: KLineData,
                **params) -> Optional[pd.Series]:
        """计算指定因子

        Args:
            name: 因子名
            data: K 线数据
            params: 覆盖默认参数

        Returns:
            因子值序列，或 None（因子不存在/计算失败）
        """
        factor_cls = cls._registry.get(name)
        if factor_cls is None:
            print(f"[FactorRegistry] 因子 '{name}' 未注册")
            return None

        factor = factor_cls()
        if not factor.validate(data):
            print(f"[FactorRegistry] 数据不满足 '{name}' 计算要求")
            return None

        try:
            return factor.compute(data, **params)
        except Exception as e:
            print(f"[FactorRegistry] 计算 '{name}' 失败: {e}")
            return None

    @classmethod
    def compute_batch(cls, names: List[str], data: KLineData,
                      **params) -> Dict[str, Optional[pd.Series]]:
        """批量计算多个因子"""
        return {n: cls.compute(n, data, **params) for n in names}

    @classmethod
    def compute_all(cls, data: KLineData,
                    market: Optional[str] = None,
                    category: Optional[str] = None,
                    **params) -> Dict[str, Optional[pd.Series]]:
        """计算所有匹配条件的因子"""
        metas = cls.list_factors(market, category)
        names = [m.name for m in metas]
        return cls.compute_batch(names, data, **params)

    @classmethod
    def count(cls) -> int:
        return len(cls._registry)