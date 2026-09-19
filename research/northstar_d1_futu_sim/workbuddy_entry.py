"""WorkBuddy entry: deliver current Forward or recover it by manual validation."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from typing import Any

from .forward_runner import FORWARD_RECEIPTS, run_integrated
from .report import _latest_forward_receipt, build_report


REUSABLE_FORWARD_STATUSES = {"PASS", "ATTENTION_REQUIRED"}


def run(
    market: str,
    *,
    now: datetime | None = None,
    scheduled_delivery: bool = False,
) -> dict[str, Any]:
    market = market.upper()
    local_now = now or datetime.now().astimezone()
    prior_status = "MISSING"
    try:
        _, prior = _latest_forward_receipt(
            market,
            FORWARD_RECEIPTS,
            expected_local_date=local_now.date(),
        )
        prior_status = str(prior.get("status") or "UNKNOWN")
    except RuntimeError:
        prior = None

    recovery = None
    trigger_mode = "CURRENT_FORWARD_REUSED"
    if prior_status not in REUSABLE_FORWARD_STATUSES and not scheduled_delivery:
        trigger_mode = "MANUAL_VALIDATION_RECOVERY"
        recovery = run_integrated(market, manual_validation=True)
    elif scheduled_delivery:
        trigger_mode = "SCHEDULED_DELIVERY"

    report = build_report(market=market, now=local_now)
    return {
        **report,
        "trigger_mode": trigger_mode,
        "prior_forward_status": prior_status,
        "manual_validation": recovery,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", required=True, choices=("HK", "US", "hk", "us"))
    parser.add_argument("--scheduled-delivery", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run(args.market, scheduled_delivery=args.scheduled_delivery)
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAILED_CLOSED", "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
