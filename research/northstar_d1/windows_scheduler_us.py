"""Windows Task Scheduler entry for Northstar-D1 US."""

from __future__ import annotations

from .windows_scheduler_entry import run_windows_scheduled_market


def main(argv=None) -> int:
    return run_windows_scheduled_market("US", argv)


if __name__ == "__main__":
    raise SystemExit(main())
