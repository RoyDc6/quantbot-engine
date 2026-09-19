"""Create the WorkBuddy read-only daily-report automation with online backup."""

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
PROMPT_PATH = Path(__file__).resolve().parent.parent / "WORKBUDDY_PROMPT.txt"
NAME = "Northstar-D1 Futu模拟交易日报"
RRULE = "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=22;BYMINUTE=0"
TZ = ZoneInfo("Asia/Shanghai")


def _next_run_ms(now: datetime) -> int:
    candidate = now.replace(hour=22, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return int(candidate.timestamp() * 1000)


def main() -> int:
    prompt = PROMPT_PATH.read_text(encoding="utf-8").strip()
    if not prompt.startswith("[NORTHSTAR_D1_FUTU_SIM_REPORT_V1]"):
        raise RuntimeError("prompt contract marker missing")
    now = datetime.now(TZ)
    now_ms = int(time.time() * 1000)
    automation_id = f"automation-{now_ms}"
    BACKUPS.mkdir(parents=True, exist_ok=True)
    backup = BACKUPS / f"before_northstar_d1_futu_sim_report_{now.strftime('%Y%m%dT%H%M%S')}.db"

    source = sqlite3.connect(DB, timeout=30)
    try:
        target = sqlite3.connect(backup)
        try:
            source.backup(target)
        finally:
            target.close()

        source.row_factory = sqlite3.Row
        existing = source.execute(
            "SELECT * FROM automations WHERE name=? AND deleted_at IS NULL",
            (NAME,),
        ).fetchall()
        if existing:
            if len(existing) != 1:
                raise RuntimeError("multiple active automations with target name")
            row = dict(existing[0])
            expected = {
                "prompt": prompt,
                "status": "ACTIVE",
                "cwds": json.dumps([r"E:\quant"], ensure_ascii=False),
                "rrule": RRULE,
            }
            if all(row.get(k) == v for k, v in expected.items()):
                print(json.dumps({
                    "status": "EXISTING_UNCHANGED",
                    "automation_id": row["id"],
                    "backup_path": str(backup),
                }, ensure_ascii=False))
                return 0
            raise RuntimeError("target automation already exists with different contract")

        source.execute("BEGIN IMMEDIATE")
        source.execute(
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
                automation_id, NAME, prompt, "ACTIVE", "recurring",
                _next_run_ms(now), None, json.dumps([r"E:\quant"]), RRULE,
                None, None, None, "glm-5.3", 1, 0, now_ms, now_ms, "[]",
                None, None, None, "[]", "fullAccess",
                "db3ee9db-942a-44eb-aa02-c3b8b88aee2a",
                "confirmed", "created", 0, None, None,
            ),
        )
        source.commit()
        row = dict(source.execute(
            "SELECT * FROM automations WHERE id=?", (automation_id,)
        ).fetchone())
    except Exception:
        source.rollback()
        raise
    finally:
        source.close()

    prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest().upper()
    checks = {
        "name": row["name"] == NAME,
        "status": row["status"] == "ACTIVE",
        "schedule_type": row["schedule_type"] == "recurring",
        "rrule": row["rrule"] == RRULE,
        "cwds": json.loads(row["cwds"]) == [r"E:\quant"],
        "model": row["model_id"] == "glm-5.3",
        "thinking": row["model_is_thinking"] == 1,
        "permission": row["permission_mode"] == "fullAccess",
        "prompt": row["prompt"] == prompt,
        "not_deleted": row["deleted_at"] is None,
    }
    if not all(checks.values()):
        raise RuntimeError(f"readback failed: {checks}")
    print(json.dumps({
        "status": "CREATED_AND_VERIFIED",
        "automation_id": automation_id,
        "name": NAME,
        "rrule": RRULE,
        "next_run_at": row["next_run_at"],
        "prompt_sha256": prompt_sha,
        "backup_path": str(backup),
        "checks": checks,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
