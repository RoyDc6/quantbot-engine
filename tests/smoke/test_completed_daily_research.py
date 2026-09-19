from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from core.futu_adapter import FutuAdapter
from reports.fusion_report_v3 import (
    _build_account_section,
    _build_execution_tracking,
)
from unified_runner import (
    _fc_result_to_signal,
    _require_completed_daily_signals,
    run,
)


def _bars(*dates):
    return pd.DataFrame({
        'date': list(dates),
        'open': [1.0] * len(dates),
        'high': [2.0] * len(dates),
        'low': [0.5] * len(dates),
        'close': [1.5] * len(dates),
        'volume': [100.0] * len(dates),
    })


def test_hk_intraday_bar_is_excluded_before_completed_cutoff():
    data = _bars('2026-08-12', '2026-08-13')
    result = FutuAdapter._enforce_completed_daily_bars(
        data,
        'HK.00700',
        now=datetime(2026, 8, 13, 10, 0, tzinfo=ZoneInfo('Asia/Hong_Kong')),
    )

    assert result['date'].tolist() == ['2026-08-12']
    assert result.attrs['signal_asof'] == '2026-08-12'
    assert result.attrs['bar_confirmed'] is True
    assert result.attrs['bar_finality'] == 'COMPLETED_SESSION_ONLY'
    assert result.attrs['incomplete_bars_excluded'] == 1


def test_us_prior_session_is_confirmed_next_beijing_morning():
    data = _bars('2026-08-11', '2026-08-12')
    result = FutuAdapter._enforce_completed_daily_bars(
        data,
        'US.AAPL',
        now=datetime(2026, 8, 13, 10, 0, tzinfo=ZoneInfo('Asia/Shanghai')),
    )

    assert result['date'].tolist() == ['2026-08-11', '2026-08-12']
    assert result.attrs['signal_asof'] == '2026-08-12'
    assert result.attrs['bar_timezone'] == 'America/New_York'
    assert result.attrs['bar_confirmed'] is True


def test_fc_mapping_preserves_finality_and_research_gate_accepts_uniform_date():
    fc_result = {
        'ticker': 'AAPL.US',
        'date': '2026-08-12',
        'signal_asof': '2026-08-12',
        'bar_confirmed': True,
        'bar_timezone': 'America/New_York',
        'incomplete_bars_excluded': 0,
        'requested_count': 252,
        'returned_count': 252,
        'fusion': {},
        'directive': {},
        'sources': {},
        'status': {},
    }
    signal = _fc_result_to_signal(fc_result, 'US')

    assert signal['bar_finality'] == 'COMPLETED_SESSION_ONLY'
    assert signal['bar_confirmed'] is True
    assert _require_completed_daily_signals([signal], 'US') == '2026-08-12'


def test_research_gate_rejects_unverified_bar():
    signal = {
        'symbol': 'AAPL.US',
        'date': '2026-08-12',
        'signal_asof': '2026-08-12',
        'bar_confirmed': False,
        'bar_finality': 'UNVERIFIED',
        'bar_timezone': 'America/New_York',
    }

    with pytest.raises(RuntimeError, match='BAR_NOT_CONFIRMED'):
        _require_completed_daily_signals([signal], 'US')


def test_research_gate_rejects_mixed_signal_dates():
    signals = [
        {
            'symbol': symbol,
            'date': signal_date,
            'signal_asof': signal_date,
            'bar_confirmed': True,
            'bar_finality': 'COMPLETED_SESSION_ONLY',
            'bar_timezone': 'Asia/Hong_Kong',
        }
        for symbol, signal_date in (
            ('00700.HK', '2026-08-12'),
            ('01810.HK', '2026-08-13'),
        )
    ]

    with pytest.raises(RuntimeError, match='MIXED_SIGNAL_ASOF'):
        _require_completed_daily_signals(signals, 'HK')


def test_research_report_sections_declare_account_and_order_boundaries():
    account = _build_account_section(
        0, 0, 0, 0,
        report_mode='RESEARCH_ONLY_COMPLETED_DAILY',
        account_access=False,
        order_api_called=False,
    )
    execution = _build_execution_tracking(
        [], [], [], 'HK',
        execution_mode='RESEARCH_ONLY',
        report_mode='RESEARCH_ONLY_COMPLETED_DAILY',
        account_access=False,
        order_api_called=False,
    )

    assert 'Account Access** | DISABLED' in account
    assert 'Order API Called** | NO' in account
    assert 'Account Access | `DISABLED`' in execution
    assert 'Order API Called | `NO`' in execution
    assert '未访问账户/持仓' in execution


def test_research_run_returns_before_every_account_and_order_api(tmp_path):
    class FakeUniverse:
        def get_symbols_by_market(self, market):
            return ['00700.HK']

        def contains(self, symbol):
            return True

        def get_lot_size(self, symbol):
            return 100

    fc_result = {
        'ticker': '00700.HK',
        'date': '2026-08-12',
        'signal_asof': '2026-08-12',
        'bar_confirmed': True,
        'bar_timezone': 'Asia/Hong_Kong',
        'incomplete_bars_excluded': 1,
        'requested_count': 252,
        'returned_count': 252,
        'close': 500.0,
        'data_source': 'FUTU_OPEND_LIVE',
        'fusion': {'level': 'HOLD'},
        'directive': {'level': 'HOLD'},
        'sources': {},
        'status': {},
    }

    with patch('unified_runner.BASE', tmp_path):
        with patch('unified_runner.UniverseManager', return_value=FakeUniverse()):
            with patch('unified_runner.FutuAdapter') as adapter_class:
                adapter = adapter_class.return_value
                adapter.test_connection.return_value = (True, 'mock')
                adapter.fetch_vix_data.return_value = {}
                with patch('unified_runner.FusionController') as controller_class:
                    controller_class.return_value.analyze_ticker.return_value = fc_result
                    with patch('reports.fusion_report_v3.generate_v3_report') as report:
                        rc = run(
                            market='HK',
                            research_report=True,
                            requested_live=False,
                            live_confirmed=False,
                        )

    assert rc == 0
    adapter.get_account_info.assert_not_called()
    adapter.get_positions.assert_not_called()
    adapter.place_order.assert_not_called()
    report.assert_called_once()
