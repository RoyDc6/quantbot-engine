from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path


DB_PATH = Path(r"C:\Users\RoyGoode\.workbuddy\workbuddy.db")
BACKUP_DIR = Path(r"C:\Users\RoyGoode\.workbuddy\automation-backups")
RECEIPT_PATH = Path(r"E:\quant\research\quantbot_signal_only\workbuddy_update_receipt.json")

PROMPTS = {
    "automation-1779886038083": """不要执行 unified_runner.py。

本自动化只审核并交付 Windows 计划任务已经生成的港股信号报告。QuantBot HK 已切换为 RESEARCH_ONLY；不得访问或操作 Futu 账户、持仓、余额、订单或成交 API。

读取当天文件：
- E:\\quant\\output\\schedule_hk.log
- E:\\quant\\reports\\{today}_hk_v3.md
- E:\\quant\\paper_trading\\signals\\{today}_HK.json

日志与证据规则：
- schedule_hk.log 是历史追加日志，只取最后一次出现的“QuantBot Unified Runner v2.2”起至文件末尾，作为本次运行日志。
- 只依据该日志段和当天文件判断结果，不得引用历史运行替代当天证据。
- 信号 JSON 必须满足 execution.mode=RESEARCH_ONLY、execution.enabled=false、execution.account_access=false、execution.order_api_called=false、orders=[]。任一条件不满足，明确报告 FAILED_CLOSED，不得描述为信号模式成功。

输出要求：
1. 先给简洁摘要：计划任务是否完成、当天报告是否存在、signal_asof、BUY/SELL/HOLD/REDUCED 数量、数据或模型错误。
2. 明确写出“账户访问：关闭；订单 API：未调用；订单：0”。
3. 交付 E:\\quant\\reports\\{today}_hk_v3.md 作为主要报告附件或链接。
4. 成功时附最后一次运行日志段的关键片段；失败时附错误和该日志段。
5. 不得重新生成信号，不得执行 Git 命令，不得创建、修改、取消任何订单。
""",
    "automation-1779886038108": """不要执行 unified_runner.py。

本自动化只审核并交付 Windows 计划任务已经生成的美股信号报告。QuantBot US 已切换为 RESEARCH_ONLY；不得访问或操作 Futu 账户、持仓、余额、订单或成交 API。

读取当天文件：
- E:\\quant\\output\\schedule_us.log
- E:\\quant\\reports\\{today}_us_v3.md
- E:\\quant\\paper_trading\\signals\\{today}_US.json

日志与证据规则：
- schedule_us.log 是历史追加日志，只取最后一次出现的“QuantBot Unified Runner v2.2”起至文件末尾，作为本次运行日志。
- 只依据该日志段和当天文件判断结果，不得引用历史运行替代当天证据。
- 信号 JSON 必须满足 execution.mode=RESEARCH_ONLY、execution.enabled=false、execution.account_access=false、execution.order_api_called=false、orders=[]。任一条件不满足，明确报告 FAILED_CLOSED，不得描述为信号模式成功。

输出要求：
1. 先给简洁摘要：计划任务是否完成、当天报告是否存在、signal_asof、BUY/SELL/HOLD/REDUCED 数量、数据或模型错误。
2. 明确写出“账户访问：关闭；订单 API：未调用；订单：0”。
3. 交付 E:\\quant\\reports\\{today}_us_v3.md 作为主要报告附件或链接。
4. 成功时附最后一次运行日志段的关键片段；失败时附错误和该日志段。
5. 不得重新生成信号，不得执行 Git 命令，不得创建、修改、取消任何订单。
""",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup_path = BACKUP_DIR / f"workbuddy-before-quantbot-signal-only-{timestamp}.db"
    before: dict[str, dict] = {}
    after: dict[str, dict] = {}

    source = sqlite3.connect(DB_PATH)
    source.row_factory = sqlite3.Row
    try:
        for automation_id in PROMPTS:
            row = source.execute("SELECT * FROM automations WHERE id=?", (automation_id,)).fetchone()
            if row is None:
                raise RuntimeError(f"automation missing: {automation_id}")
            before[automation_id] = dict(row)

        backup = sqlite3.connect(backup_path)
        try:
            source.backup(backup)
        finally:
            backup.close()

        now_ms = int(time.time() * 1000)
        source.execute("BEGIN IMMEDIATE")
        for automation_id, prompt in PROMPTS.items():
            source.execute(
                "UPDATE automations SET prompt=?, updated_at=? WHERE id=?",
                (prompt, now_ms, automation_id),
            )
        source.commit()

        for automation_id in PROMPTS:
            row = source.execute("SELECT * FROM automations WHERE id=?", (automation_id,)).fetchone()
            after[automation_id] = dict(row)
    except Exception:
        source.rollback()
        raise
    finally:
        source.close()

    allowed_changes = {"prompt", "updated_at"}
    verification: dict[str, dict] = {}
    for automation_id in PROMPTS:
        changed = sorted(
            key for key in before[automation_id]
            if before[automation_id][key] != after[automation_id][key]
        )
        if set(changed) - allowed_changes:
            raise RuntimeError(f"unexpected fields changed for {automation_id}: {changed}")
        if after[automation_id]["prompt"] != PROMPTS[automation_id]:
            raise RuntimeError(f"prompt verification failed: {automation_id}")
        if after[automation_id]["status"] != "ACTIVE":
            raise RuntimeError(f"automation not active: {automation_id}")
        verification[automation_id] = {
            "name": after[automation_id]["name"],
            "status": after[automation_id]["status"],
            "rrule": after[automation_id]["rrule"],
            "changed_fields": changed,
            "research_only_required": "execution.mode=RESEARCH_ONLY" in after[automation_id]["prompt"],
            "account_access_forbidden": "execution.account_access=false" in after[automation_id]["prompt"],
            "order_api_forbidden": "execution.order_api_called=false" in after[automation_id]["prompt"],
        }

    receipt = {
        "updated_at": datetime.now().astimezone().isoformat(),
        "database": str(DB_PATH),
        "backup": str(backup_path),
        "backup_sha256": sha256(backup_path),
        "verification": verification,
    }
    RECEIPT_PATH.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
