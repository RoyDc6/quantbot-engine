"""Fail-closed WorkBuddy entry point for the HK scheduled deployment."""

from __future__ import annotations

from typing import List

from .workbuddy_entry import run_scheduled_workbuddy_market


AUTOMATION_ID = "automation-1785736457372"


def main(argv: List[str] | None = None) -> int:
    return run_scheduled_workbuddy_market(AUTOMATION_ID, "HK", argv)


if __name__ == "__main__":
    raise SystemExit(main())
