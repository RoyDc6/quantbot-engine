"""Controlled Windows Task Scheduler entry for Northstar-D1.

The operating-system scheduler is the timing authority.  WorkBuddy is not
consulted and is not part of the execution or provenance path.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List
from zoneinfo import ZoneInfo

from .runner import deployment_for, main_for_market


SCHEDULED_GRACE = timedelta(minutes=5)
WINDOWS_TASK_CONTRACTS = {
    "HK": {
        "task_name": r"\NorthstarD1-HK",
        "weekdays": (0, 1, 2, 3, 4),
        "start_when_available": True,
        "multiple_instances": "IGNORE_NEW",
    },
    "US": {
        "task_name": r"\NorthstarD1-US",
        "weekdays": (1, 2, 3, 4, 5),
        "start_when_available": True,
        "multiple_instances": "IGNORE_NEW",
    },
}
SCHEDULER_ENV_KEYS = (
    "NORTHSTAR_INVOCATION_SOURCE",
    "NORTHSTAR_INVOCATION_RUN_KIND",
    "NORTHSTAR_ORIGIN_SCHEDULED_AT_LOCAL",
    "NORTHSTAR_SCHEDULER_TASK_NAME",
    "NORTHSTAR_SCHEDULER_CONTRACT_SHA256",
)


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def latest_due_slot(market: str, observed_at: datetime | None = None) -> datetime:
    """Return the newest configured schedule slot that is not in the future."""

    market = market.upper()
    deployment = deployment_for(market)
    contract = WINDOWS_TASK_CONTRACTS[market]
    schedule_zone = ZoneInfo(str(deployment["schedule_timezone"]))
    local_now = _utc(observed_at).astimezone(schedule_zone)
    candidate = local_now.replace(
        hour=int(deployment["schedule_hour"]),
        minute=int(deployment["schedule_minute"]),
        second=0,
        microsecond=0,
    )
    while candidate > local_now or candidate.weekday() not in contract["weekdays"]:
        candidate = (candidate - timedelta(days=1)).replace(
            hour=int(deployment["schedule_hour"]),
            minute=int(deployment["schedule_minute"]),
            second=0,
            microsecond=0,
        )
    return candidate


def scheduler_contract(market: str) -> Dict[str, object]:
    market = market.upper()
    deployment = deployment_for(market)
    task_contract = WINDOWS_TASK_CONTRACTS[market]
    return {
        "market": market,
        "task_name": task_contract["task_name"],
        "schedule_timezone": deployment["schedule_timezone"],
        "schedule_hour": deployment["schedule_hour"],
        "schedule_minute": deployment["schedule_minute"],
        "weekdays": list(task_contract["weekdays"]),
        "start_when_available": task_contract["start_when_available"],
        "multiple_instances": task_contract["multiple_instances"],
    }


def scheduler_contract_sha256(market: str) -> str:
    encoded = json.dumps(
        scheduler_contract(market),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()


def run_windows_scheduled_market(
    market: str,
    argv: List[str] | None = None,
    *,
    observed_at: datetime | None = None,
) -> int:
    """Inject truthful OS-scheduler provenance and invoke one market once."""

    market = market.upper()
    current = _utc(observed_at)
    scheduled_at_local = latest_due_slot(market, current)
    lag = current - scheduled_at_local.astimezone(timezone.utc)
    run_kind = "SCHEDULED" if lag <= SCHEDULED_GRACE else "MISSED_RECOVERY"
    injected = {
        "NORTHSTAR_INVOCATION_SOURCE": "WINDOWS_TASK_SCHEDULER",
        "NORTHSTAR_INVOCATION_RUN_KIND": run_kind,
        "NORTHSTAR_ORIGIN_SCHEDULED_AT_LOCAL": scheduled_at_local.isoformat(),
        "NORTHSTAR_SCHEDULER_TASK_NAME": WINDOWS_TASK_CONTRACTS[market][
            "task_name"
        ],
        "NORTHSTAR_SCHEDULER_CONTRACT_SHA256": scheduler_contract_sha256(market),
    }
    previous = {key: os.environ.get(key) for key in SCHEDULER_ENV_KEYS}
    try:
        os.environ.update(injected)
        print(
            f"[NORTHSTAR_D1_{market}] WINDOWS_SCHEDULER_PROVENANCE "
            f"task={injected['NORTHSTAR_SCHEDULER_TASK_NAME']} "
            f"run_kind={run_kind} "
            f"origin_scheduled_at={scheduled_at_local.isoformat()}"
        )
        return main_for_market(market, argv)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
