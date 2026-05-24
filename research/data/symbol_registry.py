"""
research/data/symbol_registry.py — 四市场符号注册表

集中管理各市场的标的代码，可按市场/类型查询。
避免因子研究代码中硬编码符号列表。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class SymbolInfo:
    """标的元信息"""
    symbol: str         # 交易代码（如 "00700", "SPY", "BTC-USDT"）
    market: str         # "US" | "HK" | "CN" | "CRYPTO"
    name: str           # 中文/英文名称
    asset_type: str     # "stock" | "etf" | "crypto" | "index"
    futu_code: str = "" # Futu 代码（如 "HK.00700"）
    okx_inst_id: str = ""  # OKX 合约ID（如 "BTC-USDT-SWAP"）


# ============================================================
# 默认符号注册表 — 可按需扩展
# ============================================================

DEFAULT_SYMBOLS: List[SymbolInfo] = [
    # --- 港股 ---
    SymbolInfo("00700", "HK", "腾讯控股", "stock", "HK.00700"),
    SymbolInfo("09988", "HK", "阿里巴巴", "stock", "HK.09988"),
    SymbolInfo("01810", "HK", "小米集团", "stock", "HK.01810"),
    SymbolInfo("03690", "HK", "美团", "stock", "HK.03690"),
    SymbolInfo("09618", "HK", "京东集团", "stock", "HK.09618"),
    SymbolInfo("02318", "HK", "中国平安", "stock", "HK.02318"),
    SymbolInfo("00005", "HK", "汇丰控股", "stock", "HK.00005"),
    SymbolInfo("00941", "HK", "中国移动", "stock", "HK.00941"),
    SymbolInfo("00388", "HK", "香港交易所", "stock", "HK.00388"),
    SymbolInfo("01024", "HK", "快手", "stock", "HK.01024"),

    # --- 美股 ---
    SymbolInfo("SPY", "US", "SPDR S&P 500 ETF", "etf", "US.SPY"),
    SymbolInfo("QQQ", "US", "Invesco QQQ Trust", "etf", "US.QQQ"),
    SymbolInfo("VXX", "US", "iPath S&P 500 VIX ST Futures ETN", "etf", "US.VXX"),
    SymbolInfo("NVDA", "US", "NVIDIA", "stock", "US.NVDA"),
    SymbolInfo("TSLA", "US", "Tesla", "stock", "US.TSLA"),
    SymbolInfo("META", "US", "Meta Platforms", "stock", "US.META"),
    SymbolInfo("AMD",  "US", "AMD", "stock", "US.AMD"),
    SymbolInfo("AAPL", "US", "Apple", "stock", "US.AAPL"),
    SymbolInfo("MSFT", "US", "Microsoft", "stock", "US.MSFT"),
    SymbolInfo("AMZN", "US", "Amazon", "stock", "US.AMZN"),
    SymbolInfo("GOOGL","US", "Alphabet", "stock", "US.GOOGL"),

    # --- A 股 ---
    SymbolInfo("000001", "CN", "平安银行", "stock", "SZ.000001"),
    SymbolInfo("000333", "CN", "美的集团", "stock", "SZ.000333"),
    SymbolInfo("000858", "CN", "五粮液", "stock", "SZ.000858"),
    SymbolInfo("002415", "CN", "海康威视", "stock", "SZ.002415"),
    SymbolInfo("600519", "CN", "贵州茅台", "stock", "SH.600519"),
    SymbolInfo("600036", "CN", "招商银行", "stock", "SH.600036"),
    SymbolInfo("601318", "CN", "中国平安", "stock", "SH.601318"),
    SymbolInfo("600900", "CN", "长江电力", "stock", "SH.600900"),

    # --- 加密货币 ---
    SymbolInfo("BTC-USDT", "CRYPTO", "Bitcoin", "crypto", okx_inst_id="BTC-USDT-SWAP"),
    SymbolInfo("ETH-USDT", "CRYPTO", "Ethereum", "crypto", okx_inst_id="ETH-USDT-SWAP"),
    SymbolInfo("SOL-USDT", "CRYPTO", "Solana", "crypto", okx_inst_id="SOL-USDT-SWAP"),
    SymbolInfo("XRP-USDT", "CRYPTO", "XRP", "crypto", okx_inst_id="XRP-USDT-SWAP"),
    SymbolInfo("DOGE-USDT","CRYPTO", "Dogecoin", "crypto", okx_inst_id="DOGE-USDT-SWAP"),
    SymbolInfo("ADA-USDT", "CRYPTO", "Cardano", "crypto", okx_inst_id="ADA-USDT-SWAP"),
]


class SymbolRegistry:
    """符号注册表 — 集中管理所有市场的标的"""

    def __init__(self, symbols: Optional[List[SymbolInfo]] = None):
        self._symbols: List[SymbolInfo] = symbols or DEFAULT_SYMBOLS.copy()
        self._by_market: Dict[str, List[SymbolInfo]] = {}
        self._rebuild_index()

    def _rebuild_index(self):
        self._by_market = {}
        for s in self._symbols:
            self._by_market.setdefault(s.market, []).append(s)

    def list_markets(self) -> List[str]:
        return list(self._by_market.keys())

    def get_symbols(self, market: Optional[str] = None,
                    asset_type: Optional[str] = None) -> List[SymbolInfo]:
        """按市场和/或资产类型查询符号"""
        result = self._symbols
        if market:
            result = [s for s in result if s.market == market]
        if asset_type:
            result = [s for s in result if s.asset_type == asset_type]
        return result

    def get_symbol(self, symbol: str) -> Optional[SymbolInfo]:
        """通过交易代码查询单个符号"""
        for s in self._symbols:
            if s.symbol == symbol:
                return s
        return None

    def add_symbol(self, info: SymbolInfo):
        """动态添加符号"""
        self._symbols.append(info)
        self._rebuild_index()

    def futu_codes(self, market: Optional[str] = None) -> List[str]:
        """获取 Futu 代码列表（过滤掉无 futu_code 的）"""
        syms = self.get_symbols(market)
        return [s.futu_code for s in syms if s.futu_code]

    def okx_inst_ids(self) -> List[str]:
        """获取 OKX 合约ID列表"""
        syms = self.get_symbols("CRYPTO")
        return [s.okx_inst_id for s in syms if s.okx_inst_id]

    @property
    def all(self) -> List[SymbolInfo]:
        return self._symbols.copy()