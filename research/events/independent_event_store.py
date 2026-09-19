"""Timestamp-safe event records for Shadow B research.

The store captures independent news/filing/earnings events without wiring them
into production fusion. Historical replay must use ``available_at`` rather than
``published_at`` so an event cannot enter a signal before the system could have
observed it.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional


ALLOWED_EVENT_TYPES = {
    "announcement",
    "earnings",
    "guidance",
    "regulatory",
    "corporate_action",
    "macro",
    "news",
}
ALLOWED_DIRECTIONS = {"BULLISH", "BEARISH", "NEUTRAL"}


def _aware_datetime(value: datetime | str, field_name: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(parsed, datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed


@dataclass(frozen=True)
class IndependentEvent:
    event_id: str
    symbol: str
    published_at: datetime
    available_at: datetime
    source: str
    event_type: str
    direction: str
    confidence: float
    novelty: float
    expires_at: datetime
    headline: str = ""
    source_url: str = ""
    source_record_id: str = ""
    schema_version: int = 1

    def __post_init__(self) -> None:
        published_at = _aware_datetime(self.published_at, "published_at")
        available_at = _aware_datetime(self.available_at, "available_at")
        expires_at = _aware_datetime(self.expires_at, "expires_at")
        object.__setattr__(self, "published_at", published_at)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "expires_at", expires_at)
        object.__setattr__(self, "symbol", self.symbol.strip().upper())
        object.__setattr__(self, "event_type", self.event_type.strip().lower())
        object.__setattr__(self, "direction", self.direction.strip().upper())

        for field_name in ("event_id", "symbol", "source"):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} must not be empty")
        if self.event_type not in ALLOWED_EVENT_TYPES:
            raise ValueError(f"unsupported event_type: {self.event_type}")
        if self.direction not in ALLOWED_DIRECTIONS:
            raise ValueError(f"unsupported direction: {self.direction}")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence must be within [0, 1]")
        if not 0.0 <= float(self.novelty) <= 1.0:
            raise ValueError("novelty must be within [0, 1]")
        if available_at < published_at:
            raise ValueError("available_at cannot precede published_at")
        if expires_at <= available_at:
            raise ValueError("expires_at must be later than available_at")
        if self.schema_version != 1:
            raise ValueError("unsupported schema_version")

    def is_eligible(
        self,
        as_of: datetime | str,
        *,
        min_confidence: float = 0.0,
        min_novelty: float = 0.0,
    ) -> bool:
        timestamp = _aware_datetime(as_of, "as_of")
        return (
            self.available_at <= timestamp < self.expires_at
            and self.confidence >= min_confidence
            and self.novelty >= min_novelty
        )

    def to_dict(self) -> dict:
        payload = asdict(self)
        for field_name in ("published_at", "available_at", "expires_at"):
            payload[field_name] = payload[field_name].isoformat()
        return payload

    @classmethod
    def from_dict(cls, payload: dict) -> "IndependentEvent":
        return cls(**payload)


class JsonlEventStore:
    """Append-only JSONL store for research events.

    It performs no network access and has no production-fusion dependency.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(
        self,
        *,
        symbol: Optional[str] = None,
        as_of: datetime | str | None = None,
        min_confidence: float = 0.0,
        min_novelty: float = 0.0,
    ) -> list[IndependentEvent]:
        if not self.path.exists():
            return []
        normalized_symbol = symbol.strip().upper() if symbol else None
        events: list[IndependentEvent] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    event = IndependentEvent.from_dict(json.loads(line))
                except (json.JSONDecodeError, TypeError, ValueError) as exc:
                    raise ValueError(f"invalid event record at line {line_number}: {exc}") from exc
                if normalized_symbol and event.symbol != normalized_symbol:
                    continue
                if as_of is not None and not event.is_eligible(
                    as_of,
                    min_confidence=min_confidence,
                    min_novelty=min_novelty,
                ):
                    continue
                events.append(event)
        return events

    def append(self, event: IndependentEvent) -> None:
        existing_ids = {item.event_id for item in self.load()}
        if event.event_id in existing_ids:
            raise ValueError(f"duplicate event_id: {event.event_id}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")

    def append_many(self, events: Iterable[IndependentEvent]) -> None:
        for event in events:
            self.append(event)
