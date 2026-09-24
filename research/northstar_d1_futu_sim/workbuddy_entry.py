"""WorkBuddy entry: deliver current Forward or recover it by manual validation."""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from typing import Any

from .forward_runner import FORWARD_RECEIPTS, _replace_forward_receipt, run_integrated
from .report import REPORTS, _latest_forward_receipt, build_report
from .runner import _sha256, refresh_execution_receipt


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
    prior_path = None
    try:
        prior_path, prior = _latest_forward_receipt(
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

    # Delivery is separated from strategy execution, but it must not publish a
    # stale SUBMITTED/FILLED_PART snapshot.  Re-read broker terminal state and
    # account/positions through the reconciliation-only path.  This path never
    # submits, amends, or cancels an order.
    prior_path, current = _latest_forward_receipt(
        market,
        FORWARD_RECEIPTS,
        expected_local_date=local_now.date(),
    )
    delivery_reconciliation = None
    previous_report = current.get("report")
    execution_receipt = str(current.get("execution_receipt") or "")
    if current.get("status") in REUSABLE_FORWARD_STATUSES and execution_receipt:
        refreshed = refresh_execution_receipt(market, execution_receipt)
        current = dict(current)
        current["execution_receipt"] = refreshed["receipt_path"]
        current["reconciliation"] = refreshed["reconciliation"]
        current["status"] = (
            "PASS" if refreshed["reconciliation"] == "PASS" else "ATTENTION_REQUIRED"
        )
        delivery_reconciliation = {
            "mode": "RECONCILIATION_ONLY_NO_BROKER_MUTATION",
            "receipt_path": refreshed["receipt_path"],
            "source_execution_receipt": refreshed["source_execution_receipt"],
            "order_reconciliation": refreshed["order_reconciliation"],
            "account_risk_flags": refreshed["account_risk_flags"],
            "refreshed_at_utc": refreshed["reconciliation_refreshed_at_utc"],
        }
        current["delivery_reconciliation"] = delivery_reconciliation
        # Once the evidence changes, the old report reference is no longer a
        # valid description of this Forward receipt.  Fail closed if rendering
        # stops between the refreshed receipt and the final report link.
        current["report"] = None
        _replace_forward_receipt(prior_path, current)

    try:
        report = build_report(market=market, now=local_now)
        if report.get("status") != "REPORT_READY" or report.get("markets") != [market]:
            raise RuntimeError("delivery report identity mismatch")
        report_path = Path(str(report["report_path"])).resolve()
        report_path.relative_to(REPORTS.resolve())
        if _sha256(report_path) != report["sha256"]:
            raise RuntimeError("delivery report SHA256 mismatch")
    except Exception as exc:
        current["report_error"] = str(exc)
        _replace_forward_receipt(prior_path, current)
        raise
    if isinstance(previous_report, dict) and previous_report != report:
        prior_reference = dict(previous_report)
        old_path = Path(str(prior_reference.get("report_path") or ""))
        try:
            old_path = old_path.resolve()
            old_path.relative_to(REPORTS.resolve())
            prior_reference["verified_at_rotation"] = (
                old_path.is_file()
                and _sha256(old_path) == prior_reference.get("sha256")
            )
        except (OSError, ValueError):
            prior_reference["verified_at_rotation"] = False
        current.setdefault("report_history", []).append(prior_reference)
    current["report"] = report
    current["report_error"] = None
    _replace_forward_receipt(prior_path, current)
    return {
        **report,
        "forward_receipt_path": str(prior_path),
        "trigger_mode": trigger_mode,
        "prior_forward_status": prior_status,
        "delivery_forward_status": current.get("status"),
        "delivery_reconciliation": delivery_reconciliation,
        "manual_validation": recovery,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", required=True, choices=("HK", "US", "hk", "us"))
    parser.add_argument("--scheduled-delivery", action="store_true")
    args = parser.parse_args(argv)
    try:
        # Futu's SDK writes connection diagnostics to stdout.  Keep stdout as
        # the machine-readable one-JSON contract and route SDK chatter to
        # stderr for the delivery agent's diagnostic log.
        with redirect_stdout(sys.stderr):
            result = run(args.market, scheduled_delivery=args.scheduled_delivery)
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAILED_CLOSED", "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
