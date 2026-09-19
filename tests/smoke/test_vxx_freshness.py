# -*- coding: utf-8 -*-
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import json
import pandas as pd

from core.futu_adapter import FutuAdapter
from market_state.classifier import MarketStateClassifier
from reports.fusion_report_v3 import _build_header
from unified_runner import (
    count_missing_us_sessions,
    finalize_vxx_freshness,
    guard_new_buys_by_vxx,
    latest_completed_us_session,
)


def _vxx_frame(end='2026-07-23', periods=60):
    dates = pd.bdate_range(end=end, periods=periods)
    return pd.DataFrame({
        'date': dates.strftime('%Y-%m-%d'),
        'open': range(20, 20 + periods),
        'high': range(21, 21 + periods),
        'low': range(19, 19 + periods),
        'close': [20.0 + i / 10 for i in range(periods)],
        'volume': [1000 + i for i in range(periods)],
    })


def test_latest_completed_us_session_before_and_after_close():
    eastern = ZoneInfo('America/New_York')
    before_close = datetime(2026, 7, 20, 9, 35, tzinfo=eastern)
    after_close = datetime(2026, 7, 20, 16, 20, tzinfo=eastern)
    assert latest_completed_us_session(before_close).isoformat() == '2026-07-17'
    assert latest_completed_us_session(after_close).isoformat() == '2026-07-20'


def test_missing_sessions_skips_weekend():
    assert count_missing_us_sessions('2026-07-17', '2026-07-20') == 1
    assert count_missing_us_sessions('2026-07-20', '2026-07-20') == 0


def test_futu_primary_filters_incomplete_bar_and_updates_cache(tmp_path):
    adapter = FutuAdapter()
    adapter.fetch_kline = lambda *args, **kwargs: _vxx_frame()
    cache = tmp_path / 'VXX_US.json'

    values = adapter.fetch_vix_data(
        cache_path=str(cache),
        expected_as_of='2026-07-22',
    )

    assert adapter.last_vxx_meta['source'] == 'FUTU'
    assert adapter.last_vxx_meta['as_of'] == '2026-07-22'
    assert max(values) == '2026-07-22'
    cached = json.loads(cache.read_text(encoding='utf-8'))
    assert len(cached) == len(values)
    assert 'close' in cached[-1]


def test_cache_fallback_reports_source_and_as_of(tmp_path):
    cache = tmp_path / 'VXX_US.json'
    seed = FutuAdapter()
    seed._write_vxx_cache_atomic(
        _vxx_frame(end='2026-07-17', periods=60).assign(
            date=lambda d: pd.to_datetime(d['date'])
        ),
        cache,
    )

    adapter = FutuAdapter()
    adapter.fetch_kline = lambda *args, **kwargs: None
    adapter.fetch_vix_data(
        cache_path=str(cache),
        expected_as_of='2026-07-20',
    )
    meta = finalize_vxx_freshness(
        adapter.last_vxx_meta,
        '2026-07-20',
    )

    assert meta['source'] == 'CACHE'
    assert meta['as_of'] == '2026-07-17'
    assert meta['stale_sessions'] == 1
    assert meta['freshness'] == 'STALE'
    assert adapter.last_vxx_meta['fetch_error'] == 'Futu VXX 日线无可用数据'


def test_cache_fallback_filters_incomplete_future_bar(tmp_path):
    cache = tmp_path / 'VXX_US.json'
    seed = FutuAdapter()
    seed._write_vxx_cache_atomic(
        _vxx_frame(end='2026-07-23', periods=60).assign(
            date=lambda d: pd.to_datetime(d['date'])
        ),
        cache,
    )
    adapter = FutuAdapter()
    adapter.fetch_kline = lambda *args, **kwargs: None
    values = adapter.fetch_vix_data(
        cache_path=str(cache),
        expected_as_of='2026-07-22',
    )
    assert adapter.last_vxx_meta['as_of'] == '2026-07-22'
    assert max(values) == '2026-07-22'


def test_stale_vxx_blocks_only_new_buys():
    signal = {'symbol': 'AAPL.US', 'warnings': []}
    stale = {
        'source': 'CACHE',
        'as_of': '2026-07-17',
        'expected_as_of': '2026-07-20',
        'freshness': 'STALE',
    }
    assert guard_new_buys_by_vxx([signal], stale) == []
    assert 'VXX freshness guard' in signal['warnings'][0]
    assert guard_new_buys_by_vxx([signal], {**stale, 'freshness': 'OK'}) == [signal]


def test_report_header_exposes_vxx_provenance():
    header = _build_header(
        '2026-07-23', 'US', 'BULL', 'RELEASED', 'BULL', 'NEUTRAL',
        0.8,
        {
            'vxx_source': 'FUTU',
            'vxx_as_of': '2026-07-22',
            'vxx_freshness': 'OK',
            'vix_detail': {'vxx_price': 21.55},
        },
    )
    assert 'VXX: RELEASED' in header
    assert '21.55 · OK' in header
    assert 'FUTU · as-of 2026-07-22' in header


def test_classifier_keeps_original_252_bar_window():
    frame = _vxx_frame(periods=300).assign(
        date=lambda d: pd.to_datetime(d['date'])
    )
    classifier = MarketStateClassifier('SPY.US', vix_df=frame)
    assert len(classifier.vix_df) == 252
