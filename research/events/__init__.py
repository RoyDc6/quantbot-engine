"""Independent, timestamp-safe event research primitives.

This package is intentionally disconnected from FusionController, HardGate,
account access, positions, and order execution.
"""

from .independent_event_store import IndependentEvent, JsonlEventStore

__all__ = ["IndependentEvent", "JsonlEventStore"]
