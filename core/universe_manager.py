# -*- coding: utf-8 -*-
"""
core/universe_manager.py - QuantBot 资产管理器

单一真相源：从 config/universe_config.yaml 加载所有标的定义。
config.py 的 UNIVERSE_HK/UNIVERSE_US 作为向后兼容的降级方案。

架构定位：底层基建层，AdapterFactory 和 FusionController 的依赖。
"""

from typing import List, Optional, Dict, Any
from pathlib import Path
import sys

# 路径注入
BASE = Path(__file__).resolve().parent.parent  # E:\quant
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))


# YAML 加载
YAML_PATH = BASE / 'config' / 'universe_config.yaml'
YAML_AVAILABLE = False
_ASSETS = []  # 全局资产列表缓存

try:
    import yaml
    if YAML_PATH.exists():
        with open(YAML_PATH, 'r', encoding='utf-8') as f:
            raw = yaml.safe_load(f)
        if raw and isinstance(raw, dict) and 'assets' in raw:
            _ASSETS = raw['assets']
            YAML_AVAILABLE = True
except Exception:
    pass


class UniverseManager:
    """
    资产管理器 — 统一标的查询入口。

    数据源优先级:
        1. config/universe_config.yaml（主数据源，推荐）
        2. config.py 的 UNIVERSE_HK / UNIVERSE_US（降级方案）

    用法:
        um = UniverseManager()
        symbols = um.get_hk_symbols()
        info = um.get_info('00700.HK')
        adapter_type = um.get_adapter_type('BTC.USDT')
    """

    def __init__(self, config: dict = None):
        """
        Args:
            config: 可选覆盖配置，格式同 UniverseManager 内部结构
                    {'assets': [{'ticker': '00700.HK', ...}]}
        """
        self._assets: List[Dict] = []

        if config and 'assets' in config:
            # 显式传入的配置优先
            self._assets = config['assets']
        elif YAML_AVAILABLE:
            self._assets = list(_ASSETS)
        else:
            # 降级: 从 config.py 加载
            self._assets = self._legacy_load()

        # 构建索引
        self._by_ticker: Dict[str, Dict] = {}
        for asset in self._assets:
            if asset.get('enabled', True):
                ticker = asset['ticker']
                self._by_ticker[ticker] = asset

    # ─── 符号列表 ────────────────────────────────────────────

    def get_hk_symbols(self) -> List[str]:
        """获取港股标准符号列表（.HK 后缀）。"""
        return [a['ticker'] for a in self._by_ticker.values()
                if self._get_market(a) == 'HK']

    def get_us_symbols(self) -> List[str]:
        """获取美股标准符号列表（.US 后缀）。"""
        return [a['ticker'] for a in self._by_ticker.values()
                if self._get_market(a) == 'US']

    def get_crypto_symbols(self) -> List[str]:
        """获取加密货币符号列表（.USDT / .USDC 后缀）。"""
        return [a['ticker'] for a in self._by_ticker.values()
                if self._get_market(a) == 'Crypto']

    def get_all_symbols(self) -> List[str]:
        """获取全市场标准符号列表。"""
        return list(self._by_ticker.keys())

    def get_symbols_by_market(self, market: str) -> List[str]:
        """按市场获取符号列表。HK / US / Crypto"""
        market_upper = market.upper()
        if market_upper == 'HK':
            return self.get_hk_symbols()
        elif market_upper == 'US':
            return self.get_us_symbols()
        elif market_upper in ('CRYPTO', 'CRYPTO'):
            return self.get_crypto_symbols()
        return []

    # ─── 元数据 ────────────────────────────────────────────

    def get_info(self, symbol: str) -> Optional[Dict]:
        """获取标的元数据（含 market, name, adapter 等）。"""
        asset = self._by_ticker.get(symbol)
        if asset:
            info = dict(asset.get('params', {}))
            info['name'] = asset.get('name', symbol)
            info['market'] = self._get_market(asset)
            info['adapter'] = asset.get('adapter', 'FutuAdapter')
            info['ticker'] = symbol
            info['enabled'] = asset.get('enabled', True)
            return info
        return None

    def get_name(self, symbol: str) -> str:
        """获取标的中文/英文名称。"""
        info = self.get_info(symbol)
        return info.get('name', symbol) if info else symbol

    def get_lot_size(self, symbol: str) -> int:
        """获取每手股数（港股按 lot_size，美股默认 1，Crypto 默认 0.01）。"""
        info = self.get_info(symbol)
        if info:
            market = info.get('market', 'HK')
            if market == 'HK':
                return info.get('lot_size', 100)
            elif market == 'Crypto':
                return info.get('lot_size', 1)  # 1 单位
            return 1
        return 100

    def get_market(self, symbol: str) -> str:
        """获取标的市场代码。HK / US / Crypto"""
        info = self.get_info(symbol)
        return info.get('market', 'HK') if info else 'HK'

    def get_adapter_type(self, symbol: str) -> Optional[str]:
        """获取标的的适配器类型名。"""
        info = self.get_info(symbol)
        return info.get('adapter') if info else None

    # ─── 格式转换 ────────────────────────────────────────────

    @staticmethod
    def to_futu_code(symbol: str) -> str:
        """标准符号 → Futu 代码。00700.HK → HK.00700"""
        parts = symbol.split('.')
        if len(parts) == 2:
            return f'{parts[1]}.{parts[0]}'
        return symbol

    @staticmethod
    def to_standard_symbol(futu_code: str) -> str:
        """Futu 代码 → 标准符号。HK.00700 → 00700.HK"""
        parts = futu_code.split('.')
        if len(parts) == 2:
            return f'{parts[1]}.{parts[0]}'
        return futu_code

    @staticmethod
    def to_inst_id(symbol: str) -> Optional[str]:
        """标准符号 → OKX instId。BTC.USDT → BTC-USDT"""
        for suffix in ['.USDT', '.USDC', '.USD']:
            if symbol.endswith(suffix):
                base = symbol[:-len(suffix)]
                return f'{base}-{suffix[1:]}'
        return None

    # ─── 统计 ────────────────────────────────────────────────

    def contains(self, symbol: str) -> bool:
        """判断 symbol 是否在资产池中。"""
        return symbol in self._by_ticker

    @property
    def hk_count(self) -> int:
        return len(self.get_hk_symbols())

    @property
    def us_count(self) -> int:
        return len(self.get_us_symbols())

    @property
    def crypto_count(self) -> int:
        return len(self.get_crypto_symbols())

    @property
    def total_count(self) -> int:
        return len(self._by_ticker)

    # ─── 内部工具 ────────────────────────────────────────────

    @staticmethod
    def _get_market(asset: Dict) -> str:
        """从 asset 的 ticker 或 params 推导市场。"""
        # 先看 params.market（最准确）
        params = asset.get('params', {})
        if 'market' in params:
            return params['market']

        # 再从 ticker 后缀推导
        ticker = asset.get('ticker', '')
        if ticker.endswith('.HK'):
            return 'HK'
        elif ticker.endswith('.US'):
            return 'US'
        elif ticker.endswith(('.USDT', '.USDC', '.USD')):
            return 'Crypto'
        return 'HK'

    @staticmethod
    def _legacy_load() -> List[Dict]:
        """从 config.py 加载（降级方案）。"""
        assets = []
        try:
            from config import UNIVERSE_HK, UNIVERSE_US
            for sym, info in UNIVERSE_HK.items():
                assets.append({
                    'ticker': sym,
                    'name': info.get('name', sym),
                    'adapter': 'FutuAdapter',
                    'params': {'market': 'HK', 'lot_size': info.get('lot_size', 100)},
                    'enabled': True,
                })
            for raw_sym, info in UNIVERSE_US.items():
                # 转换 US.AAPL → AAPL.US
                if raw_sym.startswith('US.'):
                    std_sym = raw_sym[3:] + '.US'
                else:
                    std_sym = raw_sym
                assets.append({
                    'ticker': std_sym,
                    'name': info.get('name', std_sym),
                    'adapter': 'FutuAdapter',
                    'params': {'market': 'US'},
                    'enabled': True,
                })
        except Exception:
            pass
        return assets