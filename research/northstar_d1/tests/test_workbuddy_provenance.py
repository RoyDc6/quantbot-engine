from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from research.northstar_d1.workbuddy_provenance import (
    main,
    resolve_workbuddy_provenance,
)


HK_AUTOMATION_ID = "automation-1785736457372"


def _database(
    tmp_path,
    *,
    running: bool = True,
    command_matches: bool = True,
    run_kind: str = "scheduled",
    run_status: str = "RUNNING",
    session_status: str = "working",
):
    path = tmp_path / "workbuddy.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE automations (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            cwds TEXT NOT NULL,
            prompt TEXT NOT NULL,
            deleted_at INTEGER,
            updated_at INTEGER NOT NULL
        );
        CREATE TABLE automation_runs (
            thread_id TEXT PRIMARY KEY,
            automation_id TEXT NOT NULL,
            status TEXT NOT NULL,
            source_cwd TEXT,
            runs_json TEXT,
            metadata_json TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE TABLE automation_runtime_state (
            automation_id TEXT PRIMARY KEY,
            running INTEGER NOT NULL,
            running_started_at INTEGER,
            running_conversation_id TEXT
        );
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            cwd TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            last_activity_at INTEGER,
            is_background_automation INTEGER NOT NULL,
            deleted_at INTEGER
        );
        """
    )
    observed = datetime(2026, 8, 4, 8, 21, tzinfo=timezone.utc)
    started_ms = int((observed - timedelta(minutes=1)).timestamp() * 1000)
    conversation_id = "conversation-hk-current"
    command = (
        "python -m research.northstar_d1.workbuddy_hk"
        if command_matches
        else "python -m research.northstar_d1.us"
    )
    connection.execute(
        "INSERT INTO automations VALUES (?, 'ACTIVE', ?, ?, NULL, ?)",
        (HK_AUTOMATION_ID, json.dumps([r"E:\quant"]), command, started_ms - 1),
    )
    connection.execute(
        "INSERT INTO automation_runtime_state VALUES (?, ?, ?, ?)",
        (HK_AUTOMATION_ID, int(running), started_ms, conversation_id),
    )
    metadata = {"runKind": run_kind}
    if run_kind == "missed":
        metadata["missedScheduledAt"] = started_ms
    connection.execute(
        "INSERT INTO automation_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "run-verified-hk",
            HK_AUTOMATION_ID,
            run_status,
            r"E:\quant",
            json.dumps(
                [
                    {
                        "cwd": r"E:\quant",
                        "conversationId": conversation_id,
                        "startedAt": started_ms,
                    }
                ]
            ),
            json.dumps(metadata),
            started_ms,
            started_ms + 500,
        ),
    )
    connection.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, 1, NULL)",
        (
            conversation_id,
            r"E:\quant",
            session_status,
            started_ms + 5,
            int((observed - timedelta(seconds=1)).timestamp() * 1000),
        ),
    )
    connection.commit()
    connection.close()
    return path, observed


def test_us_contract_uses_strict_workbuddy_entry():
    from research.northstar_d1.workbuddy_provenance import AUTOMATION_CONTRACTS

    assert (
        AUTOMATION_CONTRACTS["automation-1785736457815"]["command_token"]
        == "research.northstar_d1.workbuddy_us"
    )


def test_resolves_unique_active_workbuddy_run(tmp_path):
    database, observed = _database(tmp_path)

    result = resolve_workbuddy_provenance(
        HK_AUTOMATION_ID,
        database_path=database,
        observed_at=observed,
    )

    assert result["provenance_status"] == "VERIFIED"
    assert result["invocation_source"] == "WORKBUDDY_AUTOMATION"
    assert result["automation_id"] == HK_AUTOMATION_ID
    assert result["automation_run_id"] == "run-verified-hk"
    assert result["automation_conversation_id"] == "conversation-hk-current"
    assert result["automation_run_kind"] == "scheduled"
    assert result["deployment_id"] == "NORTHSTAR_D1_HK"


def test_resolves_verified_missed_recovery_run(tmp_path):
    database, observed = _database(tmp_path, run_kind="missed")

    result = resolve_workbuddy_provenance(
        HK_AUTOMATION_ID,
        database_path=database,
        observed_at=observed,
    )

    assert result["provenance_status"] == "VERIFIED"
    assert result["automation_run_kind"] == "missed"


def test_resolves_active_manual_test_run_without_scheduler_runtime(tmp_path):
    database, observed = _database(
        tmp_path,
        running=False,
        run_kind="manual_test",
        run_status="IN_PROGRESS",
    )

    result = resolve_workbuddy_provenance(
        HK_AUTOMATION_ID,
        database_path=database,
        observed_at=observed,
    )

    assert result["provenance_status"] == "VERIFIED"
    assert result["automation_run_kind"] == "manual_test"
    assert result["automation_identity_mode"] == "ACTIVE_MANUAL_RUN"


def test_resolves_active_manual_test_before_runs_json_is_populated(tmp_path):
    database, observed = _database(
        tmp_path,
        running=False,
        run_kind="manual_test",
        run_status="IN_PROGRESS",
    )
    connection = sqlite3.connect(database)
    connection.execute("UPDATE automation_runs SET runs_json = '[]'")
    connection.commit()
    connection.close()

    result = resolve_workbuddy_provenance(
        HK_AUTOMATION_ID,
        database_path=database,
        observed_at=observed,
    )

    assert result["provenance_status"] == "VERIFIED"
    assert result["automation_run_kind"] == "manual_test"
    assert result["automation_conversation_id"] == "conversation-hk-current"
    assert result["automation_identity_mode"] == "ACTIVE_MANUAL_RUN"
    assert result["automation_conversation_link_mode"] == "CREATED_AT_PAIR"


def test_rejects_ambiguous_manual_test_timestamp_pair(tmp_path):
    database, observed = _database(
        tmp_path,
        running=False,
        run_kind="manual_test",
        run_status="IN_PROGRESS",
    )
    connection = sqlite3.connect(database)
    created_at = connection.execute(
        "SELECT created_at FROM automation_runs"
    ).fetchone()[0]
    connection.execute("UPDATE automation_runs SET runs_json = '[]'")
    connection.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, 1, NULL)",
        (
            "conversation-hk-ambiguous",
            r"E:\quant",
            "working",
            created_at + 10,
            int((observed - timedelta(seconds=1)).timestamp() * 1000),
        ),
    )
    connection.commit()
    connection.close()

    result = resolve_workbuddy_provenance(
        HK_AUTOMATION_ID,
        database_path=database,
        observed_at=observed,
    )

    assert result["provenance_status"] == "NOT_PROVIDED"
    assert result["reason"] == "AUTOMATION_ON_DEMAND_RECORD_NOT_UNIQUE"


def test_resolves_active_followup_in_linked_automation_session(tmp_path):
    database, observed = _database(
        tmp_path,
        running=False,
        run_kind="missed",
        run_status="PENDING_REVIEW",
    )
    followup_observed = observed + timedelta(days=1)
    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE sessions SET last_activity_at = ?",
        (int((followup_observed - timedelta(seconds=1)).timestamp() * 1000),),
    )
    connection.commit()
    connection.close()

    result = resolve_workbuddy_provenance(
        HK_AUTOMATION_ID,
        database_path=database,
        observed_at=followup_observed,
        max_run_age=timedelta(days=2),
    )

    assert result["provenance_status"] == "VERIFIED"
    assert result["automation_run_kind"] == "on_demand_followup"
    assert result["automation_origin_run_kind"] == "missed"
    assert result["automation_identity_mode"] == "ACTIVE_AUTOMATION_SESSION"


def test_rejects_completed_followup_session(tmp_path):
    database, observed = _database(
        tmp_path,
        running=False,
        run_kind="manual_test",
        run_status="PENDING_REVIEW",
        session_status="completed",
    )

    result = resolve_workbuddy_provenance(
        HK_AUTOMATION_ID,
        database_path=database,
        observed_at=observed,
    )

    assert result["provenance_status"] == "NOT_PROVIDED"
    assert result["reason"] == "AUTOMATION_NOT_CURRENTLY_RUNNING"


def test_rejects_stale_missed_recovery_from_previous_local_day(tmp_path):
    database, observed = _database(tmp_path, run_kind="missed")
    connection = sqlite3.connect(database)
    previous_day_ms = int((observed - timedelta(days=1)).timestamp() * 1000)
    connection.execute(
        "UPDATE automation_runs SET metadata_json = ?",
        (
            json.dumps(
                {"runKind": "missed", "missedScheduledAt": previous_day_ms}
            ),
        ),
    )
    connection.commit()
    connection.close()

    result = resolve_workbuddy_provenance(
        HK_AUTOMATION_ID,
        database_path=database,
        observed_at=observed,
    )

    assert result["provenance_status"] == "NOT_PROVIDED"
    assert result["reason"] == "AUTOMATION_MISSED_SCHEDULE_STALE"


def test_fails_closed_when_automation_is_not_running(tmp_path):
    database, observed = _database(tmp_path, running=False)

    result = resolve_workbuddy_provenance(
        HK_AUTOMATION_ID,
        database_path=database,
        observed_at=observed,
    )

    assert result["provenance_status"] == "NOT_PROVIDED"
    assert result["reason"] == "AUTOMATION_NOT_CURRENTLY_RUNNING"
    assert result["automation_id"] is None
    assert result["automation_run_id"] is None


def test_fails_closed_when_market_command_does_not_match(tmp_path):
    database, observed = _database(tmp_path, command_matches=False)

    result = resolve_workbuddy_provenance(
        HK_AUTOMATION_ID,
        database_path=database,
        observed_at=observed,
    )

    assert result["provenance_status"] == "NOT_PROVIDED"
    assert result["reason"] == "AUTOMATION_COMMAND_MISMATCH"


def test_cli_does_not_fail_the_market_run_when_provenance_is_unavailable(
    tmp_path, capsys
):
    database, _ = _database(tmp_path, running=False)

    exit_code = main(
        [
            "--automation-id",
            HK_AUTOMATION_ID,
            "--database",
            str(database),
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["provenance_status"] == "NOT_PROVIDED"
