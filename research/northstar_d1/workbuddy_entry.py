"""Shared fail-closed entry logic for natural WorkBuddy scheduler runs."""

from __future__ import annotations

import json
import os
import sys
from typing import Dict, List

from .runner import main_for_market
from .workbuddy_provenance import resolve_workbuddy_provenance


ALLOWED_RUN_KINDS = frozenset(
    {"scheduled", "missed", "manual_test", "on_demand_followup"}
)
PROVENANCE_ENV_KEYS = (
    "NORTHSTAR_INVOCATION_SOURCE",
    "NORTHSTAR_AUTOMATION_ID",
    "NORTHSTAR_AUTOMATION_RUN_ID",
    "NORTHSTAR_AUTOMATION_CONVERSATION_ID",
    "NORTHSTAR_AUTOMATION_RUN_KIND",
)


def _failure(
    deployment_id: str,
    automation_id: str,
    reason: str,
    provenance: Dict,
) -> int:
    payload = {
        "deployment_id": deployment_id,
        "status": "FAILED_CLOSED",
        "reason": reason,
        "provenance_status": provenance.get("provenance_status"),
        "provenance_reason": provenance.get("reason"),
        "requested_automation_id": automation_id,
        "futu_called": False,
        "model_called": False,
    }
    print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
    return 4


def run_scheduled_workbuddy_market(
    automation_id: str,
    market: str,
    argv: List[str] | None = None,
) -> int:
    """Verify one natural scheduler run, inject identity, then run once."""

    market = market.upper()
    deployment_id = f"NORTHSTAR_D1_{market}"
    provenance = resolve_workbuddy_provenance(automation_id)
    if provenance.get("provenance_status") != "VERIFIED":
        return _failure(
            deployment_id,
            automation_id,
            "WORKBUDDY_PROVENANCE_NOT_VERIFIED",
            provenance,
        )
    expected_identity = {
        "automation_id": automation_id,
        "deployment_id": deployment_id,
        "market": market,
    }
    if any(provenance.get(key) != value for key, value in expected_identity.items()):
        return _failure(
            deployment_id,
            automation_id,
            "WORKBUDDY_DEPLOYMENT_IDENTITY_MISMATCH",
            provenance,
        )
    if provenance.get("automation_run_kind") not in ALLOWED_RUN_KINDS:
        return _failure(
            deployment_id,
            automation_id,
            "WORKBUDDY_RUN_KIND_NOT_ALLOWED",
            provenance,
        )

    injected = {
        "NORTHSTAR_INVOCATION_SOURCE": "WORKBUDDY_AUTOMATION",
        "NORTHSTAR_AUTOMATION_ID": automation_id,
        "NORTHSTAR_AUTOMATION_RUN_ID": str(provenance["automation_run_id"]),
        "NORTHSTAR_AUTOMATION_CONVERSATION_ID": str(
            provenance["automation_conversation_id"]
        ),
        "NORTHSTAR_AUTOMATION_RUN_KIND": str(provenance["automation_run_kind"]),
    }
    previous = {key: os.environ.get(key) for key in PROVENANCE_ENV_KEYS}
    try:
        os.environ.update(injected)
        print(
            f"[{deployment_id}] WORKBUDDY_PROVENANCE VERIFIED "
            f"automation_id={automation_id} "
            f"automation_run_id={injected['NORTHSTAR_AUTOMATION_RUN_ID']} "
            f"run_kind={injected['NORTHSTAR_AUTOMATION_RUN_KIND']}"
        )
        return main_for_market(market, argv)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
