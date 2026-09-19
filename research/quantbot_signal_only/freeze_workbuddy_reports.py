from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path


DB_PATH = Path(r"C:\Users\RoyGoode\.workbuddy\workbuddy.db")
BACKUP_DIR = Path(r"C:\Users\RoyGoode\.workbuddy\automation-backups")
RECEIPT_PATH = Path(
    r"E:\quant\research\quantbot_signal_only\freeze_20260916\workbuddy_freeze_receipt.json"
)
AUTOMATION_IDS = (
    "automation-1779886038083",
    "automation-1779886038108",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup_path = BACKUP_DIR / f"workbuddy-before-quantbot-freeze-{stamp}.db"
    before: dict[str, dict] = {}
    after: dict[str, dict] = {}

    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        for automation_id in AUTOMATION_IDS:
            row = connection.execute(
                "SELECT * FROM automations WHERE id=?", (automation_id,)
            ).fetchone()
            if row is None:
                raise RuntimeError(f"automation missing: {automation_id}")
            before[automation_id] = dict(row)

        backup = sqlite3.connect(backup_path)
        try:
            connection.backup(backup)
        finally:
            backup.close()

        now_ms = int(time.time() * 1000)
        connection.execute("BEGIN IMMEDIATE")
        for automation_id in AUTOMATION_IDS:
            connection.execute(
                "UPDATE automations SET status='PAUSED', next_run_at=NULL, updated_at=? WHERE id=?",
                (now_ms, automation_id),
            )
        connection.commit()

        for automation_id in AUTOMATION_IDS:
            after[automation_id] = dict(
                connection.execute(
                    "SELECT * FROM automations WHERE id=?", (automation_id,)
                ).fetchone()
            )
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    allowed = {"status", "next_run_at", "updated_at"}
    verification: dict[str, dict] = {}
    for automation_id in AUTOMATION_IDS:
        changed = sorted(
            key
            for key in before[automation_id]
            if before[automation_id][key] != after[automation_id][key]
        )
        unexpected = set(changed) - allowed
        if unexpected:
            raise RuntimeError(
                f"unexpected fields changed for {automation_id}: {sorted(unexpected)}"
            )
        if after[automation_id]["status"] != "PAUSED":
            raise RuntimeError(f"automation did not pause: {automation_id}")
        if after[automation_id]["next_run_at"] is not None:
            raise RuntimeError(f"next run remains scheduled: {automation_id}")
        verification[automation_id] = {
            "name": after[automation_id]["name"],
            "status": after[automation_id]["status"],
            "next_run_at": after[automation_id]["next_run_at"],
            "rrule_preserved": after[automation_id]["rrule"],
            "changed_fields": changed,
        }

    receipt = {
        "frozen_at": datetime.now().astimezone().isoformat(),
        "database": str(DB_PATH),
        "backup": str(backup_path),
        "backup_sha256": sha256(backup_path),
        "verification": verification,
    }
    RECEIPT_PATH.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
