"""Generate the single Markdown delivery receipt for a WorkBuddy run.

The command is deliberately fail closed.  It reads WorkBuddy provenance and the
already-created Northstar artifacts, verifies the immutable evidence, and writes
only a receipt outside the protected runtime/archive trees.  It never calls Futu,
the model, an account API, or an order API.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from .workbuddy_provenance import (
    AUTOMATION_CONTRACTS,
    resolve_workbuddy_provenance,
)


OUTPUT_ROOT = Path(__file__).resolve().parent / "output"
ALLOWED_STATUSES = frozenset({"SUCCESS", "DUPLICATE_SUPPRESSED"})
ALLOWED_RECEIPT_SUFFIXES = frozenset({"", "-zh-CN"})

STATUS_ZH = {
    "SUCCESS": "成功",
    "DUPLICATE_SUPPRESSED": "重复运行已抑制",
    "RUN_PASS": "运行通过",
}
RUN_KIND_ZH = {
    "scheduled": "定时运行",
    "missed": "错过调度后的补偿运行",
    "manual_test": "手动测试",
    "on_demand_followup": "按需续跑",
}
IDENTITY_MODE_ZH = {
    "ACTIVE_AUTOMATION_SESSION": "活跃自动化会话",
    "ACTIVE_RUNTIME": "活跃调度运行时",
}
ACTION_ZH = {"BUY": "买入", "SELL": "卖出", "HOLD": "持有"}
TREND_ZH = {"UP": "上涨", "DOWN": "下跌", "SIDEWAYS": "震荡"}
EVENT_ZH = {
    "NO_NEW_TREND_EVENT": "无新趋势事件",
    "TREND_REVERSAL": "趋势反转",
    "TREND_CONTINUATION": "趋势延续",
}
STAGE_ZH = {
    "NONE": "无",
    "NEW_PLATEAU": "新钝化",
    "BUILDING": "形成中",
    "NEAR": "临近完成",
    "REACHED": "已完成",
}
EFFECT_ZH = {
    "NONE": "无调整",
    "OBSERVED_NO_ADJUSTMENT": "已观察但未调整",
    "NO_ACTION_TO_MODIFY": "无动作可调整",
}
ALIGNMENT_ZH = {
    "NOT_ACTIVE": "未激活",
    "NOT_APPLICABLE": "不适用",
}
SOURCE_ZH = {"FUTU_OPEND_LIVE": "富途 OpenD 实时直连"}
FRESHNESS_BASIS_ZH = {
    "LIVE_OPEND_RESPONSE": "OpenD 实时响应",
    "LIVE_OPEND_RESPONSE_MARKET_NOT_TRADING": "非交易时段的 OpenD 实时响应",
}
GATE_ZH = {
    "deployment_id_match": "部署 ID 匹配",
    "market_match": "市场匹配",
    "single_market_only": "仅单一市场",
    "combined_run_disallowed": "禁止合并市场运行",
    "data_ready": "数据就绪",
    "freshness_ready": "新鲜度门禁通过",
    "summary_errors_zero": "汇总错误数为零",
    "errors_empty": "错误列表为空",
    "execution_disabled": "执行功能已关闭",
    "account_access_disabled": "账户访问已关闭",
    "order_api_not_called": "未调用订单 API",
    "orders_empty": "订单列表为空",
    "fills_empty": "成交列表为空",
    "report_quality.decision_audit_complete": "决策审计完整",
    "report_quality.private_detail_policy_enforced": "私有细节策略已执行",
}


class ReceiptValidationError(RuntimeError):
    """Raised when a receipt cannot be backed by complete evidence."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReceiptValidationError(f"INVALID_JSON:{path}:{exc}") from exc
    if not isinstance(value, dict):
        raise ReceiptValidationError(f"JSON_ROOT_NOT_OBJECT:{path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise ReceiptValidationError(f"ARTIFACT_NOT_READABLE:{path}:{exc}") from exc
    return digest.hexdigest().upper()


def _cell(value: Any) -> str:
    return str(value if value is not None else "N/A").replace("|", "\\|").replace("\n", " ")


def _flag_zh(value: Any) -> str:
    if value is True:
        return "是（true）"
    if value is False:
        return "否（false）"
    return _cell(value)


def _enum_zh(value: Any, translations: Mapping[str, str]) -> str:
    raw = _cell(value)
    translated = translations.get(raw)
    return f"{translated}（{raw}）" if translated else raw


def _percent_delta(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "N/A"
    return f"{number:+.0%}"


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ReceiptValidationError(code)


def _artifact_paths(latest: Mapping[str, Any]) -> dict[str, Path]:
    artifacts = latest.get("artifacts")
    _require(isinstance(artifacts, Mapping), "LATEST_ARTIFACTS_MISSING")
    required = (
        "json",
        "markdown",
        "manifest",
        "archive_json",
        "archive_markdown",
        "archive_manifest",
    )
    paths: dict[str, Path] = {}
    for key in required:
        raw = artifacts.get(key)
        _require(isinstance(raw, str) and bool(raw.strip()), f"ARTIFACT_PATH_MISSING:{key}")
        paths[key] = Path(raw)
    return paths


def _validate_evidence(
    automation_id: str,
    market: str,
    provenance: Mapping[str, Any],
    latest: Mapping[str, Any],
    data: Mapping[str, Any],
    manifest: Mapping[str, Any],
    paths: Mapping[str, Path],
    runner_status: str,
    runner_exit_code: int,
) -> dict[str, str]:
    market = market.upper()
    deployment_id = f"NORTHSTAR_D1_{market}"
    _require(runner_status in ALLOWED_STATUSES, "RUNNER_STATUS_NOT_ALLOWED")
    _require(runner_exit_code == 0, "RUNNER_EXIT_CODE_NONZERO")
    _require(provenance.get("provenance_status") == "VERIFIED", "PROVENANCE_NOT_VERIFIED")
    _require(provenance.get("automation_id") == automation_id, "PROVENANCE_AUTOMATION_MISMATCH")
    _require(provenance.get("market") == market, "PROVENANCE_MARKET_MISMATCH")
    _require(provenance.get("deployment_id") == deployment_id, "PROVENANCE_DEPLOYMENT_MISMATCH")
    _require(latest.get("status") == "SUCCESS", "LATEST_NOT_SUCCESS")
    _require(latest.get("market") == market, "LATEST_MARKET_MISMATCH")
    _require(latest.get("deployment_id") == deployment_id, "LATEST_DEPLOYMENT_MISMATCH")
    _require(data.get("market") == market, "ARTIFACT_MARKET_MISMATCH")
    _require(data.get("deployment_id") == deployment_id, "ARTIFACT_DEPLOYMENT_MISMATCH")
    _require(data.get("run_verdict") == "RUN_PASS", "RUN_VERDICT_NOT_PASS")
    _require(data.get("data_ready") is True, "DATA_NOT_READY")
    _require(data.get("freshness_ready") is True, "FRESHNESS_NOT_READY")
    _require(data.get("report_quality_ready") is True, "REPORT_QUALITY_NOT_READY")
    _require(data.get("validation_status") == "NOT_EVALUATED", "VALIDATION_STATUS_DRIFT")
    _require(data.get("formal_publish_allowed") is False, "FORMAL_PUBLISH_BOUNDARY_DRIFT")
    _require(data.get("errors") == [], "ERRORS_NOT_EMPTY")

    gates = data.get("run_gate_checks")
    _require(isinstance(gates, Mapping) and len(gates) == 13, "RUN_GATE_COUNT_NOT_13")
    _require(all(value is True for value in gates.values()), "RUN_GATE_FAILED")
    quality = data.get("report_quality_checks")
    _require(isinstance(quality, Mapping) and bool(quality), "REPORT_QUALITY_CHECKS_MISSING")
    _require(all(value is True for value in quality.values()), "REPORT_QUALITY_CHECK_FAILED")

    isolation = data.get("market_isolation") or {}
    _require(isolation.get("single_market_only") is True, "MARKET_ISOLATION_DISABLED")
    _require(isolation.get("combined_run_allowed") is False, "COMBINED_RUN_ALLOWED")
    execution = data.get("execution") or {}
    _require(execution.get("enabled") is False, "EXECUTION_ENABLED")
    _require(execution.get("account_access") is False, "ACCOUNT_ACCESS_ENABLED")
    _require(execution.get("order_api_called") is False, "ORDER_API_CALLED")
    _require(data.get("orders") == [], "ORDERS_NOT_EMPTY")
    _require(data.get("fills") == [], "FILLS_NOT_EMPTY")

    run = data.get("run") or {}
    _require(run.get("run_id") == latest.get("run_id"), "RUN_ID_MISMATCH")
    _require(manifest.get("run_id") == latest.get("run_id"), "MANIFEST_RUN_ID_MISMATCH")
    _require(manifest.get("signal_asof") == data.get("signal_asof"), "SIGNAL_ASOF_MISMATCH")
    if runner_status == "SUCCESS":
        _require(
            run.get("automation_run_id") == provenance.get("automation_run_id"),
            "AUTOMATION_RUN_ID_MISMATCH",
        )
        _require(
            run.get("automation_conversation_id")
            == provenance.get("automation_conversation_id"),
            "AUTOMATION_CONVERSATION_ID_MISMATCH",
        )

    signals = data.get("signals")
    _require(isinstance(signals, list) and bool(signals), "SIGNALS_MISSING")
    for signal in signals:
        _require(isinstance(signal, Mapping), "SIGNAL_NOT_OBJECT")
        _require(
            isinstance(signal.get("name"), str) and bool(signal["name"].strip()),
            "SIGNAL_COMPANY_NAME_MISSING",
        )
        _require(signal.get("bar_confirmed") is True, "UNCONFIRMED_SIGNAL_BAR")
        audit = signal.get("decision_audit") or {}
        _require(
            all(isinstance(audit.get(key), Mapping) for key in ("trend", "structure", "sequence", "decision")),
            "DECISION_AUDIT_INCOMPLETE",
        )
        market_data = signal.get("market_data") or {}
        _require(market_data.get("source") == "FUTU_OPEND_LIVE", "NON_FUTU_SOURCE")
        _require(market_data.get("cache_used") is False, "CACHE_USED")
        _require(market_data.get("fallback_used") is False, "FALLBACK_USED")

    hashes = {key: _sha256(path) for key, path in paths.items()}
    manifest_hashes = manifest.get("sha256") or {}
    _require(hashes["archive_json"] == str(manifest_hashes.get("json", "")).upper(), "ARCHIVE_JSON_HASH_MISMATCH")
    _require(hashes["archive_markdown"] == str(manifest_hashes.get("markdown", "")).upper(), "ARCHIVE_MARKDOWN_HASH_MISMATCH")
    _require(hashes["json"] == hashes["archive_json"], "CANONICAL_JSON_DIFFERS_FROM_ARCHIVE")
    _require(hashes["markdown"] == hashes["archive_markdown"], "CANONICAL_MARKDOWN_DIFFERS_FROM_ARCHIVE")
    _require(hashes["manifest"] == hashes["archive_manifest"], "CANONICAL_MANIFEST_DIFFERS_FROM_ARCHIVE")
    return hashes


def build_receipt_document(
    automation_id: str,
    market: str,
    provenance: Mapping[str, Any],
    *,
    output_root: Path = OUTPUT_ROOT,
    runner_status: str = "SUCCESS",
    runner_exit_code: int = 0,
) -> tuple[str, str]:
    """Return ``(receipt_markdown, automation_run_id)`` after full validation."""

    market = market.upper()
    _require(automation_id in AUTOMATION_CONTRACTS, "AUTOMATION_NOT_ALLOWED")
    _require(AUTOMATION_CONTRACTS[automation_id]["market"] == market, "AUTOMATION_MARKET_MISMATCH")
    latest_path = output_root / market.lower() / ".runtime" / "latest.json"
    latest = _read_json(latest_path)
    paths = _artifact_paths(latest)
    data = _read_json(paths["archive_json"])
    manifest = _read_json(paths["archive_manifest"])
    hashes = _validate_evidence(
        automation_id,
        market,
        provenance,
        latest,
        data,
        manifest,
        paths,
        runner_status,
        runner_exit_code,
    )

    run = data["run"]
    summary = data["summary"]
    automation_run_id = str(provenance["automation_run_id"])
    lines = [
        f"# Northstar-D1 {market} WorkBuddy 回执",
        "",
        f"> {_enum_zh(runner_status, STATUS_ZH)} · 仅研究（`RESEARCH_ONLY`） · "
        "模型尚未评估（`NOT_EVALUATED`） · 禁止正式发布（`formal_publish_allowed=false`） · "
        "零执行（`ZERO_EXECUTION`）",
        "",
        "## 1. 运行身份与结果",
        "",
        "| 字段 | 值 |",
        "|---|---|",
        f"| 状态 | {_enum_zh(runner_status, STATUS_ZH)} |",
        f"| 部署 ID | `{data['deployment_id']}` |",
        f"| WorkBuddy 自动化 ID | `{automation_id}` |",
        f"| WorkBuddy 自动化运行 ID | `{automation_run_id}` |",
        f"| WorkBuddy 对话 ID | `{provenance['automation_conversation_id']}` |",
        f"| WorkBuddy 运行类型 | {_enum_zh(provenance['automation_run_kind'], RUN_KIND_ZH)} |",
        f"| 原始运行类型 | {_enum_zh(provenance['automation_origin_run_kind'], RUN_KIND_ZH)} |",
        f"| 身份模式 | {_enum_zh(provenance['automation_identity_mode'], IDENTITY_MODE_ZH)} |",
        f"| Northstar 归档运行 ID | `{run['run_id']}` |",
        f"| 调度槽 | `{run['slot_key']}` |",
        f"| 尝试次数 | `{run['attempt']}` |",
        f"| 计划时间 | `{run['scheduled_at_local']}` |",
        f"| 实际开始时间 | `{run['started_at_local']}` |",
        f"| 完成时间 | `{latest['completed_at_utc']}` |",
        f"| 退出码 | `{runner_exit_code}` |",
        f"| 运行判定 | {_enum_zh(data['run_verdict'], STATUS_ZH)} |",
        f"| 信号日期 | `{data['signal_asof']}` |",
        f"| 买入 / 卖出 / 持有（BUY / SELL / HOLD） | `{summary['buy']} / {summary['sell']} / {summary['hold']}` |",
        "",
        "## 2. 路径与完整 SHA256",
        "",
        "| 产物 | 完整路径 | SHA256 |",
        "|---|---|---|",
    ]
    labels = {
        "json": "当前 JSON",
        "markdown": "当前 Markdown",
        "manifest": "当前清单",
        "archive_json": "归档 JSON",
        "archive_markdown": "归档 Markdown",
        "archive_manifest": "归档清单",
    }
    for key in labels:
        lines.append(f"| {labels[key]} | `{paths[key]}` | `{hashes[key]}` |")

    lines.extend([
        "",
        "## 3. 运行与报告门禁",
        "",
        "| 门禁 | 结果 |",
        "|---|---|",
    ])
    for key, value in data["run_gate_checks"].items():
        gate = _enum_zh(key, GATE_ZH)
        lines.append(f"| {gate} | {_flag_zh(value)} |")
    for key, value in data["report_quality_checks"].items():
        raw_gate = f"report_quality.{_cell(key)}"
        gate = _enum_zh(raw_gate, GATE_ZH)
        lines.append(f"| {gate} | {_flag_zh(value)} |")

    lines.extend([
        "",
        "## 4. Futu 新鲜度证据",
        "",
        "| 标的代码 | 公司名称 | 数据源 | 查询往返耗时（秒） | 快照年龄（秒） | 新鲜度依据 | 使用缓存 | 使用降级数据 |",
        "|---|---|---|---:|---:|---|---|---|",
    ])
    for signal in data["signals"]:
        market_data = signal["market_data"]
        snapshot = market_data.get("snapshot") or {}
        lines.append(
            f"| {_cell(signal['symbol'])} | {_cell(signal['name'])} | "
            f"{_enum_zh(market_data.get('source'), SOURCE_ZH)} | "
            f"{_cell(market_data.get('round_trip_seconds'))} | {_cell(snapshot.get('age_seconds'))} | "
            f"{_enum_zh(snapshot.get('freshness_basis'), FRESHNESS_BASIS_ZH)} | "
            f"{_flag_zh(market_data.get('cache_used'))} | "
            f"{_flag_zh(market_data.get('fallback_used'))} |"
        )

    lines.extend([
        "",
        "## 5. 结构审计（全标的）",
        "",
        "| 标的代码 | 公司名称 | 候选动作→输出动作 | 市场趋势 | 趋势事件 | 底部结构阶段/天数 | 顶部结构阶段/天数 | 结构作用 | 比例变化 | 置信度变化 | 模型说明 |",
        "|---|---|---|---|---|---|---|---|---:|---:|---|",
    ])
    for signal in data["signals"]:
        audit = signal["decision_audit"]
        structure = audit["structure"]
        decision = audit["decision"]
        lines.append(
            f"| {_cell(signal['symbol'])} | {_cell(signal['name'])} | "
            f"{_enum_zh(decision.get('candidate_action'), ACTION_ZH)}→"
            f"{_enum_zh(decision.get('emitted_action'), ACTION_ZH)} | "
            f"{_enum_zh(audit['trend'].get('market_state'), TREND_ZH)} | "
            f"{_enum_zh(audit['trend'].get('event'), EVENT_ZH)} | "
            f"{_enum_zh(structure['bottom'].get('stage'), STAGE_ZH)}/{_cell(structure['bottom'].get('persistence_days'))} 天 | "
            f"{_enum_zh(structure['top'].get('stage'), STAGE_ZH)}/{_cell(structure['top'].get('persistence_days'))} 天 | "
            f"{_enum_zh(structure.get('effect'), EFFECT_ZH)} | {_percent_delta(structure.get('fraction_delta'))} | "
            f"{_percent_delta(structure.get('confidence_delta'))} | {_cell(decision.get('reason'))} |"
        )

    lines.extend([
        "",
        "## 6. 序列审计（全标的）",
        "",
        "| 标的代码 | 公司名称 | 计数 | 阶段 | 方向 | 与动作关系 | 序列作用 | 置信度变化 |",
        "|---|---|---:|---|---|---|---|---:|",
    ])
    for signal in data["signals"]:
        sequence = signal["decision_audit"]["sequence"]
        lines.append(
            f"| {_cell(signal['symbol'])} | {_cell(signal['name'])} | "
            f"{_cell(sequence.get('count'))} | "
            f"{_enum_zh(sequence.get('stage'), STAGE_ZH)} | "
            f"{_enum_zh(sequence.get('direction'), ACTION_ZH)} | "
            f"{_enum_zh(sequence.get('alignment'), ALIGNMENT_ZH)} | "
            f"{_enum_zh(sequence.get('effect'), EFFECT_ZH)} | "
            f"{_percent_delta(sequence.get('confidence_delta'))} |"
        )

    lines.extend([
        "",
        "## 综述结论",
        "",
        f"本次 {market} 运行证据完整，13 项运行门禁与全部报告质量门禁通过；"
        "仅作为内部研究结果。未访问账户、持仓、余额或购买力，未调用订单 API，未生成订单或成交。",
        "",
    ])
    return "\n".join(lines), automation_run_id


def write_receipt(
    automation_id: str,
    market: str,
    provenance: Mapping[str, Any],
    *,
    output_root: Path = OUTPUT_ROOT,
    runner_status: str = "SUCCESS",
    runner_exit_code: int = 0,
    filename_suffix: str = "",
) -> Path:
    _require(filename_suffix in ALLOWED_RECEIPT_SUFFIXES, "RECEIPT_SUFFIX_NOT_ALLOWED")
    document, automation_run_id = build_receipt_document(
        automation_id,
        market,
        provenance,
        output_root=output_root,
        runner_status=runner_status,
        runner_exit_code=runner_exit_code,
    )
    receipt_dir = output_root / market.lower() / "receipts"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipt_dir / f"{automation_run_id}{filename_suffix}.md"
    encoded = document.encode("utf-8")
    try:
        with receipt_path.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        if receipt_path.read_bytes() != encoded:
            raise ReceiptValidationError(f"RECEIPT_EXISTS_WITH_DIFFERENT_CONTENT:{receipt_path}")
    return receipt_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成一份经过验证的 Northstar WorkBuddy 中文 Markdown 回执")
    parser.add_argument("--automation-id", required=True)
    parser.add_argument("--market", required=True, choices=("HK", "US"))
    parser.add_argument("--runner-status", default="SUCCESS", choices=sorted(ALLOWED_STATUSES))
    parser.add_argument("--runner-exit-code", type=int, default=0)
    parser.add_argument(
        "--filename-suffix",
        default="",
        choices=sorted(ALLOWED_RECEIPT_SUFFIXES),
        help="仅用于保留既有审计回执时生成独立中文副本",
    )
    args = parser.parse_args(argv)

    provenance = resolve_workbuddy_provenance(args.automation_id)
    try:
        receipt_path = write_receipt(
            args.automation_id,
            args.market,
            provenance,
            runner_status=args.runner_status,
            runner_exit_code=args.runner_exit_code,
            filename_suffix=args.filename_suffix,
        )
    except ReceiptValidationError as exc:
        print(json.dumps({
            "status": "FAILED_CLOSED",
            "reason": str(exc),
            "receipt_written": False,
            "futu_called": False,
            "model_called": False,
            "account_access": False,
            "order_api_called": False,
        }, ensure_ascii=False))
        return 4

    print(json.dumps({
        "status": "RECEIPT_READY",
        "receipt_path": str(receipt_path),
        "receipt_sha256": _sha256(receipt_path),
        "present_files_count": 1,
        "present_only_this_markdown": True,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
