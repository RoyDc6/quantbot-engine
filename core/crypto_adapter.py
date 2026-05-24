# -*- coding: utf-8 -*-
"""
core/crypto_adapter.py — QuantBot OKX Crypto 数据适配器

通过 OKX CLI 获取加密货币 K 线数据。
实现 BaseAdapter 接口，供 AdapterFactory / FusionController 调用。

数据源: OKX API v5（通过 @okx_ai/okx-trade-cli）
符号格式: BTC.USDT → instId BTC-USDT
"""

import subprocess
import sys
import re
from datetime import datetime, timezone
from typing import Optional, Dict, List, Tuple
from pathlib import Path

import pandas as pd

# 路径注入
BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from core.base_adapter import BaseAdapter


# K 线类型映射
KTYPE_MAP = {
    'K_1M': '1m',
    'K_5M': '5m',
    'K_15M': '15m',
    'K_30M': '30m',
    'K_1H': '1H',
    'K_4H': '4H',
    'K_DAY': '1D',
    'K_WEEK': '1W',
    'K_MON': '1M',
}


class CryptoAdapter(BaseAdapter):
    """
    OKX 加密货币适配器。

    使用 okx CLI 命令获取数据，无需 API Key（仅公共市场数据）。
    """

    def __init__(self, cli_path: str = None):
        """
        Args:
            cli_path: okx CLI 路径（默认自动查找）
        """
        self._cli = cli_path or 'okx'
        self._available = None  # 懒检测

    # ─── 核心接口 ────────────────────────────────────────────

    @property
    def available(self) -> bool:
        """检测 okx CLI 是否可用。"""
        if self._available is None:
            try:
                # okx 是 PowerShell 包装命令（okx.ps1），需 shell=True
                result = subprocess.run(
                    f'{self._cli} market ticker BTC-USDT',
                    capture_output=True, shell=True, text=True, timeout=10,
                    encoding='utf-8', errors='replace',
                )
                stdout = result.stdout or ''
                self._available = 'BTC-USDT' in stdout and 'last' in stdout
            except Exception:
                self._available = False
        return self._available

    def fetch_kline(self, symbol: str, count: int = 252,
                    ktype: str = 'K_DAY', **kwargs) -> Optional[pd.DataFrame]:
        """
        获取加密货币 K 线数据。

        Args:
            symbol: 标准符号 'BTC.USDT' / 'ETH.USDT'
            count: K 线根数（最大 300）
            ktype: 支持 K_1M / K_5M / K_15M / K_30M / K_1H / K_4H / K_DAY / K_WEEK / K_MON

        Returns:
            pd.DataFrame | None: [date, open, high, low, close, volume]
        """
        if not self.available:
            return None

        inst_id = self._to_inst_id(symbol)
        if not inst_id:
            return None

        bar = KTYPE_MAP.get(ktype, '1D')
        limit = min(count, 300)

        try:
            cmd = f'{self._cli} market candles {inst_id} --bar {bar} --limit {limit}'
            result = subprocess.run(cmd, capture_output=True, shell=True, text=True, timeout=30,
                                    encoding='utf-8', errors='replace')

            output = result.stdout or ''
            if not output.strip():
                return None

            df = self._parse_table_output(output)
            if df is not None:
                df.attrs['source'] = 'OKX'
            return df

        except subprocess.TimeoutExpired:
            return None
        except Exception:
            return None

    # ─── 报价 ────────────────────────────────────────────────

    def fetch_quote(self, symbol: str) -> Optional[Dict]:
        """获取实时报价。"""
        if not self.available:
            return None

        inst_id = self._to_inst_id(symbol)
        if not inst_id:
            return None

        try:
            cmd = f'{self._cli} market ticker {inst_id}'
            result = subprocess.run(cmd, capture_output=True, shell=True, text=True, timeout=10,
                                    encoding='utf-8', errors='replace')
            output = result.stdout or ''
            if not output.strip():
                return None
            return self._parse_ticker_output(output)

        except Exception:
            return None

    # ─── 内部工具 ────────────────────────────────────────────

    @staticmethod
    def _to_inst_id(symbol: str) -> Optional[str]:
        """标准符号 → OKX instId。BTC.USDT → BTC-USDT"""
        # 移除市场后缀（.USDT / .USDC / .USD）
        for suffix in ['.USDT', '.USDC', '.USD']:
            if symbol.endswith(suffix):
                base = symbol[:-len(suffix)]
                return f'{base}-{suffix[1:]}'  # BTC-USDT
        return None

    @staticmethod
    def _parse_table_output(text: str) -> Optional[pd.DataFrame]:
        """
        解析 OKX CLI 表格式输出为 DataFrame。

        输入格式:
            time                open     high     low      close    vol
            2026/5/21 00:00:00  77458.2  78177.3  77018.7  77800    1961.98

        输出:
            [date, open, high, low, close, volume]
        """
        lines = text.strip().split('\n')
        if len(lines) < 3:
            return None

        # 找到表头行（包含 time + open + high）
        header_idx = None
        for i, line in enumerate(lines):
            if 'time' in line.lower() and 'open' in line.lower():
                header_idx = i
                break

        if header_idx is None:
            return None

        # 分隔符行（---）之后是数据行
        data_start = header_idx + 2  # 跳过分隔符行
        data_lines = lines[data_start:]

        records = []
        for line in data_lines:
            line = line.strip()
            if not line:
                continue
            parts = re.split(r'\s{2,}', line)  # 按两个以上空格分割
            if len(parts) < 6:
                continue

            try:
                time_str = parts[0].strip()
                # 转换时间格式: 2026/5/21 00:00:00 → 2026-05-21
                dt = datetime.strptime(time_str, '%Y/%m/%d %H:%M:%S')
                date_str = dt.strftime('%Y-%m-%d')

                records.append({
                    'date': date_str,
                    'open': float(parts[1].replace(',', '')),
                    'high': float(parts[2].replace(',', '')),
                    'low': float(parts[3].replace(',', '')),
                    'close': float(parts[4].replace(',', '')),
                    'volume': float(parts[5].replace(',', '')),
                })
            except (ValueError, IndexError):
                continue

        if not records:
            return None

        df = pd.DataFrame(records)
        df = df.sort_values('date').reset_index(drop=True)
        return df

    @staticmethod
    def _parse_ticker_output(text: str) -> Optional[Dict]:
        """
        解析 OKX CLI ticker 输出为 Dict。
        """
        lines = text.strip().split('\n')
        for line in lines:
            if 'last' in line.lower() or line.startswith('BTC'):
                continue  # skip header/separator
            parts = re.split(r'\s{2,}', line)
            if len(parts) >= 6:
                try:
                    price = float(parts[4]) if len(parts) > 4 else 0.0
                    return {
                        'price': price,
                        'open': float(parts[5]) if len(parts) > 5 else price,
                        'high': float(parts[6]) if len(parts) > 6 else price,
                        'low': float(parts[7]) if len(parts) > 7 else price,
                        'volume': 0.0,
                    }
                except (ValueError, IndexError):
                    pass
        return None