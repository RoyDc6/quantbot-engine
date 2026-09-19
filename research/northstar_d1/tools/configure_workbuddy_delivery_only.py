"""Configure the two existing WorkBuddy automations as delivery-only tasks.

The update is allowlisted and hash-gated.  It preserves automation history,
sessions, IDs, workspace, model selection, connectors, and notification flags.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


DATABASE = Path(r"C:\Users\RoyGoode\.workbuddy\workbuddy.db")
BACKUP_DIR = Path(r"C:\Users\RoyGoode\.workbuddy\automation-backups")
# WorkBuddy 5.3.14 executes automation shell commands through Git Bash.  Use a
# slash form that is valid there as well as in Windows process launching; a
# backslash path is parsed as C:Users... and causes the agent to retry.
PYTHON = "C:/Users/RoyGoode/AppData/Local/Programs/Python/Python312/python.exe"
CONTRACTS = {
    "automation-1785736457372": {
        "market": "HK",
        "name": "Northstar-D1 HK 报告交付",
        "module": "research.northstar_d1.delivery_hk",
        "timezone": "Asia/Hong_Kong",
        "weekdays": (0, 1, 2, 3, 4),
        "hour": 16,
        "minute": 40,
        "rrule": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=16;BYMINUTE=40",
        "expected_prompt_sha256": "D3F3A56B158D0F3AC0BE9EE327E71607084EBA0D80CCE0C1501AE41CB5B71066",
        "previous_delivery_prompt_sha256": "DA0F24D61A096FF962F79CB7C6D2981699E5562B0D3F9EE06039AA345860FD96",
        "expected_rrule": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=16;BYMINUTE=20",
    },
    "automation-1785736457815": {
        "market": "US",
        "name": "Northstar-D1 US 报告交付",
        "module": "research.northstar_d1.delivery_us",
        "timezone": "Asia/Shanghai",
        "weekdays": (1, 2, 3, 4, 5),
        "hour": 10,
        "minute": 20,
        "rrule": "FREQ=WEEKLY;BYDAY=TU,WE,TH,FR,SA;BYHOUR=10;BYMINUTE=20",
        "expected_prompt_sha256": "1A2FFD88FB132F6CA76F9C99245FA598C68BC87738D6E02F86202582DF71E00E",
        "previous_delivery_prompt_sha256": "7F96E323B639002EF453799654805E17C824DE946DF7C9898A146ABE62C024C5",
        "expected_rrule": "FREQ=WEEKLY;BYDAY=TU,WE,TH,FR,SA;BYHOUR=10;BYMINUTE=0",
    },
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest().upper()


def _prompt(contract: dict) -> str:
    command = (
        f'{PYTHON} -m {contract["module"]} '
        "--wait-seconds 600 --poll-seconds 15"
    )
    return f"""[WINDOWS_DELIVERY_ONLY_V1]
[WORKBUDDY_ZH_CN_DELIVERY_V1]
[WORKBUDDY_MARKDOWN_ONLY_V1]

你正在执行 Northstar-D1 {contract['market']} 报告交付任务。Windows Task Scheduler 是唯一运行权威；本任务只交付已经生成并验证的 Markdown，不运行研究模型。

1. 永久禁止事项
- 禁止调用 Futu、模型、Northstar runner、workbuddy_hk、workbuddy_us、账户、持仓、余额、订单或成交 API。
- 禁止修改、删除、移动或重建 `.runtime`、`runs`、JSON、Markdown、manifest、源码、任务计划或其他 automation。
- WorkBuddy 平台可能自动读写本 automation 的 memory。它只能作为平台内部记账，不得作为
  报告路径、运行状态或交付结论的证据，也不得据此跳过 helper。
- 禁止使用历史 WorkBuddy receipt 或猜测报告路径。

2. 唯一允许的命令
- 工作目录必须保持 `E:\\quant`。
- 只执行一次以下命令，不得执行第二条 shell 命令，不得自行重试。路径已经使用 Git Bash
  与 Windows 都兼容的正斜杠，禁止改写为反斜杠：
{command}
- helper 最多只读等待 600 秒；它不调用 Futu、模型、账户或订单，也不写任何文件。

3. 失败关闭
- 只接受退出码 0 且 stdout 中唯一 JSON 的 `status=DELIVERY_READY`。
- 如果退出码非 0、状态不是 `DELIVERY_READY`、JSON 无法解析、`present_files_count!=1` 或 `present_only_this_markdown!=true`：不得调用 present_files，不得运行其他命令，只报告 `DELIVERY_NOT_READY` 和原始 reason。

4. 唯一交付
- 从 helper JSON 读取 `report_path` 和 `report_sha256`；不得改写路径。
- 调用 present_files 恰好一次，只附加 `report_path` 指向的一个 Markdown。不得附加 JSON、manifest、日志或第二个文件。
- 不要在普通回复中复制完整逐标的表；附件 Markdown 是权威报告。

5. 中文优先回复
- 附件成功后，只用中文简要报告：`DELIVERY_READY`、market、run_id、slot_key、run_kind、signal_asof、BUY/SELL/HOLD、report_path、report_sha256。
- 必须保留：`RESEARCH_ONLY / NOT_EVALUATED / formal_publish_allowed=false / ZERO_EXECUTION`。
- 不得把 `MISSED_RECOVERY` 写成准时运行，不得把 `RUN_PASS` 写成模型已经评估或可以正式发布。
"""


def _next_occurrence(contract: dict, now: datetime | None = None) -> datetime:
    current = (now or datetime.now(timezone.utc)).astimezone(
        ZoneInfo(contract["timezone"])
    )
    candidate = current.replace(
        hour=contract["hour"],
        minute=contract["minute"],
        second=0,
        microsecond=0,
    )
    if candidate <= current:
        candidate += timedelta(days=1)
    while candidate.weekday() not in contract["weekdays"]:
        candidate += timedelta(days=1)
    return candidate


def _rows(connection: sqlite3.Connection) -> dict[str, dict]:
    connection.row_factory = sqlite3.Row
    ids = tuple(CONTRACTS)
    rows = connection.execute(
        "SELECT * FROM automations WHERE id IN (?,?) ORDER BY id",
        ids,
    ).fetchall()
    if len(rows) != 2:
        raise RuntimeError("ALLOWLIST_AUTOMATION_ROW_MISSING")
    return {str(row["id"]): dict(row) for row in rows}


def _proposal(rows: dict[str, dict]) -> dict[str, dict]:
    proposal = {}
    for automation_id, contract in CONTRACTS.items():
        row = rows[automation_id]
        prompt = _prompt(contract)
        next_local = _next_occurrence(contract)
        proposal[automation_id] = {
            "name": contract["name"],
            "market": contract["market"],
            "prompt": prompt,
            "prompt_sha256": _sha(prompt),
            "prompt_length": len(prompt),
            "status": "ACTIVE",
            "rrule": contract["rrule"],
            "next_run_at": int(next_local.astimezone(timezone.utc).timestamp() * 1000),
            "next_run_local": next_local.isoformat(),
            "workspace": row["cwds"],
        }
    return proposal


def _verify_prestate(rows: dict[str, dict]) -> None:
    for automation_id, contract in CONTRACTS.items():
        row = rows[automation_id]
        current_hash = _sha(str(row["prompt"] or ""))
        if (
            row["name"] == contract["name"]
            and row["status"] == "ACTIVE"
            and row["rrule"] == contract["rrule"]
            and (
                str(row["prompt"] or "") == _prompt(contract)
                or current_hash == contract["previous_delivery_prompt_sha256"]
            )
        ):
            continue
        if row["status"] != "PAUSED":
            raise RuntimeError(f"EXPECTED_PAUSED:{automation_id}:{row['status']}")
        if current_hash != contract["expected_prompt_sha256"]:
            raise RuntimeError(f"PROMPT_HASH_DRIFT:{automation_id}:{current_hash}")
        if row["rrule"] != contract["expected_rrule"]:
            raise RuntimeError(f"RRULE_DRIFT:{automation_id}:{row['rrule']}")


def _backup(database: Path, rows: dict[str, dict]) -> tuple[Path, Path]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    db_backup = BACKUP_DIR / f"northstar-delivery-only-{stamp}.db"
    rows_backup = BACKUP_DIR / f"northstar-delivery-only-{stamp}.automations.json"
    with sqlite3.connect(str(database), timeout=30) as source:
        with sqlite3.connect(str(db_backup)) as target:
            source.backup(target)
            if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("BACKUP_QUICK_CHECK_FAILED")
    rows_backup.write_text(
        json.dumps(
            {
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "source_database": str(database),
                "automations": rows,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return db_backup, rows_backup


def configure(database: Path, *, apply: bool) -> dict:
    with sqlite3.connect(str(database), timeout=30) as connection:
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("DATABASE_QUICK_CHECK_FAILED_BEFORE")
        runtime_rows = connection.execute(
            "SELECT automation_id,running FROM automation_runtime_state WHERE automation_id IN (?,?)",
            tuple(CONTRACTS),
        ).fetchall()
        if any(int(row[1] or 0) for row in runtime_rows):
            raise RuntimeError("AUTOMATION_CURRENTLY_RUNNING")
        rows = _rows(connection)
    _verify_prestate(rows)
    proposal = _proposal(rows)
    if not apply:
        return {"status": "DRY_RUN", "automations": proposal}
    if all(
        rows[automation_id]["name"] == item["name"]
        and rows[automation_id]["status"] == item["status"]
        and rows[automation_id]["rrule"] == item["rrule"]
        and rows[automation_id]["prompt"] == item["prompt"]
        for automation_id, item in proposal.items()
    ):
        return {"status": "ALREADY_APPLIED", "automations": proposal}

    db_backup, rows_backup = _backup(database, rows)
    connection = sqlite3.connect(str(database), timeout=30, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        updated_at = int(time.time() * 1000)
        for automation_id, item in proposal.items():
            cursor = connection.execute(
                "UPDATE automations SET name=?,prompt=?,status=?,rrule=?,next_run_at=?,updated_at=? WHERE id=?",
                (
                    item["name"],
                    item["prompt"],
                    item["status"],
                    item["rrule"],
                    item["next_run_at"],
                    updated_at,
                    automation_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"UPDATE_COUNT:{automation_id}:{cursor.rowcount}")
        connection.execute("COMMIT")
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()

    with sqlite3.connect(str(database), timeout=30) as verify:
        if verify.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("DATABASE_QUICK_CHECK_FAILED_AFTER")
        final_rows = _rows(verify)
    for automation_id, item in proposal.items():
        row = final_rows[automation_id]
        for field in ("name", "prompt", "status", "rrule", "next_run_at"):
            if row[field] != item[field]:
                raise RuntimeError(f"POSTCHECK_FAILED:{automation_id}:{field}")
    return {
        "status": "APPLIED",
        "database_backup": str(db_backup),
        "automation_rows_backup": str(rows_backup),
        "automations": proposal,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Configure WorkBuddy delivery-only tasks")
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = configure(args.database, apply=args.apply)
    printable = json.loads(json.dumps(result, ensure_ascii=False, default=str))
    for item in printable.get("automations", {}).values():
        item.pop("prompt", None)
    print(json.dumps(printable, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
