from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


DB = Path(r"C:\Users\RoyGoode\.workbuddy\workbuddy.db")
BACKUPS = Path(r"C:\Users\RoyGoode\.workbuddy\automation-backups")
ROOT = Path(__file__).resolve().parent.parent
RECEIPT = ROOT / "integrated_workbuddy_migration_receipt.json"
TZ = ZoneInfo("Asia/Shanghai")

REPORTS = {
    "automation-1789539961349": {
        "prompt": (ROOT / "WORKBUDDY_PROMPT_HK.txt").read_text(encoding="utf-8").strip(),
        "rrule": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=9;BYMINUTE=50",
        "hour": 9,
        "minute": 50,
    },
    "automation-1789539961350": {
        "prompt": (ROOT / "WORKBUDDY_PROMPT_US.txt").read_text(encoding="utf-8").strip(),
        "rrule": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=21;BYMINUTE=50",
        "hour": 21,
        "minute": 50,
    },
}
LEGACY_RESEARCH_DELIVERY = (
    "automation-1785736457372",
    "automation-1785736457815",
)


def next_weekday_ms(now: datetime, hour: int, minute: int) -> int:
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return int(candidate.timestamp() * 1000)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    now = datetime.now(TZ)
    now_ms = int(time.time() * 1000)
    BACKUPS.mkdir(parents=True, exist_ok=True)
    backup = BACKUPS / f"workbuddy-before-northstar-integrated-{now:%Y%m%dT%H%M%S}.db"
    ids = tuple(REPORTS) + LEGACY_RESEARCH_DELIVERY

    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    before = {}
    after = {}
    try:
        for automation_id in ids:
            row = con.execute("SELECT * FROM automations WHERE id=?", (automation_id,)).fetchone()
            if row is None:
                raise RuntimeError(f"automation missing: {automation_id}")
            before[automation_id] = dict(row)
        target = sqlite3.connect(backup)
        try:
            con.backup(target)
        finally:
            target.close()

        con.execute("BEGIN IMMEDIATE")
        for automation_id, config in REPORTS.items():
            con.execute(
                "UPDATE automations SET prompt=?,status='ACTIVE',rrule=?,next_run_at=?,updated_at=? WHERE id=?",
                (
                    config["prompt"],
                    config["rrule"],
                    next_weekday_ms(now, config["hour"], config["minute"]),
                    now_ms,
                    automation_id,
                ),
            )
        for automation_id in LEGACY_RESEARCH_DELIVERY:
            con.execute(
                "UPDATE automations SET status='PAUSED',next_run_at=NULL,updated_at=? WHERE id=?",
                (now_ms, automation_id),
            )
        con.commit()
        for automation_id in ids:
            after[automation_id] = dict(
                con.execute("SELECT * FROM automations WHERE id=?", (automation_id,)).fetchone()
            )
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    verification = {}
    for automation_id in ids:
        changed = sorted(
            key for key in before[automation_id]
            if before[automation_id][key] != after[automation_id][key]
        )
        if automation_id in REPORTS:
            allowed = {"prompt", "status", "rrule", "next_run_at", "updated_at"}
            expected_status = "ACTIVE"
            if after[automation_id]["rrule"] != REPORTS[automation_id]["rrule"]:
                raise RuntimeError(f"rrule mismatch: {automation_id}")
            if after[automation_id]["prompt"] != REPORTS[automation_id]["prompt"]:
                raise RuntimeError(f"prompt mismatch: {automation_id}")
        else:
            allowed = {"status", "next_run_at", "updated_at"}
            expected_status = "PAUSED"
        unexpected = set(changed) - allowed
        if unexpected:
            raise RuntimeError(f"unexpected changes for {automation_id}: {sorted(unexpected)}")
        if after[automation_id]["status"] != expected_status:
            raise RuntimeError(f"status mismatch: {automation_id}")
        verification[automation_id] = {
            "name": after[automation_id]["name"],
            "status": after[automation_id]["status"],
            "rrule": after[automation_id]["rrule"],
            "next_run_at": after[automation_id]["next_run_at"],
            "changed_fields": changed,
        }

    result = {
        "status": "WORKBUDDY_INTEGRATED_MIGRATION_COMPLETE",
        "updated_at": now.isoformat(),
        "backup": str(backup),
        "backup_sha256": sha256(backup),
        "verification": verification,
    }
    RECEIPT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
