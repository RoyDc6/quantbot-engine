"""Replace the combined WorkBuddy report automation with HK/US tasks."""

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
COMBINED_ID = "automation-1789539425376"
TZ = ZoneInfo("Asia/Shanghai")
OWNER = "db3ee9db-942a-44eb-aa02-c3b8b88aee2a"
TASKS = (
    {
        "market": "HK",
        "name": "Northstar-D1 Futu港股模拟交易日报",
        "prompt_path": ROOT / "WORKBUDDY_PROMPT_HK.txt",
        "rrule": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=10;BYMINUTE=10",
        "hour": 10,
        "minute": 10,
    },
    {
        "market": "US",
        "name": "Northstar-D1 Futu美股模拟交易日报",
        "prompt_path": ROOT / "WORKBUDDY_PROMPT_US.txt",
        "rrule": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=23;BYMINUTE=0",
        "hour": 23,
        "minute": 0,
    },
)


def _next_ms(now: datetime, hour: int, minute: int) -> int:
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return int(candidate.timestamp() * 1000)


def main() -> int:
    now = datetime.now(TZ)
    now_ms = int(time.time() * 1000)
    BACKUPS.mkdir(parents=True, exist_ok=True)
    backup = BACKUPS / f"before_split_northstar_d1_futu_sim_{now:%Y%m%dT%H%M%S}.db"
    prompts = {
        item["market"]: item["prompt_path"].read_text(encoding="utf-8").strip()
        for item in TASKS
    }

    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    target = sqlite3.connect(backup)
    try:
        con.backup(target)
    finally:
        target.close()

    created: list[str] = []
    try:
        combined = con.execute(
            "SELECT id,status FROM automations WHERE id=? AND deleted_at IS NULL",
            (COMBINED_ID,),
        ).fetchone()
        if combined is None:
            raise RuntimeError("combined automation not found")
        con.execute("BEGIN IMMEDIATE")
        con.execute(
            "UPDATE automations SET status='PAUSED',next_run_at=NULL,updated_at=? WHERE id=?",
            (now_ms, COMBINED_ID),
        )
        for offset, item in enumerate(TASKS):
            existing = con.execute(
                "SELECT id FROM automations WHERE name=? AND deleted_at IS NULL",
                (item["name"],),
            ).fetchall()
            if existing:
                raise RuntimeError(f"target already exists: {item['name']}")
            automation_id = f"automation-{now_ms + offset + 1}"
            con.execute(
                """
                INSERT INTO automations(
                    id,name,prompt,status,schedule_type,next_run_at,last_run_at,cwds,
                    rrule,scheduled_at,valid_from,valid_until,model_id,
                    model_is_thinking,push_to_wechat,created_at,updated_at,skills_json,
                    deleted_at,expert_id,expert_marketplace,connector_ids_json,
                    permission_mode,owner_user_id,owner_status,owner_source,
                    push_to_wecom_bot,wecom_bot_source,context_window
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    automation_id, item["name"], prompts[item["market"]],
                    "ACTIVE", "recurring", _next_ms(now, item["hour"], item["minute"]), None,
                    json.dumps([r"E:\quant"]), item["rrule"], None, None, None,
                    "glm-5.3", 1, 0, now_ms + offset + 1, now_ms + offset + 1,
                    "[]", None, None, None, "[]", "fullAccess", OWNER,
                    "confirmed", "created", 0, None, None,
                ),
            )
            created.append(automation_id)
        con.commit()

        rows = [dict(con.execute("SELECT * FROM automations WHERE id=?", (aid,)).fetchone()) for aid in created]
        combined_after = dict(con.execute(
            "SELECT id,status,next_run_at FROM automations WHERE id=?", (COMBINED_ID,)
        ).fetchone())
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    checks = {
        "combined_paused": combined_after["status"] == "PAUSED" and combined_after["next_run_at"] is None,
        "two_created": len(rows) == 2,
        "both_active": all(row["status"] == "ACTIVE" for row in rows),
        "isolated_names": {row["name"] for row in rows} == {item["name"] for item in TASKS},
        "isolated_schedules": {row["rrule"] for row in rows} == {item["rrule"] for item in TASKS},
        "cwd": all(json.loads(row["cwds"]) == [r"E:\quant"] for row in rows),
        "prompt_match": all(
            rows[i]["prompt"] == prompts[TASKS[i]["market"]] for i in range(2)
        ),
    }
    if not all(checks.values()):
        raise RuntimeError(f"split readback failed: {checks}")
    print(json.dumps({
        "status": "SPLIT_CREATED_AND_VERIFIED",
        "combined": combined_after,
        "tasks": [{
            "id": row["id"], "name": row["name"], "status": row["status"],
            "rrule": row["rrule"], "next_run_at": row["next_run_at"],
            "prompt_sha256": hashlib.sha256(row["prompt"].encode("utf-8")).hexdigest().upper(),
        } for row in rows],
        "backup_path": str(backup),
        "checks": checks,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
