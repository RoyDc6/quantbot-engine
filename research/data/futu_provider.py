"""
research/data/futu_provider.py — Futu OpenD 数据提供者

通过 Futu OpenD API 获取港股/美股 K 线数据，返回统一的 KLineData 格式。
处理 subscribe + get_cur_kline 的同一连接要求。
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import List, Optional, Dict, Any

import pandas as pd

from .base import KLineData, UnifiedDataProvider
from .symbol_registry import SymbolRegistry


class FutuProvider(UnifiedDataProvider):
    """Futu OpenD 数据提供者 — 只读数据获取

    连接本地 Futu OpenD (127.0.0.1:11111) 获取港股/美股 K 线。
    每次 fetch_klines 打开独立连接，处理 subscribe + get_cur_kline 的同一连接要求。
    """

    # Futu 市场代码映射
    MARKET_PREFIX = {
        "HK": "HK.",
        "US": "US.",
    }

    # Futu 时间周期映射
    TIMEFRAME_MAP = {
        "1m": "K_1M",
        "5m": "K_5M",
        "15m": "K_15M",
        "30m": "K_30M",
        "60m": "K_60M",
        "1d": "K_DAY",
        "1w": "K_WEEK",
        "1M": "K_MONTH",
    }

    def __init__(self, host: str = "127.0.0.1", port: int = 11111,
                 symbol_registry: Optional[SymbolRegistry] = None):
        self.host = host
        self.port = port
        self.registry = symbol_registry or SymbolRegistry()

    def _get_futu_code(self, symbol: str, market: str) -> str:
        """获取完整的 Futu 代码

        优先使用注册表中的 futu_code，否则按市场前缀构造。
        """
        info = self.registry.get_symbol(symbol)
        if info and info.futu_code:
            return info.futu_code

        prefix = self.MARKET_PREFIX.get(market, "")
        return f"{prefix}{symbol}"

    def fetch_klines(self, symbol: str, count: int = 200,
                     timeframe: str = "1d") -> KLineData:
        """获取 Futu K 线并转为统一格式

        注意: Futu 需要先 subscribe 再 get_cur_kline，且必须在同一连接内。
        """
        # 确定市场
        info = self.registry.get_symbol(symbol)
        market = info.market if info else "US"

        futu_code = self._get_futu_code(symbol, market)
        kl_type = self.TIMEFRAME_MAP.get(timeframe, "K_DAY")

        # 延迟导入 futu SDK（避免模块加载时依赖）
        from futu import OpenQuoteContext, SubType, KLType, AuType, RET_OK

        ctx = OpenQuoteContext(host=self.host, port=self.port)
        try:
            # 1. 订阅
            ret, _ = ctx.subscribe([futu_code], [SubType.K_DAY], subscribe_push=False)
            if ret != RET_OK:
                raise RuntimeError(f"Futu subscribe 失败: {futu_code}")

            # 2. 获取 K 线（get_cur_kline 返回从旧到新排列）
            ret, data = ctx.get_cur_kline(futu_code, count, KLType.K_DAY, AuType.QFQ)
            if ret != RET_OK:
                raise RuntimeError(f"Futu get_cur_kline 失败: {futu_code}: {data}")

            # 3. 转为统一格式
            # Futu K 线列: code, name, time_key, open, close, high, low, volume, turnover, pe_ratio, turnover_rate, last_close
            records = []
            for _, row in data.iterrows():
                records.append({
                    "date": pd.Timestamp(row["time_key"]),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                })

            df = pd.DataFrame(records)
            if df.empty:
                raise ValueError(f"FutuProvider: {futu_code} 无 K 线数据")

            # 确保按时间排序
            df = df.sort_values("date").reset_index(drop=True)

            return KLineData(
                symbol=symbol,
                market=market,
                timeframe=timeframe,
                df=df,
                provider="futu"
            )

        finally:
            ctx.close()

    def fetch_multiple(self, symbols: List[str], count: int = 200,
                       timeframe: str = "1d") -> dict[str, KLineData]:
        """批量获取多个标的 K 线

        优化: 同一市场的标的合并订阅，减少连接次数。
        """
        # 按市场分组
        by_market: Dict[str, List[str]] = {}
        for sym in symbols:
            info = self.registry.get_symbol(sym)
            market = info.market if info else "US"
            by_market.setdefault(market, []).append(sym)

        result = {}
        for market, syms in by_market.items():
            # 每个市场打开一个连接，批量订阅
            futu_codes = [self._get_futu_code(s, market) for s in syms]

            from futu import OpenQuoteContext, SubType, KLType, AuType, RET_OK

            ctx = OpenQuoteContext(host=self.host, port=self.port)
            try:
                # 批量订阅
                ret, _ = ctx.subscribe(futu_codes, [SubType.K_DAY], subscribe_push=False)
                if ret != RET_OK:
                    print(f"[WARN] Futu 批量订阅失败 ({market}): {ret}")
                    # 逐个尝试
                    for sym, fc in zip(syms, futu_codes):
                        try:
                            result[sym] = self.fetch_klines(sym, count, timeframe)
                        except Exception as e:
                            print(f"[WARN] 获取 {sym} 失败: {e}")
                    continue

                # 逐个获取
                for sym, fc in zip(syms, futu_codes):
                    try:
                        ret, data = ctx.get_cur_kline(fc, count, KLType.K_DAY, AuType.QFQ)
                        if ret != RET_OK:
                            print(f"[WARN] {sym}: get_cur_kline 失败: {data}")
                            continue

                        records = []
                        for _, row in data.iterrows():
                            records.append({
                                "date": pd.Timestamp(row["time_key"]),
                                "open": float(row["open"]),
                                "high": float(row["high"]),
                                "low": float(row["low"]),
                                "close": float(row["close"]),
                                "volume": float(row["volume"]),
                            })

                        df = pd.DataFrame(records).sort_values("date").reset_index(drop=True)
                        result[sym] = KLineData(
                            symbol=sym, market=market,
                            timeframe=timeframe, df=df, provider="futu"
                        )
                    except Exception as e:
                        print(f"[WARN] 获取 {sym} 失败: {e}")

            finally:
                ctx.close()

        return result

    def list_available_symbols(self, market: str = "US") -> List[str]:
        """列出某市场可用标的"""
        syms = self.registry.get_symbols(market)
        return [s.symbol for s in syms if s.futu_code]