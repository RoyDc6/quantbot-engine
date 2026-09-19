# -*- coding: utf-8 -*-
"""
core/crypto_adapter.py — QuantBot OKX Crypto 数据适配器

通过 OKX CLI 获取加密货币 K 线数据。
实现 BaseAdapter 接口，供 AdapterFactory / FusionController 调用。

数据源: OKX API v5（通过 @okx_ai/okx-trade-cli）
符号格式: BTC.USDT → instId BTC-USDT
"""

import json
import subprocess
import sys
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
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

OKX_CANDLE_LIMIT = 300
OKX_DAILY_TIMEZONE = ZoneInfo('Asia/Shanghai')
CONFIRMED_BAR_TYPES = {'K_DAY', 'K_WEEK', 'K_MON'}


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
        self._cli_available = None
        self._rest_base = 'https://www.okx.com'

    # ─── 核心接口 ────────────────────────────────────────────

    @property
    def available(self) -> bool:
        """检测 okx CLI 或 OKX 公共 REST 行情是否可用。"""
        if self._available is None:
            self._available = self._detect_cli_available() or self._rest_ticker('BTC-USDT') is not None
        return self._available

    def fetch_kline(self, symbol: str, count: int = 252,
                    ktype: str = 'K_DAY', **kwargs) -> Optional[pd.DataFrame]:
        """
        获取加密货币 K 线数据。

        Args:
            symbol: 标准符号 'BTC.USDT' / 'ETH.USDT'
            count: K 线根数（日线等已收盘周期支持自动分页）
            ktype: 支持 K_1M / K_5M / K_15M / K_30M / K_1H / K_4H / K_DAY / K_WEEK / K_MON

        Returns:
            pd.DataFrame | None: [date, open, high, low, close, volume]
        """
        inst_id = self._to_inst_id(symbol)
        if not inst_id:
            return None

        confirmed_only = bool(kwargs.get('confirmed_only', ktype in CONFIRMED_BAR_TYPES))

        # 日/周/月线必须通过 REST 获取 confirm 字段。CLI 表格输出没有
        # confirm，无法区分正在形成的 K 线，不能用于闭合周期信号。
        if confirmed_only:
            return self._fetch_kline_rest(
                inst_id,
                count=count,
                ktype=ktype,
                confirmed_only=confirmed_only,
            )

        if not self.available:
            return None

        if not self._detect_cli_available():
            return self._fetch_kline_rest(
                inst_id,
                count=count,
                ktype=ktype,
                confirmed_only=False,
            )

        bar = KTYPE_MAP.get(ktype, '1D')
        limit = min(count, OKX_CANDLE_LIMIT)

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
        inst_id = self._to_inst_id(symbol)
        if not inst_id:
            return None

        if not self.available:
            return None

        if not self._detect_cli_available():
            return self._rest_ticker(inst_id)

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

    def test_connection(self) -> Tuple[bool, str]:
        """测试 OKX 公共行情连接。"""
        if self._detect_cli_available():
            return True, 'OKX CLI 可用'
        if self._rest_ticker('BTC-USDT') is not None:
            return True, 'OKX REST 公共行情可用'
        return False, 'OKX CLI 不可用，REST 公共行情不可达'

    def _detect_cli_available(self) -> bool:
        """检测 okx CLI 是否可用。"""
        if self._cli_available is None:
            try:
                # okx 是 PowerShell 包装命令（okx.ps1），需 shell=True
                result = subprocess.run(
                    f'{self._cli} market ticker BTC-USDT',
                    capture_output=True, shell=True, text=True, timeout=10,
                    encoding='utf-8', errors='replace',
                )
                stdout = result.stdout or ''
                self._cli_available = result.returncode == 0 and 'BTC-USDT' in stdout and 'last' in stdout
            except Exception:
                self._cli_available = False
        return bool(self._cli_available)

    def _fetch_kline_rest(self, inst_id: str, count: int = 252,
                          ktype: str = 'K_DAY',
                          confirmed_only: bool = False) -> Optional[pd.DataFrame]:
        """通过 OKX 公共 REST 获取 K 线。

        对闭合周期默认过滤 ``confirm != 1`` 的当前形成中 K 线，并在需要
        超过 300 根历史数据时使用 history-candles 向前分页。
        """
        bar = KTYPE_MAP.get(ktype, '1D')
        target_count = max(int(count), 1)
        first_limit = min(
            target_count + (1 if confirmed_only else 0),
            OKX_CANDLE_LIMIT,
        )
        params = {'instId': inst_id, 'bar': bar, 'limit': first_limit}
        data = self._rest_get('/api/v5/market/candles', params)
        rows = data.get('data') if data else None
        if not rows:
            return None

        raw_rows = list(rows)
        seen_ts = {str(row[0]) for row in raw_rows if row}

        def usable_count(items) -> int:
            if not confirmed_only:
                return len(items)
            return sum(1 for row in items if len(row) > 8 and str(row[8]) == '1')

        # OKX 每页最多 300 根。after=<oldest ts> 返回更早的记录。
        page_count = 0
        while usable_count(raw_rows) < target_count and page_count < 50:
            oldest_ts = min(int(row[0]) for row in raw_rows if row)
            remaining = target_count - usable_count(raw_rows)
            page_limit = min(max(remaining, 1), OKX_CANDLE_LIMIT)
            history = self._rest_get('/api/v5/market/history-candles', {
                'instId': inst_id,
                'bar': bar,
                'after': str(oldest_ts),
                'limit': page_limit,
            })
            history_rows = history.get('data') if history else None
            if not history_rows:
                break

            new_rows = [row for row in history_rows if row and str(row[0]) not in seen_ts]
            if not new_rows:
                break
            raw_rows.extend(new_rows)
            seen_ts.update(str(row[0]) for row in new_rows)
            page_count += 1

        records = []
        incomplete_bars_excluded = 0
        for row in raw_rows:
            try:
                confirm = str(row[8]) if len(row) > 8 else ''
                if confirmed_only and confirm != '1':
                    incomplete_bars_excluded += 1
                    continue
                ts_ms = int(row[0])
                dt_utc = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
                dt_local = dt_utc.astimezone(OKX_DAILY_TIMEZONE)
                records.append({
                    'timestamp': ts_ms,
                    'date': dt_local.strftime('%Y-%m-%d'),
                    'open': float(row[1]),
                    'high': float(row[2]),
                    'low': float(row[3]),
                    'close': float(row[4]),
                    'volume': float(row[5]),
                    'confirm': confirm == '1',
                })
            except (ValueError, TypeError, IndexError):
                continue

        if not records:
            return None
        df = pd.DataFrame(records)
        df = (
            df.sort_values('timestamp')
            .drop_duplicates(subset=['timestamp'])
            .tail(target_count)
            .reset_index(drop=True)
        )
        df.attrs['source'] = 'OKX_REST'
        df.attrs['signal_asof'] = str(df['date'].iloc[-1])
        df.attrs['bar_confirmed'] = bool(df['confirm'].all())
        df.attrs['bar_timezone'] = str(OKX_DAILY_TIMEZONE)
        df.attrs['incomplete_bars_excluded'] = incomplete_bars_excluded
        df.attrs['requested_count'] = target_count
        df.attrs['returned_count'] = len(df)
        try:
            last_date = datetime.strptime(df.attrs['signal_asof'], '%Y-%m-%d').date()
            local_today = datetime.now(OKX_DAILY_TIMEZONE).date()
            df.attrs['stale_days'] = max(0, (local_today - last_date).days)
        except (TypeError, ValueError):
            pass
        return df

    def _rest_ticker(self, inst_id: str) -> Optional[Dict]:
        """通过 OKX 公共 REST 获取 ticker。"""
        data = self._rest_get('/api/v5/market/ticker', {'instId': inst_id})
        rows = data.get('data') if data else None
        if not rows:
            return None
        row = rows[0]
        try:
            price = float(row.get('last') or 0)
            return {
                'price': price,
                'open': float(row.get('open24h') or price),
                'high': float(row.get('high24h') or price),
                'low': float(row.get('low24h') or price),
                'volume': float(row.get('vol24h') or 0),
                'source': 'OKX_REST',
            }
        except (TypeError, ValueError):
            return None

    def _rest_get(self, path: str, params: Dict) -> Optional[Dict]:
        """OKX 公共 REST GET，失败时返回 None。"""
        url = f'{self._rest_base}{path}?{urlencode(params)}'
        try:
            req = Request(url, headers={'User-Agent': 'QuantBot/1.0'})
            with urlopen(req, timeout=10) as resp:
                payload = json.loads(resp.read().decode('utf-8'))
            if payload.get('code') != '0':
                return None
            return payload
        except Exception:
            return None

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
