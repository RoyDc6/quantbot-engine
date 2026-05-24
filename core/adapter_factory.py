# -*- coding: utf-8 -*-
"""
core/adapter_factory.py — QuantBot 数据适配器工厂

根据标的 ticker 从 UniverseManager 查询适配器类型，返回对应适配器实例。
适配器实例按类型缓存（单例），避免重复创建连接。

架构定位：底层基建层，FusionController 的唯一数据源入口。

用法:
    adapter = AdapterFactory.get_adapter('00700.HK')
    adapter = AdapterFactory.get_adapter('BTC.USDT')
    df = adapter.fetch_kline(symbol, count=252)
"""

from typing import Dict, Optional, Type
from pathlib import Path
import sys

# 路径注入
BASE = Path(__file__).resolve().parent.parent  # E:\quant
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from core.base_adapter import BaseAdapter, NullAdapter
from core.universe_manager import UniverseManager


class AdapterFactory:
    """
    适配器工厂 — 注册表 + 缓存。
    线程安全：适配器实例是只读的，创建后不会被修改。
    """

    # 适配器类注册表（类名 → 类）
    _registry: Dict[str, Type[BaseAdapter]] = {}

    # 适配器实例缓存（类名 → 实例）
    _instances: Dict[str, BaseAdapter] = {}

    # UniverseManager 懒加载
    _universe: Optional[UniverseManager] = None

    # ─── 注册 ────────────────────────────────────────────────

    @classmethod
    def register(cls, adapter_type: str, adapter_class: Type[BaseAdapter]):
        """
        注册适配器类。

        Args:
            adapter_type: 适配器类型名（如 'FutuAdapter', 'CryptoAdapter'）
            adapter_class: 实现 BaseAdapter 的类
        """
        if not issubclass(adapter_class, BaseAdapter):
            raise TypeError(f'{adapter_class.__name__} 必须继承 BaseAdapter')
        cls._registry[adapter_type] = adapter_class
        # 清除已缓存的实例（强制下次重新创建）
        cls._instances.pop(adapter_type, None)

    @classmethod
    def registered_types(cls) -> list:
        """返回所有已注册的适配器类型名。"""
        return list(cls._registry.keys())

    # ─── 获取适配器 ──────────────────────────────────────────

    @classmethod
    def get_adapter(cls, ticker: str) -> BaseAdapter:
        """
        根据 ticker 获取对应的数据适配器。

        流程:
            1. 从 UniverseManager 查询 ticker 的 adapter_type
            2. 从注册表获取对应类
            3. 缓存并返回适配器实例

        Args:
            ticker: 标准符号 '00700.HK' / 'BTC.USDT'

        Returns:
            BaseAdapter 实例（未找到返回 NullAdapter）
        """
        adapter_type = cls._get_adapter_type(ticker)
        if not adapter_type:
            return NullAdapter()

        # 缓存命中
        if adapter_type in cls._instances:
            return cls._instances[adapter_type]

        # 创建新实例
        adapter_class = cls._registry.get(adapter_type)
        if not adapter_class:
            return NullAdapter()

        try:
            instance = adapter_class()
            cls._instances[adapter_type] = instance
            return instance
        except Exception:
            return NullAdapter()

    @classmethod
    def get_all_adapters(cls) -> Dict[str, BaseAdapter]:
        """
        返回所有已实例化的适配器 {类型名: 实例}。

        不自动创建未使用的适配器，只返回已缓存实例。
        """
        return dict(cls._instances)

    @classmethod
    def get_adapter_by_type(cls, adapter_type: str) -> BaseAdapter:
        """
        按类型名获取适配器（不依赖 ticker）。
        用于 VIX / 全局数据等非标的查询。

        Args:
            adapter_type: 'FutuAdapter' / 'CryptoAdapter'

        Returns:
            BaseAdapter 实例
        """
        if adapter_type in cls._instances:
            return cls._instances[adapter_type]

        adapter_class = cls._registry.get(adapter_type)
        if not adapter_class:
            return NullAdapter()

        try:
            instance = adapter_class()
            cls._instances[adapter_type] = instance
            return instance
        except Exception:
            return NullAdapter()

    # ─── 全局状态 ────────────────────────────────────────────

    @classmethod
    def status(cls) -> dict:
        """所有适配器的状态报告。"""
        result = {}
        # 已注册的类型
        for atype in cls._registry:
            inst = cls._instances.get(atype)
            available = inst.available if inst else False
            result[atype] = {
                'registered': True,
                'instantiated': inst is not None,
                'available': bool(available),
            }
        return result

    # ─── 内部 ────────────────────────────────────────────────

    @classmethod
    def _get_adapter_type(cls, ticker: str) -> Optional[str]:
        """从 UniverseManager 查询 ticker 的适配器类型。"""
        if cls._universe is None:
            cls._universe = UniverseManager()
        return cls._universe.get_adapter_type(ticker)

    @classmethod
    def reset(cls):
        """重置工厂状态（测试用）。"""
        cls._registry.clear()
        cls._instances.clear()
        cls._universe = None

    @classmethod
    def ensure_registered(cls):
        """确保已知适配器已注册。幂等操作。"""
        if 'FutuAdapter' in cls._registry:
            return  # 已注册，跳过
        # 尝试导入并注册已知适配器
        _try_register(cls, 'FutuAdapter', 'core.futu_adapter', 'FutuAdapter')
        _try_register(cls, 'CryptoAdapter', 'core.crypto_adapter', 'CryptoAdapter')


def _try_register(factory_cls, name: str, module_path: str, class_name: str):
    """尝试导入并注册适配器（失败时静默跳过）。"""
    try:
        import importlib
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name, None)
        if cls:
            factory_cls.register(name, cls)
    except Exception:
        pass  # 适配器不可用时不抛异常


# 模块加载时自动注册已知适配器
AdapterFactory.ensure_registered()