# -*- coding: utf-8 -*-
"""Regression tests for the 2026-06-14 runtime optimization.

These tests protect operational behavior only:
  - status_report performs a real adapter probe
  - CryptoAdapter falls back to OKX public REST when okx CLI is unavailable
  - unified_runner uses UniverseManager as the primary HK/US target source
"""

import io
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))


def _minimal_signal(symbol: str) -> dict:
    return {
        'ticker': symbol,
        'date': '2026-06-14',
        'close': 100.0,
        'data_source': 'Mock',
        'fusion': {
            'level': 'HOLD',
            'score': 0,
            'confidence': 0.70,
            'position_pct': 0,
            'risk_level': 'LOW',
            'reasoning': 'test',
            'raw_scores': {},
            'weights_used': {},
            'warnings': [],
        },
        'directive': {'level': 'HOLD', 'target_pct': 0},
        'sources': {'xmm': {}, 'vp': {}, 'llm': {}},
        'status': {'xmm': 'OK', 'vp': 'OK', 'llm': 'OK'},
        'gate': {'approved': True, 'reject_reasons': []},
        'rsi_daily': 50,
        'rsi_weekly': 50,
        'market_state': 'CRAB',
    }


def test_status_report_probes_and_instantiates_adapters():
    from core.fusion_controller import FusionController

    with patch('core.fusion_controller.AdapterFactory.status') as status:
        status.return_value = {
            'FutuAdapter': {
                'registered': True,
                'instantiated': True,
                'available': True,
                'message': 'Futu OpenD 连接成功',
            },
            'CryptoAdapter': {
                'registered': True,
                'instantiated': True,
                'available': True,
                'message': 'OKX REST 公共行情可用',
            },
        }

        report = FusionController().status_report()

    status.assert_called_once_with(probe=True, instantiate=True)
    assert report['components']['futu_adapter'] is True
    assert report['components']['crypto_adapter'] is True
    assert report['adapters']['FutuAdapter']['message'] == 'Futu OpenD 连接成功'


class _FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode('utf-8')


def test_crypto_adapter_uses_rest_fallback_when_cli_missing():
    from core.crypto_adapter import CryptoAdapter

    ticker_payload = {
        'code': '0',
        'data': [{
            'instId': 'BTC-USDT',
            'last': '64000.5',
            'open24h': '63000',
            'high24h': '65000',
            'low24h': '62000',
            'vol24h': '123.45',
        }],
    }
    candles_payload = {
        'code': '0',
        'data': [
            ['1781308800000', '63000', '65000', '62000', '64000', '10', '0', '0', '1'],
            ['1781222400000', '62000', '63500', '61500', '63000', '8', '0', '0', '1'],
        ],
    }

    def fake_urlopen(req, timeout=10):
        url = req.full_url
        if '/market/candles' in url:
            return _FakeResponse(candles_payload)
        return _FakeResponse(ticker_payload)

    missing_cli = MagicMock(returncode=1, stdout='', stderr='okx not found')
    with patch('core.crypto_adapter.subprocess.run', return_value=missing_cli):
        with patch('core.crypto_adapter.urlopen', side_effect=fake_urlopen):
            adapter = CryptoAdapter()
            assert adapter.available is True
            assert adapter.test_connection() == (True, 'OKX REST 公共行情可用')
            quote = adapter.fetch_quote('BTC.USDT')
            df = adapter.fetch_kline('BTC.USDT', count=2)

    assert quote['price'] == 64000.5
    assert quote['source'] == 'OKX_REST'
    assert df is not None
    assert list(df['close']) == [63000.0, 64000.0]
    assert df.attrs['source'] == 'OKX_REST'


def test_unified_runner_prefers_universe_manager_targets():
    from unified_runner import run

    class FakeUniverse:
        def get_symbols_by_market(self, market):
            assert market == 'HK'
            return ['09988.HK']

        def contains(self, symbol):
            return symbol == '09988.HK'

        def get_lot_size(self, symbol):
            assert symbol == '09988.HK'
            return 100

    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        sig_dir = tmp_root / 'paper_trading' / 'signals'
        report_dir = tmp_root / 'reports'

        with patch('unified_runner.BASE', tmp_root):
            with patch('unified_runner.UniverseManager', return_value=FakeUniverse()):
                with patch('reports.fusion_report_v3.SIGNAL_DIR', sig_dir):
                    with patch('reports.fusion_report_v3.REPORT_DIR', report_dir):
                        with patch('unified_runner.FutuAdapter') as MockFA:
                            inst = MockFA.return_value
                            inst.test_connection.return_value = (True, 'Mock OK')
                            inst.fetch_vix_data.return_value = {'vix': 15.0}

                            with patch('unified_runner.FusionController') as MockFC:
                                fc_inst = MockFC.return_value
                                fc_inst.analyze_ticker.return_value = _minimal_signal('09988.HK')

                                with patch('market_state.classifier.load_price_data', return_value=None):
                                    with patch('market_state.classifier.load_vix_data', return_value=None):
                                        with patch('sys.stdout', new=io.StringIO()):
                                            run(
                                                market='HK',
                                                dry_run=True,
                                                signal_only=True,
                                                no_stop=False,
                                                requested_live=False,
                                                live_confirmed=False,
                                            )

    calls = fc_inst.analyze_ticker.call_args_list
    assert len(calls) == 1
    assert calls[0].kwargs['ticker'] == '09988.HK'
