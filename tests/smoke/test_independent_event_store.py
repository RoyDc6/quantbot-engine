from datetime import datetime, timedelta, timezone

import pytest

from research.events import IndependentEvent, JsonlEventStore


UTC = timezone.utc


def _event(event_id: str = "evt-1", symbol: str = "AAPL.US") -> IndependentEvent:
    published = datetime(2026, 7, 6, 1, 0, tzinfo=UTC)
    return IndependentEvent(
        event_id=event_id,
        symbol=symbol,
        published_at=published,
        available_at=published + timedelta(minutes=2),
        source="exchange-filing",
        event_type="announcement",
        direction="BULLISH",
        confidence=0.8,
        novelty=0.9,
        expires_at=published + timedelta(hours=24),
        headline="Audited filing released",
        source_url="https://example.test/filing/evt-1",
        source_record_id="filing-evt-1",
    )


def test_event_store_round_trip_and_symbol_filter(tmp_path):
    store = JsonlEventStore(tmp_path / "events.jsonl")
    store.append(_event())
    store.append(_event("evt-2", "00700.HK"))

    loaded = store.load(symbol="aapl.us")

    assert len(loaded) == 1
    assert loaded[0].symbol == "AAPL.US"
    assert loaded[0].available_at.isoformat() == "2026-07-06T01:02:00+00:00"


def test_as_of_filter_blocks_lookahead_and_expired_events(tmp_path):
    store = JsonlEventStore(tmp_path / "events.jsonl")
    store.append(_event())

    assert store.load(as_of="2026-07-06T01:01:00+00:00") == []
    assert len(store.load(as_of="2026-07-06T01:03:00+00:00")) == 1
    assert store.load(as_of="2026-07-07T01:00:00+00:00") == []


def test_quality_thresholds_and_duplicate_ids(tmp_path):
    store = JsonlEventStore(tmp_path / "events.jsonl")
    event = _event()
    store.append(event)

    assert len(
        store.load(
            as_of="2026-07-06T02:00:00+00:00",
            min_confidence=0.75,
            min_novelty=0.85,
        )
    ) == 1
    assert store.load(
        as_of="2026-07-06T02:00:00+00:00",
        min_confidence=0.9,
    ) == []
    with pytest.raises(ValueError, match="duplicate event_id"):
        store.append(event)


def test_event_rejects_naive_or_impossible_timestamps():
    published = datetime(2026, 7, 6, 1, 0)
    with pytest.raises(ValueError, match="timezone-aware"):
        IndependentEvent(
            event_id="bad-1",
            symbol="AAPL.US",
            published_at=published,
            available_at=published,
            source="test",
            event_type="news",
            direction="NEUTRAL",
            confidence=0.5,
            novelty=0.5,
            expires_at=published + timedelta(hours=1),
        )

    aware = published.replace(tzinfo=UTC)
    with pytest.raises(ValueError, match="available_at cannot precede"):
        IndependentEvent(
            event_id="bad-2",
            symbol="AAPL.US",
            published_at=aware,
            available_at=aware - timedelta(minutes=1),
            source="test",
            event_type="news",
            direction="NEUTRAL",
            confidence=0.5,
            novelty=0.5,
            expires_at=aware + timedelta(hours=1),
        )
