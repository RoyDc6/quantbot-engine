from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from research.northstar_d1 import windows_scheduler_entry
from research.northstar_d1.runner import deployment_for
from research.northstar_d1.runtime import RunCoordinator


def test_latest_due_slot_uses_prior_weekday_before_hk_monday_gate():
    observed = datetime(2026, 8, 17, 1, 0, tzinfo=timezone.utc)

    slot = windows_scheduler_entry.latest_due_slot("HK", observed)

    assert slot.isoformat() == "2026-08-14T16:20:00+08:00"


def test_latest_due_slot_uses_current_us_slot_after_gate():
    observed = datetime(2026, 8, 19, 2, 7, tzinfo=timezone.utc)

    slot = windows_scheduler_entry.latest_due_slot("US", observed)

    assert slot.isoformat() == "2026-08-19T10:00:00+08:00"


def test_injects_missed_recovery_and_restores_environment(monkeypatch):
    observed = {}

    def fake_main_for_market(market, argv):
        observed.update(
            {
                "market": market,
                "argv": argv,
                "source": os.environ.get("NORTHSTAR_INVOCATION_SOURCE"),
                "run_kind": os.environ.get("NORTHSTAR_INVOCATION_RUN_KIND"),
                "scheduled_at": os.environ.get(
                    "NORTHSTAR_ORIGIN_SCHEDULED_AT_LOCAL"
                ),
                "task": os.environ.get("NORTHSTAR_SCHEDULER_TASK_NAME"),
                "contract_hash": os.environ.get(
                    "NORTHSTAR_SCHEDULER_CONTRACT_SHA256"
                ),
            }
        )
        return 0

    monkeypatch.setattr(
        windows_scheduler_entry,
        "main_for_market",
        fake_main_for_market,
    )
    result = windows_scheduler_entry.run_windows_scheduled_market(
        "US",
        ["--no-write"],
        observed_at=datetime(2026, 8, 19, 3, 0, tzinfo=timezone.utc),
    )

    assert result == 0
    assert observed == {
        "market": "US",
        "argv": ["--no-write"],
        "source": "WINDOWS_TASK_SCHEDULER",
        "run_kind": "MISSED_RECOVERY",
        "scheduled_at": "2026-08-19T10:00:00+08:00",
        "task": r"\NorthstarD1-US",
        "contract_hash": windows_scheduler_entry.scheduler_contract_sha256("US"),
    }
    for key in windows_scheduler_entry.SCHEDULER_ENV_KEYS:
        assert key not in os.environ


def test_injects_scheduled_within_grace(monkeypatch):
    observed = {}

    def fake_main_for_market(market, argv):
        observed["run_kind"] = os.environ.get("NORTHSTAR_INVOCATION_RUN_KIND")
        return 0

    monkeypatch.setattr(
        windows_scheduler_entry,
        "main_for_market",
        fake_main_for_market,
    )
    result = windows_scheduler_entry.run_windows_scheduled_market(
        "HK",
        [],
        observed_at=datetime(2026, 8, 19, 8, 21, tzinfo=timezone.utc),
    )

    assert result == 0
    assert observed == {"run_kind": "SCHEDULED"}


def test_runtime_uses_missed_origin_slot_instead_of_current_date(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setenv(
        "NORTHSTAR_ORIGIN_SCHEDULED_AT_LOCAL",
        "2026-08-14T16:20:00+08:00",
    )
    monkeypatch.setenv("NORTHSTAR_INVOCATION_SOURCE", "WINDOWS_TASK_SCHEDULER")
    monkeypatch.setenv("NORTHSTAR_INVOCATION_RUN_KIND", "MISSED_RECOVERY")
    monkeypatch.setenv("NORTHSTAR_SCHEDULER_TASK_NAME", r"\NorthstarD1-HK")
    coordinator = RunCoordinator(
        market="HK",
        deployment=deployment_for("HK"),
        output_root=tmp_path,
        now=datetime(2026, 8, 17, 1, 0, tzinfo=timezone.utc),
    )

    decision = coordinator.acquire()
    metadata = coordinator.metadata()

    assert decision.slot_key == "HK:2026-08-14:16:20:Asia/Hong_Kong"
    assert metadata["scheduled_at_local"] == "2026-08-14T16:20:00+08:00"
    assert metadata["invocation_source"] == "WINDOWS_TASK_SCHEDULER"
    assert metadata["invocation_run_kind"] == "MISSED_RECOVERY"
    assert metadata["scheduler_task_name"] == r"\NorthstarD1-HK"


def test_runtime_rejects_spoofed_non_schedule_origin(monkeypatch, tmp_path: Path):
    monkeypatch.setenv(
        "NORTHSTAR_ORIGIN_SCHEDULED_AT_LOCAL",
        "2026-08-17T16:21:00+08:00",
    )

    try:
        RunCoordinator(
            market="HK",
            deployment=deployment_for("HK"),
            output_root=tmp_path,
            now=datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc),
        )
    except ValueError as exc:
        assert "violates the deployment schedule" in str(exc)
    else:
        raise AssertionError("non-schedule origin must fail closed")
