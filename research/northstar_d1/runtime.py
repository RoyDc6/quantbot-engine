"""Operational safeguards for Northstar-D1 scheduled research runs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict
from zoneinfo import ZoneInfo


RUNTIME_SCHEMA_VERSION = "1.0"


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat()


def _read_json(path: Path) -> Dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return value if isinstance(value, dict) else None


def _atomic_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


@dataclass(frozen=True)
class RunDecision:
    """Result of attempting to acquire one scheduled market slot."""

    action: str
    slot_key: str
    run_id: str | None = None
    attempt: int = 0
    reason: str | None = None
    prior_state: Dict[str, Any] | None = None

    @property
    def should_run(self) -> bool:
        return self.action == "RUN"


class RunCoordinator:
    """Enforce one successful invocation per configured schedule slot."""

    def __init__(
        self,
        *,
        market: str,
        deployment: Dict[str, Any],
        output_root: Path,
        now: datetime | None = None,
        retry_delay: timedelta = timedelta(minutes=5),
        stale_after: timedelta = timedelta(minutes=30),
    ):
        self.market = market.upper()
        self.deployment = deployment
        self.output_root = Path(output_root)
        self._fixed_clock = now is not None
        self.now = _utc(now)
        self.retry_delay = retry_delay
        self.stale_after = stale_after

        schedule_timezone = deployment["schedule_timezone"]
        schedule_hour = int(deployment["schedule_hour"])
        schedule_minute = int(deployment["schedule_minute"])
        schedule_zone = ZoneInfo(schedule_timezone)
        local_now = self.now.astimezone(schedule_zone)
        scheduled_override = os.environ.get(
            "NORTHSTAR_ORIGIN_SCHEDULED_AT_LOCAL"
        )
        if scheduled_override:
            try:
                scheduled_at_local = datetime.fromisoformat(scheduled_override)
            except ValueError as exc:
                raise ValueError(
                    "invalid NORTHSTAR_ORIGIN_SCHEDULED_AT_LOCAL"
                ) from exc
            if scheduled_at_local.tzinfo is None:
                raise ValueError(
                    "NORTHSTAR_ORIGIN_SCHEDULED_AT_LOCAL must be timezone-aware"
                )
            scheduled_at_local = scheduled_at_local.astimezone(schedule_zone)
            expected_weekdays = tuple(deployment.get("schedule_weekdays") or ())
            if (
                scheduled_at_local.hour != schedule_hour
                or scheduled_at_local.minute != schedule_minute
                or scheduled_at_local.second != 0
                or scheduled_at_local.microsecond != 0
                or (
                    expected_weekdays
                    and scheduled_at_local.weekday() not in expected_weekdays
                )
            ):
                raise ValueError(
                    "NORTHSTAR_ORIGIN_SCHEDULED_AT_LOCAL violates the deployment schedule"
                )
        else:
            scheduled_at_local = local_now.replace(
                hour=schedule_hour,
                minute=schedule_minute,
                second=0,
                microsecond=0,
            )
        self.schedule_timezone = schedule_timezone
        self.scheduled_at_local = scheduled_at_local
        self.scheduled_at_utc = _utc(self.scheduled_at_local)
        self.slot_key = (
            f"{self.market}:{scheduled_at_local.date().isoformat()}:"
            f"{schedule_hour:02d}:{schedule_minute:02d}:{schedule_timezone}"
        )
        self.slot_token = (
            f"{scheduled_at_local:%Y%m%d}T{schedule_hour:02d}{schedule_minute:02d}"
        )

        self.runtime_dir = self.output_root / self.market.lower() / ".runtime"
        self.state_path = self.runtime_dir / "latest.json"
        self.lock_path = self.runtime_dir / "active.lock"
        self.ledger_path = self.runtime_dir / "events.jsonl"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)

        self.run_id: str | None = None
        self.attempt = 0
        self.started_at: datetime | None = None

    def _current(self) -> datetime:
        return self.now if self._fixed_clock else _utc()

    def _event(self, payload: Dict[str, Any]) -> None:
        record = {
            "schema_version": RUNTIME_SCHEMA_VERSION,
            "recorded_at_utc": _iso(self._current()),
            "deployment_id": self.deployment["deployment_id"],
            "market": self.market,
            "slot_key": self.slot_key,
            **payload,
        }
        with self.ledger_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _verified_success(self, state: Dict[str, Any]) -> bool:
        artifacts = state.get("artifacts")
        hashes = state.get("artifact_hashes")
        if not isinstance(artifacts, dict) or not isinstance(hashes, dict):
            return False
        if set(hashes) != {"archive_json", "archive_markdown"}:
            return False

        manifest_path = Path(str(artifacts.get("archive_manifest") or ""))
        manifest = _read_json(manifest_path)
        if not manifest:
            return False
        manifest_archive = manifest.get("archive")
        manifest_hashes = manifest.get("sha256")
        if not isinstance(manifest_archive, dict) or not isinstance(
            manifest_hashes, dict
        ):
            return False
        if (
            manifest.get("run_id") != state.get("run_id")
            or manifest.get("deployment_id") != self.deployment["deployment_id"]
            or manifest.get("market") != self.market
            or manifest.get("run_verdict") != "RUN_PASS"
        ):
            return False

        manifest_labels = {
            "archive_json": "json",
            "archive_markdown": "markdown",
        }
        for artifact_label, manifest_label in manifest_labels.items():
            artifact_path = Path(str(artifacts.get(artifact_label) or ""))
            manifest_artifact_path = Path(
                str(manifest_archive.get(manifest_label) or "")
            )
            if artifact_path.resolve() != manifest_artifact_path.resolve():
                return False
            if str(hashes[artifact_label]).upper() != str(
                manifest_hashes.get(manifest_label) or ""
            ).upper():
                return False
        for label, expected_hash in hashes.items():
            raw_path = artifacts.get(label)
            if not raw_path or not expected_hash:
                return False
            path = Path(raw_path)
            if not path.is_file() or _sha256(path) != str(expected_hash).upper():
                return False
        return bool(hashes)

    def _lock_is_stale(self, lock: Dict[str, Any] | None) -> bool:
        if not lock or not lock.get("started_at_utc"):
            return True
        try:
            started = datetime.fromisoformat(str(lock["started_at_utc"]))
        except ValueError:
            return True
        return self._current() - _utc(started) >= self.stale_after

    def _create_lock(self, lock_payload: Dict[str, Any]) -> bool:
        encoded = json.dumps(lock_payload, ensure_ascii=False, indent=2).encode("utf-8")
        try:
            descriptor = os.open(
                self.lock_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError:
            return False
        try:
            os.write(descriptor, encoded)
        finally:
            os.close(descriptor)
        return True

    def acquire(self) -> RunDecision:
        current = self._current()
        if current < self.scheduled_at_utc:
            actual_at_local = current.astimezone(ZoneInfo(self.schedule_timezone))
            decision = RunDecision(
                action="SKIPPED_EARLY",
                slot_key=self.slot_key,
                reason=(
                    f"actual time {actual_at_local.isoformat()} is earlier than "
                    f"scheduled threshold {self.scheduled_at_local.isoformat()}"
                ),
            )
            self._event(
                {
                    "event": decision.action,
                    "run_id": None,
                    "attempt": 0,
                    "actual_time_local": actual_at_local.isoformat(),
                    "threshold_local": self.scheduled_at_local.isoformat(),
                    "reason": decision.reason,
                }
            )
            return decision

        state = _read_json(self.state_path) or {}
        same_slot = state.get("slot_key") == self.slot_key
        status = state.get("status") if same_slot else None

        if status == "SUCCESS":
            if self._verified_success(state):
                decision = RunDecision(
                    action="DUPLICATE_SUPPRESSED",
                    slot_key=self.slot_key,
                    run_id=state.get("run_id"),
                    attempt=int(state.get("attempt") or 0),
                    reason="verified successful artifact already exists for slot",
                    prior_state=state,
                )
                self._event(
                    {
                        "event": decision.action,
                        "run_id": decision.run_id,
                        "attempt": decision.attempt,
                        "reason": decision.reason,
                    }
                )
                return decision
            return RunDecision(
                action="BLOCKED_CORRUPT_PRIOR_SUCCESS",
                slot_key=self.slot_key,
                run_id=state.get("run_id"),
                attempt=int(state.get("attempt") or 0),
                reason="prior success artifacts are missing or hash-mismatched",
                prior_state=state,
            )

        if status == "FAILED":
            attempt = int(state.get("attempt") or 1)
            if attempt >= 2:
                return RunDecision(
                    action="RETRY_EXHAUSTED",
                    slot_key=self.slot_key,
                    run_id=state.get("run_id"),
                    attempt=attempt,
                    reason="maximum two attempts reached for slot",
                    prior_state=state,
                )
            retry_text = state.get("retry_not_before_utc")
            if retry_text:
                try:
                    retry_at = _utc(datetime.fromisoformat(str(retry_text)))
                except ValueError:
                    retry_at = self.now + self.retry_delay
                if self.now < retry_at:
                    return RunDecision(
                        action="RETRY_DELAY_ACTIVE",
                        slot_key=self.slot_key,
                        run_id=state.get("run_id"),
                        attempt=attempt,
                        reason=f"retry is blocked until {retry_at.isoformat()}",
                        prior_state=state,
                    )

        existing_lock = _read_json(self.lock_path)
        if self.lock_path.exists():
            if not self._lock_is_stale(existing_lock):
                return RunDecision(
                    action="RUN_IN_PROGRESS",
                    slot_key=self.slot_key,
                    run_id=(existing_lock or {}).get("run_id"),
                    attempt=int((existing_lock or {}).get("attempt") or 0),
                    reason="another invocation holds the active slot lock",
                    prior_state=existing_lock,
                )
            current = self._current()
            stale_path = self.runtime_dir / (
                f"stale-{current:%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}.lock"
            )
            try:
                os.replace(self.lock_path, stale_path)
            except FileNotFoundError:
                pass

        self.attempt = int(state.get("attempt") or 0) + 1 if same_slot else 1
        self.run_id = (
            f"{self.deployment['deployment_id']}-{self.slot_token}-A{self.attempt:02d}"
        )
        self.started_at = self._current()
        lock_payload = {
            "schema_version": RUNTIME_SCHEMA_VERSION,
            "deployment_id": self.deployment["deployment_id"],
            "market": self.market,
            "slot_key": self.slot_key,
            "run_id": self.run_id,
            "attempt": self.attempt,
            "started_at_utc": _iso(self.started_at),
        }
        if not self._create_lock(lock_payload):
            return RunDecision(
                action="RUN_IN_PROGRESS",
                slot_key=self.slot_key,
                reason="active slot lock appeared during acquisition",
            )

        state_payload = {
            **lock_payload,
            "status": "RUNNING",
            "invocation_policy": "EXACTLY_ONCE_PER_SCHEDULE_SLOT",
        }
        _atomic_json(self.state_path, state_payload)
        self._event(
            {
                "event": "RUN_STARTED",
                "run_id": self.run_id,
                "attempt": self.attempt,
            }
        )
        return RunDecision(
            action="RUN",
            slot_key=self.slot_key,
            run_id=self.run_id,
            attempt=self.attempt,
        )

    def metadata(self) -> Dict[str, Any]:
        if not self.run_id or not self.started_at:
            raise RuntimeError("run metadata requested before acquiring a slot")
        started_at_local = self.started_at.astimezone(
            ZoneInfo(self.schedule_timezone)
        )
        schedule_lag_seconds = (
            self.started_at - self.scheduled_at_utc
        ).total_seconds()
        automation_id = os.environ.get("NORTHSTAR_AUTOMATION_ID") or None
        automation_run_id = (
            os.environ.get("NORTHSTAR_AUTOMATION_RUN_ID") or None
        )
        automation_conversation_id = (
            os.environ.get("NORTHSTAR_AUTOMATION_CONVERSATION_ID") or None
        )
        automation_run_kind = (
            os.environ.get("NORTHSTAR_AUTOMATION_RUN_KIND") or None
        )
        invocation_source = (
            os.environ.get("NORTHSTAR_INVOCATION_SOURCE")
            or ("WORKBUDDY_AUTOMATION" if automation_id else "LOCAL_OR_UNATTRIBUTED")
        )
        invocation_run_kind = (
            os.environ.get("NORTHSTAR_INVOCATION_RUN_KIND")
            or automation_run_kind
        )
        return {
            "run_id": self.run_id,
            "slot_key": self.slot_key,
            "attempt": self.attempt,
            "schedule_timezone": self.schedule_timezone,
            "scheduled_at_local": self.scheduled_at_local.isoformat(),
            "scheduled_at_utc": _iso(self.scheduled_at_utc),
            "started_at_local": started_at_local.isoformat(),
            "started_at_utc": _iso(self.started_at),
            "schedule_lag_seconds": round(schedule_lag_seconds, 3),
            "invocation_source": invocation_source,
            "invocation_run_kind": invocation_run_kind,
            "scheduler_task_name": (
                os.environ.get("NORTHSTAR_SCHEDULER_TASK_NAME") or None
            ),
            "scheduler_contract_sha256": (
                os.environ.get("NORTHSTAR_SCHEDULER_CONTRACT_SHA256") or None
            ),
            "automation_id": automation_id,
            "automation_run_id": automation_run_id,
            "automation_conversation_id": automation_conversation_id,
            "automation_run_kind": automation_run_kind,
            "invocation_policy": "EXACTLY_ONCE_PER_SCHEDULE_SLOT",
            "artifact_policy": "IMMUTABLE_RUN_ARCHIVE_PLUS_ATOMIC_CANONICAL",
        }

    def _release_owned_lock(self, *, required: bool = False) -> Path | None:
        if not self.lock_path.exists():
            if required:
                raise RuntimeError("owned active lock is missing during finalization")
            return None

        lock = _read_json(self.lock_path)
        if not lock:
            raise RuntimeError("active lock is unreadable during finalization")
        if lock.get("run_id") != self.run_id:
            raise RuntimeError(
                "active lock ownership changed during finalization: "
                f"expected {self.run_id!r}, observed {lock.get('run_id')!r}"
            )

        released_dir = self.runtime_dir / "released_locks"
        released_dir.mkdir(parents=True, exist_ok=True)
        released_at = self._current()
        released_path = released_dir / (
            f"{safe_run_id(str(self.run_id))}-"
            f"{released_at:%Y%m%dT%H%M%S%fZ}-{uuid.uuid4().hex[:8]}.lock"
        )
        os.replace(self.lock_path, released_path)
        return released_path

    def mark_success(self, paths: Dict[str, Any]) -> None:
        completed = self._current()
        metadata = self.metadata()
        artifacts = {
            key: value
            for key, value in paths.items()
            if key not in {"hashes"} and isinstance(value, str)
        }
        hashes = dict(paths.get("hashes") or {})
        state = {
            "schema_version": RUNTIME_SCHEMA_VERSION,
            "deployment_id": self.deployment["deployment_id"],
            "market": self.market,
            **metadata,
            "status": "SUCCESS",
            "completed_at_utc": _iso(completed),
            "artifacts": artifacts,
            "artifact_hashes": hashes,
        }
        _atomic_json(self.state_path, state)
        released_path = self._release_owned_lock(required=True)
        state["lock_release"] = {
            "status": "RELEASED",
            "method": "ATOMIC_RENAME",
            "archive_path": str(released_path),
        }
        _atomic_json(self.state_path, state)
        self._event(
            {
                "event": "RUN_SUCCEEDED",
                "run_id": self.run_id,
                "attempt": self.attempt,
                "scheduled_at_local": metadata["scheduled_at_local"],
                "started_at_local": metadata["started_at_local"],
                "schedule_lag_seconds": metadata["schedule_lag_seconds"],
                "invocation_source": metadata["invocation_source"],
                "automation_id": metadata["automation_id"],
                "automation_run_id": metadata["automation_run_id"],
                "artifacts": artifacts,
                "artifact_hashes": hashes,
                "lock_release": state["lock_release"],
            }
        )

    def mark_failed(
        self,
        reason: str,
        *,
        exit_code: int = 1,
        paths: Dict[str, Any] | None = None,
    ) -> None:
        completed = self._current()
        metadata = self.metadata()
        state = {
            "schema_version": RUNTIME_SCHEMA_VERSION,
            "deployment_id": self.deployment["deployment_id"],
            "market": self.market,
            **metadata,
            "status": "FAILED",
            "completed_at_utc": _iso(completed),
            "retry_not_before_utc": _iso(completed + self.retry_delay),
            "exit_code": int(exit_code),
            "reason": str(reason),
        }
        if paths:
            state["artifacts"] = {
                key: value
                for key, value in paths.items()
                if key != "hashes" and isinstance(value, str)
            }
            state["artifact_hashes"] = dict(paths.get("hashes") or {})
        _atomic_json(self.state_path, state)
        try:
            released_path = self._release_owned_lock()
        except Exception as lock_exc:
            state["lock_release"] = {
                "status": "FAILED",
                "method": "ATOMIC_RENAME",
                "reason": str(lock_exc),
            }
        else:
            state["lock_release"] = {
                "status": "RELEASED" if released_path else "NOT_PRESENT",
                "method": "ATOMIC_RENAME" if released_path else "NONE",
                "archive_path": str(released_path) if released_path else None,
            }
        _atomic_json(self.state_path, state)
        self._event(
            {
                "event": "RUN_FAILED",
                "run_id": self.run_id,
                "attempt": self.attempt,
                "scheduled_at_local": metadata["scheduled_at_local"],
                "started_at_local": metadata["started_at_local"],
                "schedule_lag_seconds": metadata["schedule_lag_seconds"],
                "invocation_source": metadata["invocation_source"],
                "automation_id": metadata["automation_id"],
                "automation_run_id": metadata["automation_run_id"],
                "exit_code": int(exit_code),
                "reason": str(reason),
                "lock_release": state["lock_release"],
            }
        )


def safe_run_id(value: str) -> str:
    """Validate that an audit run id cannot escape its archive directory."""

    if not value or not re.fullmatch(r"[A-Za-z0-9._-]+", value):
        raise ValueError(f"invalid run_id: {value!r}")
    return value
