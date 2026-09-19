"""Read-only WorkBuddy automation-run provenance for Northstar-D1.

WorkBuddy does not currently expose per-automation process environment
configuration.  This module verifies the scheduler's live SQLite run record
without writing to WorkBuddy or invoking market data.  The verified identity
can be consumed before a Northstar run by a dedicated WorkBuddy entry point,
or read back after the run for scheduler-level evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict
from zoneinfo import ZoneInfo


SCHEMA_VERSION = "1.0"
DEFAULT_DATABASE = Path.home() / ".workbuddy" / "workbuddy.db"
ON_DEMAND_FOLLOWUP_RUN_KIND = "on_demand_followup"
ACTIVE_SESSION_STATUSES = ("planning", "working")
AUTOMATION_CONTRACTS = {
    "automation-1785736457372": {
        "deployment_id": "NORTHSTAR_D1_HK",
        "market": "HK",
        "schedule_timezone": "Asia/Hong_Kong",
        "expected_cwd": r"E:\quant",
        "command_token": "research.northstar_d1.workbuddy_hk",
    },
    "automation-1785736457815": {
        "deployment_id": "NORTHSTAR_D1_US",
        "market": "US",
        "schedule_timezone": "Asia/Shanghai",
        "expected_cwd": r"E:\quant",
        "command_token": "research.northstar_d1.workbuddy_us",
    },
}


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _iso_from_epoch_ms(value: int | float) -> str:
    return datetime.fromtimestamp(float(value) / 1000, timezone.utc).isoformat()


def _normalized_path(value: str) -> str:
    return os.path.normcase(os.path.normpath(value))


def _not_provided(
    automation_id: str,
    reason: str,
    *,
    verified_at: datetime,
) -> Dict[str, Any]:
    contract = AUTOMATION_CONTRACTS.get(automation_id) or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "provenance_status": "NOT_PROVIDED",
        "reason": reason,
        "invocation_source": "NOT_PROVIDED",
        "automation_id": None,
        "automation_run_id": None,
        "automation_conversation_id": None,
        "automation_run_kind": None,
        "requested_automation_id": automation_id,
        "deployment_id": contract.get("deployment_id"),
        "market": contract.get("market"),
        "verified_at_utc": _utc(verified_at).isoformat(),
        "evidence_source": "WORKBUDDY_SQLITE_READ_ONLY",
    }


def resolve_workbuddy_provenance(
    automation_id: str,
    *,
    database_path: Path | str = DEFAULT_DATABASE,
    observed_at: datetime | None = None,
    max_run_age: timedelta = timedelta(minutes=30),
) -> Dict[str, Any]:
    """Return a verified WorkBuddy automation identity or NOT_PROVIDED.

    Verification requires all of the following to agree:

    - the allowlisted automation is ACTIVE and still has the expected market
      command and E:\\quant workspace;
    - a scheduler runtime, an IN_PROGRESS manual_test run, or an active
      background-automation follow-up session is uniquely attributable to it;
    - the run record, session, cwd, and conversation identity agree; and
    - all live records are recent enough to be the current invocation.
    """

    verified_at = _utc(observed_at)
    contract = AUTOMATION_CONTRACTS.get(automation_id)
    if not contract:
        return _not_provided(
            automation_id,
            "AUTOMATION_ID_NOT_ALLOWLISTED",
            verified_at=verified_at,
        )

    database = Path(database_path).expanduser().resolve()
    if not database.is_file():
        return _not_provided(
            automation_id,
            "WORKBUDDY_DATABASE_MISSING",
            verified_at=verified_at,
        )

    try:
        connection = sqlite3.connect(
            f"{database.as_uri()}?mode=ro",
            uri=True,
            timeout=5.0,
        )
        connection.row_factory = sqlite3.Row
    except sqlite3.Error:
        return _not_provided(
            automation_id,
            "WORKBUDDY_DATABASE_OPEN_FAILED",
            verified_at=verified_at,
        )

    try:
        automation = connection.execute(
            """
            SELECT id, status, cwds, prompt, deleted_at, updated_at
            FROM automations
            WHERE id = ?
            """,
            (automation_id,),
        ).fetchone()
        if not automation or automation["deleted_at"] is not None:
            return _not_provided(
                automation_id,
                "AUTOMATION_NOT_FOUND",
                verified_at=verified_at,
            )
        if automation["status"] != "ACTIVE":
            return _not_provided(
                automation_id,
                "AUTOMATION_NOT_ACTIVE",
                verified_at=verified_at,
            )

        try:
            workspaces = json.loads(automation["cwds"] or "[]")
        except json.JSONDecodeError:
            workspaces = []
        expected_cwd = _normalized_path(str(contract["expected_cwd"]))
        if not isinstance(workspaces, list) or expected_cwd not in {
            _normalized_path(str(value)) for value in workspaces
        }:
            return _not_provided(
                automation_id,
                "AUTOMATION_WORKSPACE_MISMATCH",
                verified_at=verified_at,
            )
        if str(contract["command_token"]) not in (automation["prompt"] or ""):
            return _not_provided(
                automation_id,
                "AUTOMATION_COMMAND_MISMATCH",
                verified_at=verified_at,
            )

        runtime = connection.execute(
            """
            SELECT running, running_started_at, running_conversation_id
            FROM automation_runtime_state
            WHERE automation_id = ?
            """,
            (automation_id,),
        ).fetchone()
        identity_mode = "ACTIVE_RUNTIME"
        provenance_reason = "ACTIVE_WORKBUDDY_RUN_RECORD_MATCHED"
        effective_run_kind = None
        conversation_link_mode = "RUN_ENTRY"
        if runtime and int(runtime["running"] or 0) == 1:
            running_started_at = runtime["running_started_at"]
            conversation_id = str(
                runtime["running_conversation_id"] or ""
            ).strip()
            if running_started_at is None or not conversation_id:
                return _not_provided(
                    automation_id,
                    "AUTOMATION_RUNTIME_IDENTITY_INCOMPLETE",
                    verified_at=verified_at,
                )

            age = verified_at - datetime.fromtimestamp(
                float(running_started_at) / 1000,
                timezone.utc,
            )
            if age < timedelta(seconds=-5) or age > max_run_age:
                return _not_provided(
                    automation_id,
                    "AUTOMATION_RUNTIME_RECORD_STALE",
                    verified_at=verified_at,
                )

            runs = connection.execute(
                """
                SELECT thread_id, status, source_cwd, runs_json, metadata_json,
                       created_at, updated_at
                FROM automation_runs
                WHERE automation_id = ?
                  AND ABS(created_at - ?) <= 2000
                ORDER BY ABS(created_at - ?), created_at DESC
                """,
                (automation_id, running_started_at, running_started_at),
            ).fetchall()
            if len(runs) != 1:
                return _not_provided(
                    automation_id,
                    "AUTOMATION_RUN_RECORD_NOT_UNIQUE",
                    verified_at=verified_at,
                )
            run = runs[0]
        else:
            verified_ms = int(verified_at.timestamp() * 1000)
            on_demand_age = min(max_run_age, timedelta(minutes=10))
            cutoff_ms = int((verified_at - on_demand_age).timestamp() * 1000)
            active_sessions = {}
            for session in connection.execute(
                """
                SELECT id, cwd, status, created_at, last_activity_at
                FROM sessions
                WHERE is_background_automation = 1
                  AND deleted_at IS NULL
                  AND LOWER(status) IN (?, ?)
                """,
                ACTIVE_SESSION_STATUSES,
            ).fetchall():
                activity_at = session["last_activity_at"]
                if activity_at is None:
                    continue
                session_age = verified_at - datetime.fromtimestamp(
                    float(activity_at) / 1000,
                    timezone.utc,
                )
                if (
                    session_age < timedelta(seconds=-5)
                    or session_age > on_demand_age
                    or _normalized_path(str(session["cwd"] or ""))
                    != expected_cwd
                ):
                    continue
                active_sessions[str(session["id"])] = session

            manual_matches = []
            manual_match_ambiguous = False
            manual_runs = connection.execute(
                """
                SELECT thread_id, status, source_cwd, runs_json, metadata_json,
                       created_at, updated_at
                FROM automation_runs
                WHERE automation_id = ?
                  AND status = 'IN_PROGRESS'
                  AND created_at BETWEEN ? AND ?
                ORDER BY created_at DESC
                """,
                (automation_id, cutoff_ms, verified_ms + 5000),
            ).fetchall()
            for candidate in manual_runs:
                try:
                    candidate_metadata = json.loads(
                        candidate["metadata_json"] or "{}"
                    )
                    candidate_entries = json.loads(
                        candidate["runs_json"] or "[]"
                    )
                except json.JSONDecodeError:
                    continue
                if (
                    not isinstance(candidate_metadata, dict)
                    or candidate_metadata.get("runKind") != "manual_test"
                    or not isinstance(candidate_entries, list)
                ):
                    continue
                matches = []
                declared_conversations = []
                for entry in candidate_entries:
                    if not isinstance(entry, dict):
                        continue
                    candidate_conversation = str(
                        entry.get("conversationId") or ""
                    ).strip()
                    if candidate_conversation:
                        declared_conversations.append(candidate_conversation)
                    started_at = entry.get("startedAt")
                    if (
                        candidate_conversation in active_sessions
                        and _normalized_path(str(entry.get("cwd") or ""))
                        == expected_cwd
                        and isinstance(started_at, (int, float))
                        and not isinstance(started_at, bool)
                        and abs(float(started_at) - candidate["created_at"])
                        <= 2000
                    ):
                        matches.append(candidate_conversation)
                if len(matches) > 1:
                    manual_match_ambiguous = True
                elif len(matches) == 1:
                    manual_matches.append((candidate, matches[0]))
                elif not declared_conversations:
                    # WorkBuddy creates the manual_test run and its background
                    # session together, but does not populate runs_json until
                    # the run finishes. During the only window in which the
                    # trusted entry is allowed to execute, pair those two
                    # records by their millisecond creation timestamps. This
                    # remains fail closed unless exactly one active session in
                    # the expected workspace was created within two seconds.
                    timestamp_matches = [
                        candidate_conversation
                        for candidate_conversation, session in active_sessions.items()
                        if isinstance(session["created_at"], (int, float))
                        and not isinstance(session["created_at"], bool)
                        and abs(
                            float(session["created_at"])
                            - float(candidate["created_at"])
                        )
                        <= 2000
                    ]
                    if len(timestamp_matches) > 1:
                        manual_match_ambiguous = True
                    elif len(timestamp_matches) == 1:
                        manual_matches.append((candidate, timestamp_matches[0]))
                        conversation_link_mode = "CREATED_AT_PAIR"

            if manual_match_ambiguous or len(manual_matches) > 1:
                return _not_provided(
                    automation_id,
                    "AUTOMATION_ON_DEMAND_RECORD_NOT_UNIQUE",
                    verified_at=verified_at,
                )
            if len(manual_matches) == 1:
                run, conversation_id = manual_matches[0]
                identity_mode = "ACTIVE_MANUAL_RUN"
                provenance_reason = "ACTIVE_WORKBUDDY_MANUAL_RUN_MATCHED"
            else:
                followup_matches = []
                origin_runs = connection.execute(
                    """
                    SELECT thread_id, status, source_cwd, runs_json,
                           metadata_json, created_at, updated_at
                    FROM automation_runs
                    WHERE automation_id = ?
                      AND status = 'PENDING_REVIEW'
                    ORDER BY created_at DESC
                    """,
                    (automation_id,),
                ).fetchall()
                for candidate in origin_runs:
                    try:
                        candidate_metadata = json.loads(
                            candidate["metadata_json"] or "{}"
                        )
                        candidate_entries = json.loads(
                            candidate["runs_json"] or "[]"
                        )
                    except json.JSONDecodeError:
                        continue
                    if (
                        not isinstance(candidate_metadata, dict)
                        or candidate_metadata.get("runKind")
                        not in {"scheduled", "missed", "manual_test"}
                        or not isinstance(candidate_entries, list)
                    ):
                        continue
                    linked = [
                        str(entry.get("conversationId") or "").strip()
                        for entry in candidate_entries
                        if isinstance(entry, dict)
                        and str(entry.get("conversationId") or "").strip()
                        in active_sessions
                        and _normalized_path(str(entry.get("cwd") or ""))
                        == expected_cwd
                    ]
                    if len(linked) == 1:
                        followup_matches.append((candidate, linked[0]))

                if len(followup_matches) > 1:
                    return _not_provided(
                        automation_id,
                        "AUTOMATION_ON_DEMAND_SESSION_NOT_UNIQUE",
                        verified_at=verified_at,
                    )
                if not followup_matches:
                    return _not_provided(
                        automation_id,
                        "AUTOMATION_NOT_CURRENTLY_RUNNING",
                        verified_at=verified_at,
                    )
                run, conversation_id = followup_matches[0]
                identity_mode = "ACTIVE_AUTOMATION_SESSION"
                provenance_reason = "ACTIVE_WORKBUDDY_FOLLOWUP_SESSION_MATCHED"
                effective_run_kind = ON_DEMAND_FOLLOWUP_RUN_KIND
        if _normalized_path(str(run["source_cwd"] or "")) != expected_cwd:
            return _not_provided(
                automation_id,
                "AUTOMATION_RUN_WORKSPACE_MISMATCH",
                verified_at=verified_at,
            )

        try:
            run_entries = json.loads(run["runs_json"] or "[]")
        except json.JSONDecodeError:
            run_entries = []
        matching_conversations = [
            entry
            for entry in run_entries
            if isinstance(entry, dict)
            and str(entry.get("conversationId") or "") == conversation_id
            and _normalized_path(str(entry.get("cwd") or "")) == expected_cwd
        ]
        if (
            conversation_link_mode != "CREATED_AT_PAIR"
            and len(matching_conversations) != 1
        ):
            return _not_provided(
                automation_id,
                "AUTOMATION_CONVERSATION_MISMATCH",
                verified_at=verified_at,
            )

        try:
            metadata = json.loads(run["metadata_json"] or "{}")
        except json.JSONDecodeError:
            metadata = {}
        origin_run_kind = (
            metadata.get("runKind") if isinstance(metadata, dict) else None
        )
        run_kind = effective_run_kind or origin_run_kind
        missed_scheduled_at = None
        if (
            origin_run_kind == "missed"
            and identity_mode != "ACTIVE_AUTOMATION_SESSION"
        ):
            raw_missed_scheduled_at = metadata.get("missedScheduledAt")
            if (
                isinstance(raw_missed_scheduled_at, bool)
                or not isinstance(raw_missed_scheduled_at, (int, float))
            ):
                return _not_provided(
                    automation_id,
                    "AUTOMATION_MISSED_SCHEDULE_NOT_PROVIDED",
                    verified_at=verified_at,
                )
            try:
                missed_scheduled_at = datetime.fromtimestamp(
                    float(raw_missed_scheduled_at) / 1000,
                    timezone.utc,
                )
            except (OSError, OverflowError, ValueError):
                return _not_provided(
                    automation_id,
                    "AUTOMATION_MISSED_SCHEDULE_NOT_PROVIDED",
                    verified_at=verified_at,
                )
            schedule_timezone = ZoneInfo(str(contract["schedule_timezone"]))
            if (
                missed_scheduled_at > verified_at + timedelta(seconds=5)
                or missed_scheduled_at.astimezone(schedule_timezone).date()
                != verified_at.astimezone(schedule_timezone).date()
            ):
                return _not_provided(
                    automation_id,
                    "AUTOMATION_MISSED_SCHEDULE_STALE",
                    verified_at=verified_at,
                )
        return {
            "schema_version": SCHEMA_VERSION,
            "provenance_status": "VERIFIED",
            "reason": provenance_reason,
            "invocation_source": "WORKBUDDY_AUTOMATION",
            "automation_id": automation_id,
            "automation_run_id": str(run["thread_id"]),
            "automation_conversation_id": conversation_id,
            "automation_run_kind": run_kind or "NOT_PROVIDED",
            "automation_origin_run_kind": origin_run_kind or "NOT_PROVIDED",
            "automation_identity_mode": identity_mode,
            "automation_conversation_link_mode": conversation_link_mode,
            "automation_run_status": str(run["status"]),
            "deployment_id": contract["deployment_id"],
            "market": contract["market"],
            "source_cwd": str(run["source_cwd"]),
            "automation_started_at_utc": _iso_from_epoch_ms(run["created_at"]),
            "automation_record_updated_at_utc": _iso_from_epoch_ms(
                run["updated_at"]
            ),
            "verified_at_utc": verified_at.isoformat(),
            "evidence_source": "WORKBUDDY_SQLITE_READ_ONLY",
        }
    except sqlite3.Error:
        return _not_provided(
            automation_id,
            "WORKBUDDY_DATABASE_QUERY_FAILED",
            verified_at=verified_at,
        )
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only WorkBuddy run provenance for Northstar-D1",
    )
    parser.add_argument("--automation-id", required=True)
    parser.add_argument("--database", default=str(DEFAULT_DATABASE))
    parser.add_argument("--max-run-age-seconds", type=float, default=1800.0)
    parser.add_argument(
        "--require-verified",
        action="store_true",
        help="return exit code 2 when provenance is not verified",
    )
    args = parser.parse_args(argv)
    result = resolve_workbuddy_provenance(
        args.automation_id,
        database_path=Path(args.database),
        max_run_age=timedelta(seconds=max(0.0, args.max_run_age_seconds)),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.require_verified and result["provenance_status"] != "VERIFIED":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
