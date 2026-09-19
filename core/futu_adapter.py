# -*- coding: utf-8 -*-
"""
core/futu_adapter.py - QuantBot Futu API 适配层
薄封装：提供统一的 Futu 数据+交易接口，供 FusionController 调用

定位：底层基建层，不包含信号逻辑
原则：每次调用创建独立的 short-lived 连接（Futu OpenD 是单进程的，无需复用）
     下单接口保留给 TradeExecutor，本层只做账户查询和报价
"""

import json
import os
import socket
from dataclasses import dataclass
from typing import Optional, List, Dict, Tuple, Any
from datetime import datetime, time, timedelta
import sys
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
import logging

logger = logging.getLogger(__name__)

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
from core.order_journal import OrderStatus, normalize_order_status


@dataclass(frozen=True)
class QueryResult:
    ok: bool
    data: Any = None
    error: str = ''

    def require(self, label: str = 'query') -> Any:
        if not self.ok:
            raise RuntimeError(f'{label} failed: {self.error}')
        return self.data


@dataclass(frozen=True)
class PlaceOrderResult:
    status: OrderStatus
    order_id: str = ''
    futu_status: str = ''
    dealt_qty: float = 0.0
    dealt_avg_price: float = 0.0
    message: str = ''
    filled_at: str = ''


class FutuAdapter(BaseAdapter):
    """Futu API 适配层 — 数据 + 账户 + 交易。"""

    def __init__(self, host: str = FUTU_HOST, port: int = FUTU_PORT):
        self.host = host
        self.port = port
        self._available = FUTU_AVAILABLE
        self.last_vxx_df = None
        self.last_vxx_meta: Dict[str, Any] = {}

    # ─── 可用性 ────────────────────────────────────────────────
    @property
    def available(self) -> bool:
        return self._available

    def test_connection(self, timeout: float = 1.0) -> Tuple[bool, str]:
        """测试 Futu OpenD 连接，并在端口不可达时快速失败。"""
        if not self._available:
            return False, 'futu-api 未安装'

        # futu-api 的 OpenQuoteContext 构造器会在端口拒绝连接时持续重试。
        # 先做轻量 TCP 探测，避免调度任务被阻塞几十分钟且没有明确结论。
        try:
            probe = socket.create_connection(
                (self.host, int(self.port)),
                timeout=max(float(timeout), 0.1),
            )
            probe.close()
        except OSError as e:
            return (
                False,
                f'Futu OpenD TCP 不可达 {self.host}:{self.port}: {e}',
            )

        ctx = None
        try:
            ctx = ft.OpenQuoteContext(host=self.host, port=self.port)
            ret, data = ctx.get_global_state()
            if ret == ft.RET_OK:
                return True, f'Futu OpenD 连接成功'
            return False, f'Futu OpenD 返回错误: {data}'
        except Exception as e:
            return False, f'Futu OpenD 连接失败: {e}'
        finally:
            if ctx is not None:
                try:
                    ctx.close()
                except Exception:
                    pass

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
        is_daily_ktype = (
            ktype == 'K_DAY'
            or ktype == getattr(getattr(ft, 'KLType', None), 'K_DAY', None)
        )
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
            ctx = ft.OpenQuoteContext(host=self.host, port=self.port)
            try:
                # 翻页获取完整 K 线（单次 max_count=252 已验证稳定）
                all_pages = []
                next_key = None
                while True:
                    ret, data, next_key = ctx.request_history_kline(
                        futu_code,
                        start=start, end=end,
                        ktype=ktype,
                        autype=autype_val,
                        max_count=252,
                        page_req_key=next_key,
                    )
                    if ret != ft.RET_OK or data is None or len(data) == 0:
                        break
                    all_pages.append(data)
                    if not next_key:
                        break

                if all_pages:
                    full = pd.concat(all_pages, ignore_index=True)
                    full = full.sort_values('time_key')
                    full = full.drop_duplicates(subset=['time_key']).reset_index(drop=True)
                    # 日线先多保留一根，若最后一根尚未完成，剔除后仍可返回 count 根。
                    keep_count = count + 1 if is_daily_ktype else count
                    if len(full) > keep_count:
                        full = full.iloc[-keep_count:].reset_index(drop=True)
                    result = self._normalize_kline_df(full)
                    if is_daily_ktype and result is not None:
                        result = self._enforce_completed_daily_bars(
                            result,
                            futu_code,
                            count=count,
                        )
                    # ─── 数据新鲜度检测 ────────────────────────────
                    if result is not None and len(result) > 0:
                        try:
                            last_date = pd.to_datetime(result['date'].iloc[-1])
                            stale_days = (datetime.now() - last_date).days
                            result.attrs['stale_days'] = stale_days
                            if stale_days > 30:
                                result.attrs['stale_critical'] = True
                                logger.warning(f'[CRITICAL] {futu_code} K线滞后{stale_days}天（最近日期{result["date"].iloc[-1]}），数据可能已停止同步')
                            elif stale_days > 3:
                                result.attrs['stale_warning'] = True
                                logger.warning(f'[STALE] {futu_code} K线滞后{stale_days}天（最近日期{result["date"].iloc[-1]}）')
                            elif stale_days > 0:
                                logger.info(f'[FRESH] {futu_code} K线滞后{stale_days}天 ✅')
                        except Exception:
                            pass
                    return result
                return None
            finally:
                ctx.close()

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
    def get_account_info(self, market: str = 'HK') -> QueryResult:
        """查询模拟账户信息。

        Args:
            market: 'HK' 或 'US'

        Returns:
            {total_assets, cash, market_val, power} | None
        """
        if not self._available:
            return QueryResult(False, error='futu-api 未安装')
        mkt = ft.Market.HK if market.upper() == 'HK' else ft.Market.US
        try:
            ctx = ft.OpenSecTradeContext(
                filter_trdmarket=mkt,
                host=self.host, port=self.port,
            )
            try:
                ret, acc = ctx.accinfo_query(trd_env=ft.TrdEnv.SIMULATE)
                if ret != ft.RET_OK:
                    return QueryResult(False, error=str(acc))
                row = acc.iloc[0]
                return QueryResult(True, {
                    'total_assets': float(row.get('total_assets', 0)),
                    'cash': float(row.get('cash', 0)),
                    'market_val': float(row.get('market_val', 0)),
                    'power': float(row.get('power', 0)),
                })
            finally:
                ctx.close()
        except Exception as e:
            return QueryResult(False, error=str(e))

    def get_positions(self, market: str = 'HK') -> QueryResult:
        """查询模拟账户持仓。

        Returns:
            [{code, name, qty, can_sell_qty, cost_price, current_price, market_val}]
        """
        if not self._available:
            return QueryResult(False, error='futu-api 未安装')
        mkt = ft.Market.HK if market.upper() == 'HK' else ft.Market.US
        try:
            ctx = ft.OpenSecTradeContext(
                filter_trdmarket=mkt,
                host=self.host, port=self.port,
            )
            try:
                ret, pdata = ctx.position_list_query(trd_env=ft.TrdEnv.SIMULATE)
                if ret != ft.RET_OK or pdata is None:
                    return QueryResult(False, error=str(pdata))
                positions = []
                for _, row in pdata.iterrows():
                    qty = float(row.get('qty', 0))
                    cost = float(row.get('cost_price', 0))
                    val = float(row.get('market_val', 0))
                    cur = val / max(qty, 1)
                    futu_code = str(row.get('code', ''))
                    positions.append({
                        'code': futu_code,
                        'symbol': self._to_std(futu_code),
                        'name': str(row.get('stock_name', '')),
                        'qty': qty,
                        'can_sell_qty': float(row.get('can_sell_qty', qty)),
                        'cost_price': cost,
                        'current_price': cur,
                        'market_val': val,
                        'pnl_pct': (cur / cost - 1) * 100 if cost > 0 else 0,
                    })
                return QueryResult(True, positions)
            finally:
                ctx.close()
        except Exception as e:
            return QueryResult(False, error=str(e))

    # ─── 下单 ──────────────────────────────────────────────────
    def place_order(self, symbol: str, side: str, qty: int,
                    price_type: str = 'MARKET', limit_price: float = 0.0,
                    market: str = 'HK', remark: str = '') -> PlaceOrderResult:
        """下模拟订单。

        Args:
            symbol: 标准符号
            side: 'BUY' 或 'SELL'
            qty: 股数
            price_type: 'MARKET' 或 'LIMIT'
            limit_price: LIMIT 单的限价
            market: 'HK' 或 'US'

        Returns:
            PlaceOrderResult.  RET_ERROR is treated as TIMEOUT because the SDK
            does not return authoritative order status in that branch.
        """
        if not self._available:
            return PlaceOrderResult(
                status=OrderStatus.UNKNOWN,
                message='futu-api 未安装',
            )

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
                    remark=remark or None,
                    time_in_force=ft.TimeInForce.DAY,
                )
                if ret == ft.RET_OK and data is not None and len(data) > 0:
                    row = data.iloc[0]
                    futu_status = row.get('order_status', '')
                    status = normalize_order_status(futu_status)
                    return PlaceOrderResult(
                        status=status,
                        order_id=str(row.get('order_id', '') or ''),
                        futu_status=str(futu_status or ''),
                        dealt_qty=float(row.get('dealt_qty', 0) or 0),
                        dealt_avg_price=float(row.get('dealt_avg_price', 0) or 0),
                        message=str(row.get('last_err_msg', '') or ''),
                    )
                return PlaceOrderResult(
                    status=OrderStatus.TIMEOUT,
                    message=f'下单未返回权威订单状态: ret={ret}, data={data}',
                )
            finally:
                ctx.close()
        except Exception as e:
            return PlaceOrderResult(
                status=OrderStatus.UNKNOWN,
                message=f'下单异常: {e}',
            )

    def get_order_list(self, market: str = 'HK',
                       start: str = '', end: str = '') -> QueryResult:
        """查询模拟账户订单列表，供 journal 恢复/对账使用。"""
        if not self._available:
            return QueryResult(False, error='futu-api 未安装')
        mkt = ft.Market.HK if market.upper() == 'HK' else ft.Market.US
        try:
            ctx = ft.OpenSecTradeContext(
                filter_trdmarket=mkt,
                host=self.host, port=self.port,
            )
            try:
                ret, data = ctx.order_list_query(
                    start=start,
                    end=end,
                    trd_env=ft.TrdEnv.SIMULATE,
                    refresh_cache=True,
                )
                if ret != ft.RET_OK or data is None:
                    return QueryResult(False, error=str(data))
                orders = []
                for _, row in data.iterrows():
                    orders.append({
                        'order_id': str(row.get('order_id', '') or ''),
                        'code': str(row.get('code', '') or ''),
                        'order_status': row.get('order_status', ''),
                        'trd_side': str(row.get('trd_side', '') or ''),
                        'qty': float(row.get('qty', 0) or 0),
                        'price': float(row.get('price', 0) or 0),
                        'dealt_qty': float(row.get('dealt_qty', 0) or 0),
                        'dealt_avg_price': float(row.get('dealt_avg_price', 0) or 0),
                        'remark': str(row.get('remark', '') or ''),
                        'last_err_msg': str(row.get('last_err_msg', '') or ''),
                        'create_time': str(row.get('create_time', '') or ''),
                        'updated_time': str(row.get('updated_time', '') or ''),
                    })
                return QueryResult(True, orders)
            finally:
                ctx.close()
        except Exception as e:
            return QueryResult(False, error=str(e))

    def get_history_order_list(self, market: str = 'HK',
                               start: str = '', end: str = '') -> QueryResult:
        """查询历史订单，供 journal 跨日恢复/对账使用。"""
        if not self._available:
            return QueryResult(False, error='futu-api 未安装')
        mkt = ft.Market.HK if market.upper() == 'HK' else ft.Market.US
        try:
            ctx = ft.OpenSecTradeContext(
                filter_trdmarket=mkt,
                host=self.host, port=self.port,
            )
            try:
                ret, data = ctx.history_order_list_query(
                    status_filter_list=[],
                    code='',
                    start=start,
                    end=end,
                    trd_env=ft.TrdEnv.SIMULATE,
                )
                if ret != ft.RET_OK or data is None:
                    return QueryResult(False, error=str(data))
                orders = []
                for _, row in data.iterrows():
                    orders.append({
                        'order_id': str(row.get('order_id', '') or ''),
                        'code': str(row.get('code', '') or ''),
                        'order_status': row.get('order_status', ''),
                        'trd_side': str(row.get('trd_side', '') or ''),
                        'qty': float(row.get('qty', 0) or 0),
                        'price': float(row.get('price', 0) or 0),
                        'dealt_qty': float(row.get('dealt_qty', 0) or 0),
                        'dealt_avg_price': float(row.get('dealt_avg_price', 0) or 0),
                        'remark': str(row.get('remark', '') or ''),
                        'last_err_msg': str(row.get('last_err_msg', '') or ''),
                        'create_time': str(row.get('create_time', '') or ''),
                        'updated_time': str(row.get('updated_time', '') or ''),
                    })
                return QueryResult(True, orders)
            finally:
                ctx.close()
        except Exception as e:
            return QueryResult(False, error=str(e))
    # ─── VIX 数据 ──────────────────────────────────────────────
    def fetch_vix_data(self, cache_path: Optional[str] = None,
                       expected_as_of: Optional[str] = None) -> Dict[str, float]:
        """获取 VXX 日线，Futu 为主源，本地缓存为受控回退。

        Args:
            cache_path: 缓存文件路径，默认 scanner/cache/VXX_US.json
            expected_as_of: 最新已完成美股交易日（YYYY-MM-DD）。传入时会
                丢弃此日期之后、可能尚未收盘的日 K。

        Returns:
            dict: {date_str: vxx_close}

        Side effects:
            ``last_vxx_df`` 保存本次实际使用的数据；
            ``last_vxx_meta`` 保存 source/as_of/error 等审计元数据。
        """
        if cache_path is None:
            cache_path = str(Path(__file__).resolve().parent.parent / 'scanner' / 'cache' / 'VXX_US.json')

        p = Path(cache_path)
        fetch_error = ''
        df = None

        try:
            fetched = self.fetch_kline(
                'VXX.US',
                count=500,
                ktype='K_DAY',
                autype=None,
            )
            if isinstance(fetched, pd.DataFrame) and not fetched.empty:
                df = fetched.copy()
                df['date'] = pd.to_datetime(df['date'], errors='coerce')
                df = df.dropna(subset=['date', 'close']).sort_values('date')
                if expected_as_of:
                    cutoff = pd.Timestamp(expected_as_of)
                    df = df[df['date'] <= cutoff]
                df = df.tail(500).reset_index(drop=True)
                if len(df) < 30:
                    fetch_error = f'Futu VXX 日线不足 30 根（{len(df)}）'
                    df = None
            else:
                fetch_error = 'Futu VXX 日线无可用数据'
        except Exception as e:
            fetch_error = str(e)
            logger.warning('Futu VXX 日线获取失败，尝试缓存回退: %s', e)

        source = 'FUTU'
        if df is not None:
            try:
                self._write_vxx_cache_atomic(df, p)
            except Exception as e:
                logger.warning('VXX 缓存写入失败（本次仍使用 Futu 数据）: %s', e)
        else:
            source = 'CACHE'
            try:
                df = self._load_vxx_cache(p)
                if isinstance(df, pd.DataFrame) and expected_as_of:
                    df = (
                        df[df['date'] <= pd.Timestamp(expected_as_of)]
                        .tail(500)
                        .reset_index(drop=True)
                    )
                    if df.empty:
                        df = None
            except Exception as e:
                cache_error = str(e)
                fetch_error = '; '.join(x for x in (fetch_error, cache_error) if x)
                df = None

        self.last_vxx_df = df.copy() if isinstance(df, pd.DataFrame) else None
        as_of = (
            pd.Timestamp(df['date'].iloc[-1]).strftime('%Y-%m-%d')
            if isinstance(df, pd.DataFrame) and not df.empty
            else None
        )
        self.last_vxx_meta = {
            'instrument': 'VXX.US',
            'source': source if as_of else 'MISSING',
            'as_of': as_of,
            'expected_as_of': expected_as_of,
            'rows': int(len(df)) if isinstance(df, pd.DataFrame) else 0,
            'fetch_error': fetch_error or None,
        }

        if not isinstance(df, pd.DataFrame) or df.empty:
            return {}
        return {
            pd.Timestamp(row.date).strftime('%Y-%m-%d'): float(row.close)
            for row in df[['date', 'close']].itertuples(index=False)
        }

    @staticmethod
    def _load_vxx_cache(path: Path) -> Optional[pd.DataFrame]:
        """读取主缓存；不再静默回退到废弃 archive。"""
        if not path.exists():
            return None
        with open(path, encoding='utf-8') as f:
            rows = json.load(f)
        if not isinstance(rows, list) or not rows:
            return None
        df = pd.DataFrame(rows)
        if 'date' not in df.columns or 'close' not in df.columns:
            return None
        df['date'] = (
            pd.to_datetime(df['date'], unit='ms', utc=True, errors='coerce')
            .dt.tz_convert('America/New_York')
            .dt.tz_localize(None)
            .dt.normalize()
        )
        for col in ('open', 'high', 'low', 'close', 'volume'):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        return (
            df.dropna(subset=['date', 'close'])
            .sort_values('date')
            .tail(500)
            .reset_index(drop=True)
        )

    @staticmethod
    def _write_vxx_cache_atomic(df: pd.DataFrame, path: Path) -> None:
        """按原缓存 schema 原子更新 VXX 日线。"""
        rows = []
        for row in df.itertuples(index=False):
            d = pd.Timestamp(row.date).date()
            ts = pd.Timestamp(
                datetime(d.year, d.month, d.day),
                tz='America/New_York',
            )
            item = {'date': int(ts.timestamp() * 1000)}
            for col in ('open', 'high', 'low', 'close', 'volume'):
                value = getattr(row, col, None)
                if value is not None and not pd.isna(value):
                    item[col] = float(value)
            rows.append(item)

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f'{path.name}.{os.getpid()}.tmp')
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(rows, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

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
    def _enforce_completed_daily_bars(
            data: pd.DataFrame, futu_code: str, count: int = 252,
            now: Optional[datetime] = None) -> pd.DataFrame:
        """Conservatively expose only completed HK/US daily bars.

        Futu may return the current, still-forming K_DAY row during a session.
        QuantBot research reports must never treat that row as completed.  A
        16:15 local cutoff is intentionally conservative for both markets; on
        half days this can defer a valid bar, but it cannot promote an
        incomplete one.
        """
        original_attrs = dict(getattr(data, 'attrs', {}))
        result = data.copy()
        market = str(futu_code).split('.', 1)[0].upper()
        timezone_name = {
            'HK': 'Asia/Hong_Kong',
            'US': 'America/New_York',
        }.get(market)
        if not timezone_name:
            result.attrs.update(original_attrs)
            result.attrs.update({
                'bar_confirmed': False,
                'bar_finality': 'UNVERIFIED',
                'requested_count': count,
                'returned_count': len(result),
            })
            return result

        market_tz = ZoneInfo(timezone_name)
        if now is None:
            local_now = datetime.now(market_tz)
        elif now.tzinfo is None:
            local_now = now.replace(tzinfo=market_tz)
        else:
            local_now = now.astimezone(market_tz)
        completed_through = local_now.date()
        if local_now.time() < time(16, 15):
            completed_through -= timedelta(days=1)

        try:
            dates = pd.to_datetime(result['date'], errors='raise').dt.date
            before = len(result)
            result = result.loc[dates <= completed_through].tail(count).reset_index(drop=True)
            excluded = before - len(result)
        except Exception:
            result.attrs.update(original_attrs)
            result.attrs.update({
                'bar_confirmed': False,
                'bar_finality': 'UNVERIFIED',
                'bar_timezone': timezone_name,
                'requested_count': count,
                'returned_count': len(result),
            })
            return result

        result.attrs.update(original_attrs)
        result.attrs.update({
            'source': 'FUTU_OPEND_LIVE',
            'bar_confirmed': bool(len(result)),
            'bar_finality': (
                'COMPLETED_SESSION_ONLY' if len(result) else 'UNVERIFIED'
            ),
            'bar_timezone': timezone_name,
            'incomplete_bars_excluded': excluded,
            'requested_count': count,
            'returned_count': len(result),
        })
        if len(result):
            result.attrs['signal_asof'] = str(result['date'].iloc[-1])[:10]
        return result

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
