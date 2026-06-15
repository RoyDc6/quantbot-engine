# -*- coding: utf-8 -*-
"""Phase F2-SEC order consistency smoke tests.

All tests are local-only: fake adapters, temporary SQLite files, and no broker
order placement.
"""

import inspect
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

import core.order_executor as order_executor_module
from core.futu_adapter import PlaceOrderResult, QueryResult
from core.order_executor import OrderExecutor
from core.order_journal import (
    OrderJournal,
    OrderStatus,
    build_order_identity,
    normalize_order_status,
)
from core.stop_loss import RiskManager


class FakeAdapter:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.place_calls = []

    def get_order_list(self, market='HK'):
        return QueryResult(True, [])

    def place_order(self, **kwargs):
        self.place_calls.append(kwargs)
        if self.results:
            return self.results.pop(0)
        return PlaceOrderResult(
            status=OrderStatus.FILLED_ALL,
            order_id=f'ORDER-{len(self.place_calls)}',
            futu_status='FILLED_ALL',
            dealt_qty=kwargs['qty'],
            dealt_avg_price=kwargs['limit_price'],
        )


def _journal_factory(tmp_path: Path):
    def factory(market):
        return OrderJournal(market=market, db_path=tmp_path / f'journal_{market}.db')
    return factory


def test_broker_remark_is_stable_and_short():
    order = {
        'symbol': '00700.HK',
        'action': 'BUY',
        'qty': 100,
        'price': 466.1234,
        'intent_type': 'SIGNAL_BUY',
    }
    a = build_order_identity(order, 'HK', run_date='2026-06-05')
    b = build_order_identity(order, 'HK', run_date='2026-06-05')

    assert a.intent_id == b.intent_id
    assert a.broker_remark == b.broker_remark
    assert a.broker_remark.startswith('QNT:v1:')
    assert len(a.broker_remark.encode('utf-8')) == 39


def test_journal_lease_blocks_second_active_owner(tmp_path):
    db = tmp_path / 'order_journal_HK.db'
    first = OrderJournal('HK', db_path=db, owner='first')
    second = OrderJournal('HK', db_path=db, owner='second')
    try:
        assert first.acquire_lease()
        assert second.acquire_lease() == ''
        assert first.release_lease() is True
        assert second.acquire_lease()
    finally:
        first.close()
        second.close()


def test_journal_unique_intent_and_state_machine(tmp_path):
    journal = OrderJournal('HK', db_path=tmp_path / 'journal.db')
    try:
        assert journal.acquire_lease()
        order = {
            'symbol': '00700.HK',
            'action': 'SELL',
            'qty': 100,
            'price': 400,
            'intent_type': 'STOP_SELL',
        }
        first = journal.reserve_order(order)
        second = journal.reserve_order(order)
        assert first['intent_id'] == second['intent_id']

        journal.mark_submitting(first['intent_id'])
        entry = journal.record_place_result(
            first['intent_id'],
            PlaceOrderResult(
                status=OrderStatus.FILLED_ALL,
                order_id='123',
                futu_status='FILLED_ALL',
                dealt_qty=100,
                dealt_avg_price=400,
            ),
        )
        assert entry['status'] == OrderStatus.FILLED_ALL.value
        assert not journal.has_blocking_orders()
    finally:
        journal.release_lease()
        journal.close()


def test_fresh_reserved_is_not_abandoned_by_timezone_drift(tmp_path):
    journal = OrderJournal('HK', db_path=tmp_path / 'journal.db')
    try:
        assert journal.acquire_lease()
        entry = journal.reserve_order({
            'symbol': '00700.HK',
            'action': 'BUY',
            'qty': 100,
            'price': 100,
            'intent_type': 'SIGNAL_BUY',
        })

        journal.reconcile(FakeAdapter(), stale_reserved_seconds=3600)

        assert journal.get_order(entry['intent_id'])['status'] == OrderStatus.RESERVED.value
    finally:
        journal.release_lease()
        journal.close()


def test_dry_run_never_initializes_journal(tmp_path):
    def forbidden_factory(_market):
        raise AssertionError('DRY_RUN must not initialize journal')

    executor = OrderExecutor(dry_run=True, journal_factory=forbidden_factory)
    results = executor.execute_orders([
        {
            'symbol': '00700.HK',
            'action': 'BUY',
            'qty': 100,
            'price': 100,
            'lot_size': 100,
            'intent_type': 'SIGNAL_BUY',
        }
    ])
    assert results[0]['status'] == 'DRY-RUN'


def test_live_stop_sell_filled_confirms_cooldown(tmp_path, monkeypatch):
    monkeypatch.setattr(order_executor_module, 'FUTU_AVAILABLE', True)
    risk = RiskManager(state_file=str(tmp_path / 'risk_state.json'))
    executor = OrderExecutor(dry_run=False, journal_factory=_journal_factory(tmp_path))
    fake = FakeAdapter()
    executor._adapter = fake

    results = executor.execute_orders([
        {
            'symbol': '00700.HK',
            'action': 'SELL',
            'qty': 100,
            'price': 100,
            'lot_size': 100,
            'intent_type': 'STOP_SELL',
        }
    ], risk_manager=risk)

    assert results[0]['status'] == OrderStatus.FILLED_ALL.value
    assert risk.is_in_cooldown('HK.00700')
    assert fake.place_calls[0]['remark'].startswith('QNT:v1:')


def test_live_buy_skipped_unless_all_sells_filled(tmp_path, monkeypatch):
    monkeypatch.setattr(order_executor_module, 'FUTU_AVAILABLE', True)
    executor = OrderExecutor(dry_run=False, journal_factory=_journal_factory(tmp_path))
    executor._adapter = FakeAdapter([
        PlaceOrderResult(
            status=OrderStatus.SUBMITTED,
            order_id='SELL-1',
            futu_status='SUBMITTED',
        )
    ])

    results = executor.execute_orders([
        {
            'symbol': '00700.HK',
            'action': 'SELL',
            'qty': 100,
            'price': 100,
            'lot_size': 100,
            'intent_type': 'REVERSAL_SELL',
        },
        {
            'symbol': '09988.HK',
            'action': 'BUY',
            'qty': 100,
            'price': 80,
            'lot_size': 100,
            'intent_type': 'SIGNAL_BUY',
        },
    ])

    assert results[0]['status'] == OrderStatus.SUBMITTED.value
    assert results[1]['status'] == 'SKIP'


def test_query_result_require_fails_closed():
    with pytest.raises(RuntimeError, match='positions failed'):
        QueryResult(False, error='network timeout').require('positions')


def test_stop_cooldown_survives_cleanup_closed(tmp_path):
    risk = RiskManager(state_file=str(tmp_path / 'risk_state.json'))
    recent_stop = (datetime.now() - timedelta(days=1)).isoformat()
    risk.confirm_stop('HK.00700', recent_stop)
    risk.entry_prices['HK.00700'] = 100

    risk.cleanup_closed(active_codes=set())

    assert risk.is_in_cooldown('HK.00700')


def test_futu_place_order_signature_matches_sdk():
    ft = pytest.importorskip('futu')

    sig = inspect.signature(ft.OpenSecTradeContext.place_order)
    assert 'remark' in sig.parameters
    assert 'time_in_force' in sig.parameters
    assert 'fill_side_type' not in sig.parameters


def test_sdk_status_mapping_is_exhaustive():
    ft = pytest.importorskip('futu')

    journal = OrderJournal('HK', db_path=':memory:')
    try:
        journal.validate_futu_sdk_mapping(ft)
    finally:
        journal.close()
    assert normalize_order_status('FILLED_ALL') == OrderStatus.FILLED_ALL
    assert normalize_order_status('N/A') == OrderStatus.UNKNOWN


def test_cancelling_statuses_remain_blocking_non_terminal():
    for raw_status in ('CANCELLING_PART', 'CANCELLING_ALL', 12, 13):
        status = normalize_order_status(raw_status)
        assert status == OrderStatus.CANCELLING
        assert status.value not in OrderStatus.terminal_set()
        assert status.value in OrderStatus.blocking_set()
        assert status.value in OrderStatus.uncertain_set()
