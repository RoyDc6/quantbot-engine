# -*- coding: utf-8 -*-
"""
core/order_executor.py - QuantBot 统一订单执行模块
功能:
  1. 港股/美股分别走不同 Futu market context
  2. 支持 DRY-RUN（默认）和 LIVE 模式
  3. 订单记录和日志
  4. lot_size 自动对齐

安全: 默认 DRY-RUN，--live 参数才实际下单
"""
import os
import json
from datetime import datetime

try:
    import futu as ft
    FUTU_AVAILABLE = True
except ImportError:
    FUTU_AVAILABLE = False

from .utils import to_futu_code
from .futu_adapter import FutuAdapter


class OrderExecutor:
    """统一订单执行器 — 支持港股+美股。"""

    def __init__(self, host='127.0.0.1', port=11111, dry_run=True):
        self.host = host
        self.port = port
        self.dry_run = dry_run
        self.trade_log = []
        self._adapter = FutuAdapter(host=host, port=port)

    def execute_orders(self, orders, log_path=None):
        """批量执行订单列表。
        
        Args:
            orders: list of dict，每项:
                {
                    'symbol': '00700.HK',       # 标准符号
                    'action': 'BUY'/'SELL',
                    'qty': 100,
                    'price': 350.0,
                    'lot_size': 100,             # 每手股数
                    'reason': 'STRONG_BUY score=63',
                }
            log_path: 日志保存路径（可选）
        
        Returns:
            list of dict: 执行结果
        """
        if not orders:
            return []

        # 按市场分组
        hk_orders = [o for o in orders if o['symbol'].endswith('.HK')]
        us_orders = [o for o in orders if o['symbol'].endswith('.US')]

        results = []

        for market, mkt_orders, label in [
            ('HK', hk_orders, '港股'),
            ('US', us_orders, '美股'),
        ]:
            if not mkt_orders:
                continue
            mkt_results = self._execute_market(market, mkt_orders, label)
            results.extend(mkt_results)

        # 保存日志
        if log_path:
            self._save_log(results, log_path)

        return results

    def _execute_market(self, market, orders, label):
        """执行单一市场的订单。"""
        results = []

        if not FUTU_AVAILABLE:
            for o in orders:
                results.append({
                    **o, 'status': 'ERROR',
                    'message': 'futu-api 未安装',
                })
            return results

        for order in orders:
            result = self._place_single_order(order, market)
            results.append(result)

        return results

    def _place_single_order(self, order, market):
        """执行单笔订单。使用 FutuAdapter 统一下单。"""
        symbol = order['symbol']
        action = order['action']
        qty = int(order['qty'])
        raw_price = float(order['price'])
        lot_size = int(order.get('lot_size', 1 if market == 'US' else 100))
        reason = order.get('reason', '')

        # 价格精度格式化：美股2位，港股3位
        if market == 'US':
            price = round(raw_price, 2)
        else:
            price = round(raw_price, 3)

        # 整手对齐
        qty = (qty // lot_size) * lot_size
        if qty <= 0:
            return {
                **order, 'qty': 0,
                'status': 'SKIP', 'message': '股数不足一手',
            }

        futu_code = to_futu_code(symbol)

        if self.dry_run:
            log_entry = {
                'time': datetime.now().isoformat(),
                'symbol': symbol, 'futu_code': futu_code,
                'action': action, 'qty': qty, 'price': price,
                'cost': round(qty * price, 2),
                'reason': reason,
                'status': 'DRY-RUN', 'message': '模拟执行',
            }
            self.trade_log.append(log_entry)
            print(f'  [DRY-RUN] {action} {futu_code} {qty}股 @ {price:.2f} = {qty*price:,.0f}  ({reason})')
            return log_entry

        # 实际下单（通过 FutuAdapter 统一下单）
        try:
            ok, result = self._adapter.place_order(
                symbol=symbol, side=action, qty=qty,
                price_type='LIMIT', limit_price=price,
                market=market,
            )
            if ok:
                log_entry = {
                    'time': datetime.now().isoformat(),
                    'symbol': symbol, 'futu_code': futu_code,
                    'action': action, 'qty': qty, 'price': price,
                    'cost': round(qty * price, 2),
                    'order_id': result,
                    'reason': reason,
                    'status': 'OK', 'message': f'order_id={result}',
                }
                self.trade_log.append(log_entry)
                print(f'  [ORDER] {action} {futu_code} {qty}股 @ {price:.2f} → {result}  ({reason})')
                return log_entry
            else:
                log_entry = {
                    **order, 'futu_code': futu_code,
                    'status': 'ERROR', 'message': result,
                }
                self.trade_log.append(log_entry)
                print(f'  [ERROR] {action} {futu_code}: {result}')
                return log_entry
        except Exception as e:
            log_entry = {
                **order, 'futu_code': futu_code,
                'status': 'ERROR', 'message': str(e),
            }
            self.trade_log.append(log_entry)
            print(f'  [ERROR] {action} {futu_code}: {e}')
            return log_entry

    def _save_log(self, results, log_path):
        """保存交易日志。"""
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        log = {
            'timestamp': datetime.now().isoformat(),
            'mode': 'DRY-RUN' if self.dry_run else 'LIVE',
            'total_orders': len(results),
            'ok': sum(1 for r in results if r.get('status') in ('OK', 'DRY-RUN')),
            'errors': sum(1 for r in results if r.get('status') == 'ERROR'),
            'orders': results,
        }
        with open(log_path, 'w', encoding='utf-8') as f:
            json.dump(log, f, indent=2, ensure_ascii=False, default=str)
        print(f'  日志: {log_path}')
