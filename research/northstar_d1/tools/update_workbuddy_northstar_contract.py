"""Version-locked WorkBuddy automation contract update for Northstar-D1.

The script backs up the live SQLite database and both automation rows before
changing only the two allowlisted prompts and RRULE strings.  It does not touch
run history, sessions, Northstar runtime state, or automation memory files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


DATABASE = Path(r"C:\Users\RoyGoode\.workbuddy\workbuddy.db")
BACKUP_DIR = Path(r"C:\Users\RoyGoode\.workbuddy\automation-backups")
EXPECTED = {
    "automation-1785736457372": {
        "market": "HK",
        "old_prompt_sha256": "C1DEBDA40BB4D2D3B4EDE29B17A25308439D466B14D73CB7CA4E5E450C0C6DB4",
        "old_rrule": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=16;BYMINUTE=25",
        "new_rrule": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=16;BYMINUTE=20",
        "timezone": "Asia/Hong_Kong",
    },
    "automation-1785736457815": {
        "market": "US",
        "old_prompt_sha256": "0E560F071D67EBAEE8F26682BFA7E8CF9026B79A7F8D288599620DE7B7A96A2C",
        "old_rrule": "FREQ=WEEKLY;BYDAY=TU,WE,TH,FR,SA;BYHOUR=10;BYMINUTE=10",
        "new_rrule": "FREQ=WEEKLY;BYDAY=TU,WE,TH,FR,SA;BYHOUR=10;BYMINUTE=0",
        "timezone": "Asia/Shanghai",
    },
}
CORE_MARKERS = (
    "[WORKBUDDY_NO_MEMORY_V1]",
    "[WORKBUDDY_MARKDOWN_ONLY_V1]",
    "[WORKBUDDY_RECEIPT_V1]",
    "[WORKBUDDY_CONTRACT_OVERRIDE_V1_9_3]",
)
LANGUAGE_MARKER = "[WORKBUDDY_ZH_CN_DELIVERY_V1]"
COMPANY_NAME_MARKER = "[WORKBUDDY_COMPANY_NAME_V1]"
MARKERS = CORE_MARKERS + (LANGUAGE_MARKER, COMPANY_NAME_MARKER)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest().upper()


def _new_tail(automation_id: str, market: str) -> str:
    command_base = (
        "C:\\Users\\RoyGoode\\AppData\\Local\\Programs\\Python\\Python312\\python.exe "
        "-m research.northstar_d1.workbuddy_receipt "
        f"--automation-id {automation_id} --market {market}"
    )
    return f"""8. FINAL DELIVERY IS ONE VERIFIED MARKDOWN RECEIPT
- The complete structure table, sequence table, identity fields, six full paths, full SHA256 values, Futu freshness evidence and boundary labels must be in the deterministic receipt Markdown generated in section 9.
- All human-readable receipt headings, field names, status explanations, enum explanations, and the normal assistant response must be Chinese-first. Keep raw technical IDs and enum tokens in backticks or Chinese explanations followed by parentheses. Never deliver English-first tables.
- Every per-symbol receipt table must include both the instrument code and the company name from the verified Northstar signal artifact. A code-only symbol table is incomplete and must fail closed.
- Call present_files exactly once with exactly one file: that receipt Markdown. Never present canonical/archive JSON, manifest, or a second attachment.
- The normal assistant response must stay below 2,000 characters and contain only Chinese-first status, Automation Run ID, Northstar Archive Run ID, receipt path/hash, and: 仅研究（RESEARCH_ONLY）/ 模型尚未评估（NOT_EVALUATED）/ 禁止正式发布（formal_publish_allowed=false）/ 零执行（ZERO_EXECUTION）.
- Do not reproduce the full tables in the normal assistant response. The receipt is the authoritative, non-truncated delivery.
- Any memory access, extra attachment, missing receipt field, or incomplete table is FAILED_CLOSED.

9. VERIFIED RECEIPT AND WORKBUDDY PROVENANCE [WORKBUDDY_RECEIPT_V1]
- Do not run the standalone workbuddy_provenance command. The receipt helper performs that read-only provenance verification itself and fails closed on any identity mismatch.
- After the single market command exits 0, invoke exactly one of these two commands according to its reported status; never invoke both:
  - Fresh success:
{command_base} --runner-status SUCCESS --runner-exit-code 0
  - Duplicate suppression:
{command_base} --runner-status DUPLICATE_SUPPRESSED --runner-exit-code 0
- The helper may read WorkBuddy SQLite and the current Northstar artifacts. Its only write is a new non-overwriting Markdown under output/{market.lower()}/receipts/. It does not call Futu, the model, accounts, positions, balances, orders or execution APIs.
- Require helper status=RECEIPT_READY, present_files_count=1 and present_only_this_markdown=true. Present exactly receipt_path and no other file.
- If the market command is nonzero, SKIPPED_EARLY, or the receipt helper fails, do not retry either command; report the original status as FAILED_CLOSED or SKIPPED_EARLY with no attachment.
- WorkBuddy Automation Run ID and Conversation ID come only from verified SQLite provenance. Northstar Archive Run ID remains a separate model/archive identity.
- This receipt contract preserves RESEARCH_ONLY, validation_status=NOT_EVALUATED, formal_publish_allowed=false, single-market isolation and ZERO_EXECUTION.
"""


def transform_prompt(prompt: str, automation_id: str, market: str) -> str:
    if all(marker in prompt for marker in MARKERS):
        return prompt
    if all(marker in prompt for marker in CORE_MARKERS):
        section8 = prompt.find("8. FINAL DELIVERY IS ONE VERIFIED MARKDOWN RECEIPT")
        if section8 < 0:
            raise RuntimeError(f"CURRENT_SECTION_8_NOT_FOUND:{automation_id}")
        missing_markers = [marker for marker in MARKERS if marker not in prompt]
        if not missing_markers:
            return prompt
        return "\n".join(missing_markers) + "\n" + prompt[:section8] + _new_tail(automation_id, market)
    if any(marker in prompt for marker in CORE_MARKERS):
        raise RuntimeError(f"PARTIALLY_PATCHED_PROMPT:{automation_id}")
    section8 = prompt.find("8. FINAL RESPONSE MUST SHOW THE AUDIT RESULTS")
    if section8 < 0:
        raise RuntimeError(f"SECTION_8_NOT_FOUND:{automation_id}")
    old_sentence = (
        "The read-only scheduler provenance check in section 9 is the only allowed "
        "post-invocation command; it must never call Futu or the model."
    )
    if old_sentence not in prompt[:section8]:
        raise RuntimeError(f"POST_INVOCATION_SENTENCE_NOT_FOUND:{automation_id}")
    prefix = "\n".join(MARKERS) + "\n"
    prefix += (
        "MANDATORY CONTRACT OVERRIDE: these marker rules supersede every conflicting "
        "memory, attachment, provenance, and final-response instruction below. The "
        "WorkBuddy system reminder must use restricted mode. Never read, create, edit, "
        "or summarize memory. Deliver exactly one verified Markdown receipt.\n\n"
    )
    body = prompt[:section8].replace(
        old_sentence,
        "The receipt command in section 9 is the only allowed post-invocation command; "
        "it may write only the receipt and must never call Futu or the model.",
    )
    return prefix + body + _new_tail(automation_id, market)


def _read_rows(connection: sqlite3.Connection) -> dict[str, dict]:
    connection.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in EXPECTED)
    rows = connection.execute(
        f"SELECT * FROM automations WHERE id IN ({placeholders}) ORDER BY id",
        tuple(EXPECTED),
    ).fetchall()
    if len(rows) != len(EXPECTED):
        raise RuntimeError("ALLOWLIST_AUTOMATION_ROW_MISSING")
    return {str(row["id"]): dict(row) for row in rows}


def _verify_original(rows: dict[str, dict]) -> None:
    for automation_id, contract in EXPECTED.items():
        row = rows[automation_id]
        prompt = str(row["prompt"])
        if all(marker in prompt for marker in CORE_MARKERS):
            if row["rrule"] != contract["new_rrule"]:
                raise RuntimeError(f"PATCHED_RRULE_DRIFT:{automation_id}")
            continue
        if _sha(prompt) != contract["old_prompt_sha256"]:
            raise RuntimeError(f"UNREVIEWED_PROMPT_HASH:{automation_id}:{_sha(prompt)}")
        if row["rrule"] != contract["old_rrule"]:
            raise RuntimeError(f"UNREVIEWED_RRULE:{automation_id}:{row['rrule']}")


def _proposed(rows: dict[str, dict]) -> dict[str, dict]:
    result = {}
    for automation_id, contract in EXPECTED.items():
        row = rows[automation_id]
        prompt = transform_prompt(str(row["prompt"]), automation_id, contract["market"])
        next_local = datetime.fromtimestamp(
            float(row["next_run_at"]) / 1000,
            tz=timezone.utc,
        ).astimezone(ZoneInfo(contract["timezone"]))
        result[automation_id] = {
            "market": contract["market"],
            "prompt": prompt,
            "prompt_sha256": _sha(prompt),
            "prompt_length": len(prompt),
            "rrule": contract["new_rrule"],
            "next_run_at": row["next_run_at"],
            "next_run_local": next_local.isoformat(),
            "markers": {marker: prompt.count(marker) for marker in MARKERS},
        }
    return result


def _backup(database: Path, rows: dict[str, dict]) -> tuple[Path, Path]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    db_backup = BACKUP_DIR / f"northstar-contract-{stamp}.db"
    json_backup = BACKUP_DIR / f"northstar-contract-{stamp}.automations.json"
    if db_backup.exists() or json_backup.exists():
        raise RuntimeError("BACKUP_PATH_COLLISION")
    source = sqlite3.connect(str(database), timeout=30)
    target = sqlite3.connect(str(db_backup))
    try:
        source.backup(target)
        if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("DATABASE_BACKUP_QUICK_CHECK_FAILED")
    finally:
        target.close()
        source.close()
    payload = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_database": str(database),
        "automations": rows,
    }
    with json_backup.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return db_backup, json_backup


def apply_update(database: Path) -> dict:
    connection = sqlite3.connect(str(database), timeout=30)
    try:
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("DATABASE_QUICK_CHECK_FAILED_BEFORE")
        rows = _read_rows(connection)
        _verify_original(rows)
        proposal = _proposed(rows)
        if all(
            rows[automation_id]["prompt"] == item["prompt"]
            and rows[automation_id]["rrule"] == item["rrule"]
            for automation_id, item in proposal.items()
        ):
            return {"status": "ALREADY_APPLIED", "automations": proposal}
    finally:
        connection.close()

    db_backup, json_backup = _backup(database, rows)
    connection = sqlite3.connect(str(database), timeout=30, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        now_ms = int(time.time() * 1000)
        for automation_id, item in proposal.items():
            cursor = connection.execute(
                "UPDATE automations SET prompt=?, rrule=?, updated_at=? WHERE id=?",
                (item["prompt"], item["rrule"], now_ms, automation_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"AUTOMATION_UPDATE_COUNT:{automation_id}:{cursor.rowcount}")
        connection.execute("COMMIT")
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()

    verify = sqlite3.connect(str(database), timeout=30)
    try:
        if verify.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("DATABASE_QUICK_CHECK_FAILED_AFTER")
        final_rows = _read_rows(verify)
        final = _proposed(final_rows)
        for automation_id, item in final.items():
            if final_rows[automation_id]["prompt"] != item["prompt"]:
                raise RuntimeError(f"PROMPT_POSTCHECK_FAILED:{automation_id}")
            if final_rows[automation_id]["rrule"] != item["rrule"]:
                raise RuntimeError(f"RRULE_POSTCHECK_FAILED:{automation_id}")
    finally:
        verify.close()
    return {
        "status": "APPLIED",
        "database_backup": str(db_backup),
        "automation_rows_backup": str(json_backup),
        "automations": final,
    }


def inspect(database: Path) -> dict:
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("DATABASE_QUICK_CHECK_FAILED")
        rows = _read_rows(connection)
        _verify_original(rows)
        return {"status": "DRY_RUN", "automations": _proposed(rows)}
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Update the two Northstar WorkBuddy contracts")
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = apply_update(args.database) if args.apply else inspect(args.database)
    printable = json.loads(json.dumps(result, ensure_ascii=False, default=str))
    for item in printable.get("automations", {}).values():
        item.pop("prompt", None)
    print(json.dumps(printable, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
