# -*- coding: utf-8 -*-
"""
core/futu_adapter.py - QuantBot Futu API 适配层
薄封装：提供统一的 Futu 数据+交易接口，供 FusionController 调用

定位：底层基建层，不包含信号逻辑
原则：每次调用创建独立的 short-lived 连接（Futu OpenD 是单进程的，无需复用）
     下单接口保留给 TradeExecutor，本层只做账户查询和报价
"""

import json
from typing import Optional, List, Dict, Tuple
from datetime import datetime, timedelta
import sys
from pathlib import Path
import numpy as np
import pandas as pd

# Futu API（可选依赖）
try:
    import futu as ft
    FUTU_AVAILABLE = True
except ImportError:
    FUTU_AVAILABLE = False

# 路径注入
BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from config import FUTU_HOST, FUTU_PORT

from core.base_adapter import BaseAdapter


class FutuAdapter(BaseAdapter):
    """Futu API 适配层 — 数据 + 账户 + 交易。"""

    def __init__(self, host: str = FUTU_HOST, port: int = FUTU_PORT):
        self.host = host
        self.port = port
        self._available = FUTU_AVAILABLE

    # ─── 可用性 ────────────────────────────────────────────────
    @property
    def available(self) -> bool:
        return self._available

    def test_connection(self) -> Tuple[bool, str]:
        """测试 Futu OpenD 连接。"""
        if not self._available:
            return False, 'futu-api 未安装'
        try:
            ctx = ft.OpenQuoteContext(host=self.host, port=self.port)
            ret, data = ctx.get_global_state()
            ctx.close()
            if ret == ft.RET_OK:
                return True, f'Futu OpenD 连接成功'
            return False, f'Futu OpenD 返回错误: {data}'
        except Exception as e:
            return False, f'Futu OpenD 连接失败: {e}'

    # ─── K 线获取 ──────────────────────────────────────────────
    def fetch_kline(self, symbol: str, count: int = 252,
                    ktype: str = 'K_DAY', autype: str = 'qfq'
                    ) -> Optional[pd.DataFrame]:
        """获取单个标的日 K 线。

        Args:
            symbol: 标准符号 '00700.HK' 或 'US.AAPL'
            count: K 线根数
            ktype: K 线类型 'K_DAY' / 'K_WEEK' / 'K_MON'
            autype: 复权类型 'qfq'(前复权) / None(不复权)

        Returns:
            pd.DataFrame | None: [date, open, high, low, close, volume]
        """
        if not self._available:
            return None

        futu_code = self._to_futu(symbol)
        start = (datetime.now() - timedelta(days=count * 2 + 100)).strftime('%Y-%m-%d')
        end = datetime.now().strftime('%Y-%m-%d')

        # ktype 参数处理（支持字符串和枚举）
        if isinstance(ktype, str):
            ktype_map = {
                'K_DAY': ft.KLType.K_DAY,
                'K_WEEK': ft.KLType.K_WEEK,
                'K_MON': ft.KLType.K_MON,
            }
            ktype = ktype_map.get(ktype, ft.KLType.K_DAY)

        # autype 参数处理
        if autype == 'qfq':
            autype_val = ft.AuType.QFQ
        elif autype == 'hfq':
            autype_val = ft.AuType.HFQ
        else:
            autype_val = ft.AuType.NONE

        try:
            result = [None]
            def _fetch():
                try:
                    ctx = ft.OpenQuoteContext(host=self.host, port=self.port)
                    try:
                        ret, data, _ = ctx.request_history_kline(
                            futu_code,
                            start=start, end=end,
                            ktype=ktype,
                            autype=autype_val,
                            max_count=count,
                        )
                        if ret == ft.RET_OK and data is not None and len(data) > 0:
                            result[0] = self._normalize_kline_df(data)
                    finally:
                        ctx.close()
                except Exception:
                    pass

            import threading
            t = threading.Thread(target=_fetch, daemon=True)
            t.start()
            t.join(timeout=15)
            return result[0]

        except Exception:
            return None

    # ─── 批量报价 ──────────────────────────────────────────────
    def fetch_quotes(self, symbols: List[str]) -> Dict[str, Dict]:
        """批量获取实时报价。

        Args:
            symbols: 标准符号列表 ['00700.HK', 'US.AAPL']

        Returns:
            {symbol: {price, high, low, open, volume, lot_size, name}}
        """
        result = {}
        if not self._available or not symbols:
            return result

        # 分组港股/美股
        hk_std = [s for s in symbols if s.endswith('.HK')]
        us_std = [s for s in symbols if s.endswith('.US')]

        for std_list, market in [(hk_std, 'HK'), (us_std, 'US')]:
            if not std_list:
                continue
            futu_codes = [self._to_futu(s) for s in std_list]
            try:
                ctx = ft.OpenQuoteContext(host=self.host, port=self.port)
                try:
                    ret, snap = ctx.get_market_snapshot(futu_codes)
                    if ret == ft.RET_OK and snap is not None:
                        for _, row in snap.iterrows():
                            std = self._to_std(row['code'])
                            result[std] = {
                                'price': float(row.get('last_price', 0)),
                                'high': float(row.get('high_price', 0)),
                                'low': float(row.get('low_price', 0)),
                                'open': float(row.get('open_price', 0)),
                                'volume': float(row.get('volume', 0)),
                                'lot_size': int(row.get('lot_size', 100 if market == 'HK' else 1)),
                                'name': str(row.get('stock_name', '')),
                            }
                finally:
                    ctx.close()
            except Exception:
                pass

        return result

    def fetch_quote(self, symbol: str) -> Optional[Dict]:
        """获取单个标的实时报价。"""
        quotes = self.fetch_quotes([symbol])
        return quotes.get(symbol)

    # ─── 账户信息 ──────────────────────────────────────────────
    def get_account_info(self, market: str = 'HK') -> Optional[Dict]:
        """查询模拟账户信息。

        Args:
            market: 'HK' 或 'US'

        Returns:
            {total_assets, cash, market_val, power} | None
        """
        if not self._available:
            return None
        mkt = ft.Market.HK if market.upper() == 'HK' else ft.Market.US
        try:
            ctx = ft.OpenSecTradeContext(
                filter_trdmarket=mkt,
                host=self.host, port=self.port,
            )
            try:
                ret, acc = ctx.accinfo_query(trd_env=ft.TrdEnv.SIMULATE)
                if ret != ft.RET_OK:
                    return None
                row = acc.iloc[0]
                return {
                    'total_assets': float(row.get('total_assets', 0)),
                    'cash': float(row.get('cash', 0)),
                    'market_val': float(row.get('market_val', 0)),
                    'power': float(row.get('power', 0)),
                }
            finally:
                ctx.close()
        except Exception:
            return None

    def get_positions(self, market: str = 'HK') -> List[Dict]:
        """查询模拟账户持仓。

        Returns:
            [{code, name, qty, can_sell_qty, cost_price, current_price, market_val}]
        """
        if not self._available:
            return []
        mkt = ft.Market.HK if market.upper() == 'HK' else ft.Market.US
        try:
            ctx = ft.OpenSecTradeContext(
                filter_trdmarket=mkt,
                host=self.host, port=self.port,
            )
            try:
                ret, pdata = ctx.position_list_query(trd_env=ft.TrdEnv.SIMULATE)
                if ret != ft.RET_OK or pdata is None:
                    return []
                positions = []
                for _, row in pdata.iterrows():
                    qty = float(row.get('qty', 0))
                    cost = float(row.get('cost_price', 0))
                    val = float(row.get('market_val', 0))
                    cur = val / max(qty, 1)
                    positions.append({
                        'code': self._to_std(row['code']),
                        'name': str(row.get('stock_name', '')),
                        'qty': qty,
                        'can_sell_qty': float(row.get('can_sell_qty', qty)),
                        'cost_price': cost,
                        'current_price': cur,
                        'market_val': val,
                        'pnl_pct': (cur / cost - 1) * 100 if cost > 0 else 0,
                    })
                return positions
            finally:
                ctx.close()
        except Exception:
            return []

    # ─── 下单 ──────────────────────────────────────────────────
    def place_order(self, symbol: str, side: str, qty: int,
                    price_type: str = 'MARKET', limit_price: float = 0.0,
                    market: str = 'HK') -> Tuple[bool, str]:
        """下模拟订单。

        Args:
            symbol: 标准符号
            side: 'BUY' 或 'SELL'
            qty: 股数
            price_type: 'MARKET' 或 'LIMIT'
            limit_price: LIMIT 单的限价
            market: 'HK' 或 'US'

        Returns:
            (success: bool, order_id_or_reason: str)
        """
        if not self._available:
            return False, 'futu-api 未安装'

        trd_side = ft.TrdSide.BUY if side.upper() == 'BUY' else ft.TrdSide.SELL
        mkt = ft.Market.HK if market.upper() == 'HK' else ft.Market.US
        futu_code = self._to_futu(symbol)

        try:
            ctx = ft.OpenSecTradeContext(
                filter_trdmarket=mkt,
                host=self.host, port=self.port,
            )
            try:
                ret, data = ctx.place_order(
                    price=limit_price if price_type == 'LIMIT' else 0.0,
                    qty=qty,
                    code=futu_code,
                    trd_side=trd_side,
                    trd_env=ft.TrdEnv.SIMULATE,
                    order_type=ft.OrderType.NORMAL,
                    fill_side_type=ft.FillSideType.FILL_OR_KILL if price_type == 'LIMIT'
                    else ft.FillSideType.NORMAL,
                )
                if ret == ft.RET_OK and data is not None and len(data) > 0:
                    return True, str(data.iloc[0].get('order_id', 'OK'))
                return False, f'下单失败: ret={ret}'
            finally:
                ctx.close()
        except Exception as e:
            return False, f'下单异常: {e}'

    # ─── VIX 数据 ──────────────────────────────────────────────
    def fetch_vix_data(self, cache_path: Optional[str] = None) -> Dict[str, float]:
        """获取 VIX/VXX 数据。优先从缓存读取。

        Args:
            cache_path: 缓存文件路径，默认 scanner/cache/VXX_US.json

        Returns:
            dict: {date_str: vix_value}
        """
        if cache_path is None:
            cache_path = str(Path(__file__).resolve().parent.parent / 'scanner' / 'cache' / 'VXX_US.json')

        vix_map: Dict[str, float] = {}
        p = Path(cache_path)
        if not p.exists():
            fallback = Path(__file__).resolve().parent.parent / '_archive' / 'scanner_v1' / 'cache' / 'VXX_US.json'
            if fallback.exists():
                p = fallback

        if p.exists():
            try:
                with open(p, encoding='utf-8') as f:
                    vxx = json.load(f)
                for r in vxx:
                    d = datetime.fromtimestamp(r['date'] / 1000).strftime('%Y-%m-%d')
                    vix_map[d] = float(r['close'])
            except Exception:
                pass

        return vix_map

    # ─── 内部工具 ──────────────────────────────────────────────
    @staticmethod
    def _to_futu(symbol: str) -> str:
        """标准符号 → Futu 代码"""
        parts = symbol.split('.')
        if len(parts) == 2:
            return f'{parts[1]}.{parts[0]}'
        return symbol

    @staticmethod
    def _to_std(futu_code: str) -> str:
        """Futu 代码 → 标准符号"""
        parts = futu_code.split('.')
        if len(parts) == 2:
            return f'{parts[1]}.{parts[0]}'
        return futu_code

    @staticmethod
    def _normalize_kline_df(data: pd.DataFrame) -> pd.DataFrame:
        """标准化 Futu K 线 DataFrame。"""
        df = data.copy()
        col_map = {}
        for c in df.columns:
            cl = c.lower()
            if cl in ('time_key', 'time_key'):
                col_map[c] = 'date'
            elif cl in ('open', 'high', 'low', 'close', 'volume'):
                col_map[c] = cl
        df = df.rename(columns=col_map)

        if 'date' in df.columns:
            df['date'] = df['date'].astype(str).str[:10]
        for c in ['open', 'high', 'low', 'close', 'volume']:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors='coerce')

        cols = [c for c in ['date', 'open', 'high', 'low', 'close', 'volume'] if c in df.columns]
        return df[cols].dropna(subset=['close']).reset_index(drop=True)