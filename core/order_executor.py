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
import time
from datetime import datetime

try:
    import futu as ft
    FUTU_AVAILABLE = True
except ImportError:
    FUTU_AVAILABLE = False

from .utils import to_futu_code
from .futu_adapter import FutuAdapter, PlaceOrderResult
from .order_journal import OrderJournal, OrderStatus


class OrderExecutor:
    """统一订单执行器 — 支持港股+美股。"""

    def __init__(self, host='127.0.0.1', port=11111, dry_run=True,
                 journal_factory=None, terminal_poll_timeout_seconds=15.0,
                 terminal_poll_interval_seconds=1.0, sleep_fn=None,
                 monotonic_fn=None):
        self.host = host
        self.port = port
        self.dry_run = dry_run
        self.trade_log = []
        self._adapter = FutuAdapter(host=host, port=port)
        self._journal_factory = journal_factory or OrderJournal
        self.terminal_poll_timeout_seconds = max(
            0.0, float(terminal_poll_timeout_seconds),
        )
        self.terminal_poll_interval_seconds = max(
            0.0, float(terminal_poll_interval_seconds),
        )
        self._sleep = sleep_fn or time.sleep
        self._monotonic = monotonic_fn or time.monotonic

    def execute_orders(self, orders, log_path=None, risk_manager=None):
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
            mkt_results = self._execute_market(
                market, mkt_orders, label, risk_manager=risk_manager,
            )
            results.extend(mkt_results)

        # 保存日志
        if log_path:
            self._save_log(results, log_path)

        return results

    def _execute_market(self, market, orders, label, risk_manager=None):
        """执行单一市场的订单。"""
        results = []

        if self.dry_run:
            for order in orders:
                result = self._place_single_order(order, market)
                results.append(result)
            return results

        if not FUTU_AVAILABLE:
            for o in orders:
                results.append({
                    **o, 'status': 'ERROR',
                    'message': 'futu-api 未安装',
                })
            return results

        journal = self._journal_factory(market)
        token = journal.acquire_lease()
        if not token:
            journal.close()
            raise RuntimeError(f'{market} execution lease is already ACTIVE; manual reconciliation required')

        try:
            journal.reconcile(self._adapter)
            journal.apply_stop_side_effects(risk_manager)
            if journal.has_blocking_orders():
                unresolved = journal.unresolved_orders()
                raise RuntimeError(
                    f'{market} has unresolved orders before new execution: '
                    f'{[(o["intent_id"][:8], o["status"]) for o in unresolved]}'
                )

            sell_orders = [o for o in orders if o['action'].upper() == 'SELL']
            buy_orders = [o for o in orders if o['action'].upper() == 'BUY']

            sell_results = []
            for order in sell_orders:
                result = self._place_single_order(
                    order, market, journal=journal,
                    risk_manager=risk_manager,
                )
                sell_results.append(result)
                results.append(result)

            self._wait_for_terminal_results(
                journal, sell_results, risk_manager=risk_manager,
            )

            if sell_orders:
                sell_statuses = {r.get('status') for r in sell_results}
                if sell_statuses != {OrderStatus.FILLED_ALL.value}:
                    for order in buy_orders:
                        skipped = {
                            **order,
                            'futu_code': to_futu_code(order['symbol']),
                            'status': 'SKIP',
                            'message': 'LIVE BUY skipped because not all SELL orders FILLED_ALL',
                        }
                        results.append(skipped)
                        self.trade_log.append(skipped)
                    return results

            for order in buy_orders:
                if any(r.get('status') in OrderStatus.uncertain_set() for r in results):
                    skipped = {
                        **order,
                        'futu_code': to_futu_code(order['symbol']),
                        'status': 'SKIP',
                        'message': 'LIVE BUY skipped after uncertain prior order',
                    }
                    results.append(skipped)
                    self.trade_log.append(skipped)
                    continue
                result = self._place_single_order(
                    order, market, journal=journal,
                    risk_manager=risk_manager,
                )
                results.append(result)
                self._wait_for_terminal_results(
                    journal, [result], risk_manager=risk_manager,
                )

            journal.reconcile(self._adapter)
            journal.apply_stop_side_effects(risk_manager)
            return results
        finally:
            journal.release_lease()
            journal.close()

        return results

    def _wait_for_terminal_results(self, journal, results, risk_manager=None):
        """Bounded polling that keeps executor results aligned with the journal."""
        tracked = [r for r in results if r.get('intent_id')]
        if not tracked:
            return

        deadline = self._monotonic() + self.terminal_poll_timeout_seconds
        while True:
            journal.reconcile(self._adapter, include_history=False)
            journal.apply_stop_side_effects(risk_manager)

            pending = []
            for result in tracked:
                entry = journal.get_order(result['intent_id'])
                if not entry:
                    continue
                result.update({
                    'order_id': entry.get('order_id', result.get('order_id', '')),
                    'status': entry.get('status', result.get('status', '')),
                    'futu_status': entry.get('futu_status', ''),
                    'dealt_qty': entry.get('dealt_qty', 0),
                    'dealt_avg_price': entry.get('dealt_avg_price', 0),
                    'filled_at': entry.get('filled_at', ''),
                })
                if entry.get('status') not in OrderStatus.terminal_set():
                    pending.append(result)

            if not pending or self._monotonic() >= deadline:
                return
            remaining = max(0.0, deadline - self._monotonic())
            self._sleep(min(self.terminal_poll_interval_seconds, remaining))

    def _place_single_order(self, order, market, journal=None, risk_manager=None):
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
            if journal is None:
                raise RuntimeError('LIVE execution requires an OrderJournal')

            journal_entry = journal.reserve_order({
                **order,
                'symbol': symbol,
                'futu_code': futu_code,
                'action': action,
                'qty': qty,
                'price': price,
            })
            if journal_entry['status'] != OrderStatus.RESERVED.value:
                log_entry = {
                    'time': datetime.now().isoformat(),
                    'symbol': symbol, 'futu_code': futu_code,
                    'action': action, 'qty': qty, 'price': price,
                    'cost': round(qty * price, 2),
                    'order_id': journal_entry.get('order_id', ''),
                    'intent_id': journal_entry['intent_id'],
                    'broker_remark': journal_entry['broker_remark'],
                    'reason': reason,
                    'status': journal_entry['status'],
                    'message': 'existing journal intent; broker submit suppressed',
                }
                self.trade_log.append(log_entry)
                print(f'  [JOURNAL] {action} {futu_code}: existing intent {journal_entry["status"]}')
                return log_entry

            journal.mark_submitting(journal_entry['intent_id'])
            result: PlaceOrderResult = self._adapter.place_order(
                symbol=symbol, side=action, qty=qty,
                price_type='LIMIT', limit_price=price,
                market=market,
                remark=journal_entry['broker_remark'],
            )
            final_entry = journal.record_place_result(journal_entry['intent_id'], result)
            journal.apply_stop_side_effects(risk_manager)
            log_entry = {
                'time': datetime.now().isoformat(),
                'symbol': symbol, 'futu_code': futu_code,
                'action': action, 'qty': qty, 'price': price,
                'cost': round(qty * price, 2),
                'order_id': result.order_id,
                'intent_id': journal_entry['intent_id'],
                'broker_remark': journal_entry['broker_remark'],
                'reason': reason,
                'status': final_entry.get('status', result.status.value),
                'message': result.message or f'order_id={result.order_id}',
            }
            self.trade_log.append(log_entry)
            print(f'  [ORDER] {action} {futu_code} {qty}股 @ {price:.2f} → {log_entry["status"]} {result.order_id}  ({reason})')
            return log_entry
        except Exception as e:
            log_entry = {
                **order, 'futu_code': futu_code,
                'status': OrderStatus.UNKNOWN.value if not self.dry_run else 'ERROR',
                'message': str(e),
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
