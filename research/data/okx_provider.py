"""
research/data/okx_provider.py — OKX API 数据提供者

包装 OKX API 获取加密货币 K 线，返回统一的 KLineData 格式。
直接使用 OKX REST API v5，不依赖现有 OKX 引擎的执行层代码。
"""

from __future__ import annotations

import hashlib
import hmac
import base64
import json
import time
from datetime import datetime
from typing import List, Optional, Dict, Any

import pandas as pd
import requests

from .base import KLineData, UnifiedDataProvider
from .symbol_registry import SymbolRegistry


class OKXProvider(UnifiedDataProvider):
    """OKX 数据提供者 — 只读数据获取

    直接调用 OKX REST API v5，不依赖 OKX 引擎的执行层代码。
    """

    BASE_URL = "https://www.okx.com"

    TIMEFRAME_MAP = {
        "1m": "1m", "5m": "5m", "15m": "15m",
        "30m": "30m", "1h": "1H", "4h": "4H",
        "1d": "1D", "1w": "1W", "1M": "1M",
    }

    def __init__(self, api_key: str = "", secret_key: str = "",
                 passphrase: str = "",
                 symbol_registry: Optional[SymbolRegistry] = None):
        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase
        self.registry = symbol_registry or SymbolRegistry()
        self._session = requests.Session()

    def _sign_request(self, method: str, path: str, body: dict = None) -> dict:
        """生成 OKX API v5 签名"""
        timestamp = str(time.time())
        body_str = json.dumps(body) if body else ""
        msg = f"{timestamp}{method}{path}{body_str}"
        mac = hmac.new(
            self.secret_key.encode('utf-8'),
            msg.encode('utf-8'),
            hashlib.sha256
        )
        sign = base64.b64encode(mac.digest()).decode('utf-8')

        return {
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": sign,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
        }

    def _get(self, path: str, params: dict = None) -> Dict[str, Any]:
        """发送 GET 请求到 OKX API"""
        url = f"{self.BASE_URL}{path}"
        headers = {"Content-Type": "application/json"}

        if self.api_key and self.secret_key:
            # 需要签名的请求
            qs = "&".join(f"{k}={v}" for k, v in (params or {}).items())
            full_path = f"{path}?{qs}" if qs else path
            headers.update(self._sign_request("GET", full_path))

        resp = self._session.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != "0":
            raise ValueError(f"OKX API 错误: {data.get('msg', 'unknown')}")

        return data

    def _to_inst_id(self, symbol: str) -> str:
        """将简写代码转为 OKX instrument ID"""
        info = self.registry.get_symbol(symbol)
        if info and info.okx_inst_id:
            return info.okx_inst_id
        # 默认构造
        return f"{symbol}-SWAP"

    def fetch_klines(self, symbol: str, count: int = 500,
                     timeframe: str = "1d") -> KLineData:
        """获取 OKX K 线并转为统一格式"""
        inst_id = self._to_inst_id(symbol)
        bar = self.TIMEFRAME_MAP.get(timeframe, "1D")

        params = {
            "instId": inst_id,
            "bar": bar,
            "limit": min(count, 300),  # OKX 最大 300
        }

        data = self._get("/api/v5/market/candles", params)

        # OKX 返回: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
        records = []
        for candle in data.get("data", []):
            records.append({
                "date": datetime.fromtimestamp(int(candle[0]) / 1000),
                "open": float(candle[1]),
                "high": float(candle[2]),
                "low": float(candle[3]),
                "close": float(candle[4]),
                "volume": float(candle[5]),
            })

        df = pd.DataFrame(records)
        if df.empty:
            raise ValueError(f"OKXProvider: {inst_id} 无 K 线数据")

        # 需要更多数据则分批获取
        if count > 300:
            all_dfs = [df]
            while len(df) < count:
                before = df['date'].iloc[0]
                params['before'] = str(int(before.timestamp() * 1000))
                more = self._get("/api/v5/market/candles", params)
                more_records = []
                for candle in more.get("data", []):
                    more_records.append({
                        "date": datetime.fromtimestamp(int(candle[0]) / 1000),
                        "open": float(candle[1]),
                        "high": float(candle[2]),
                        "low": float(candle[3]),
                        "close": float(candle[4]),
                        "volume": float(candle[5]),
                    })
                if not more_records:
                    break
                df_more = pd.DataFrame(more_records)
                all_dfs.append(df_more)
                df = pd.concat(all_dfs, ignore_index=True)
                if len(more_records) < 300:
                    break

        # 按时间排序
        df = df.sort_values('date').reset_index(drop=True)

        return KLineData(
            symbol=symbol,
            market="CRYPTO",
            timeframe=timeframe,
            df=df,
            provider="okx"
        )

    def list_available_symbols(self, market: str = "CRYPTO") -> List[str]:
        """列出加密货币标的"""
        syms = self.registry.get_symbols("CRYPTO")
        return [s.symbol for s in syms]

    def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        """获取最新 ticker（扩展功能）"""
        inst_id = self._to_inst_id(symbol)
        data = self._get("/api/v5/market/ticker", {"instId": inst_id})
        return data.get("data", [{}])[0]

    def fetch_orderbook(self, symbol: str, depth: int = 5) -> Dict[str, Any]:
        """获取订单簿（扩展功能）"""
        inst_id = self._to_inst_id(symbol)
        data = self._get("/api/v5/market/books", {"instId": inst_id, "sz": str(depth)})
        return data.get("data", [{}])[0]