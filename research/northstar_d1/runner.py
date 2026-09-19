"""CLI runner for the independent research-only Northstar-D1 model."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List
from zoneinfo import ZoneInfo

from .data import CompletedDailyDataSource
from .model import NorthstarD1Model
from .runtime import RunCoordinator, safe_run_id


PACKAGE_DIR = Path(__file__).resolve().parent
OUTPUT_ROOT = PACKAGE_DIR / "output"
MARKET_DEPLOYMENTS = {
    "HK": {
        "deployment_id": "NORTHSTAR_D1_HK",
        "deployment_name": "Northstar-D1 HK",
        "timezone": "Asia/Hong_Kong",
        "schedule_timezone": "Asia/Hong_Kong",
        "schedule_hour": 16,
        "schedule_minute": 20,
        "schedule_weekdays": (0, 1, 2, 3, 4),
    },
    "US": {
        "deployment_id": "NORTHSTAR_D1_US",
        "deployment_name": "Northstar-D1 US",
        "timezone": "America/New_York",
        "schedule_timezone": "Asia/Shanghai",
        "schedule_hour": 10,
        "schedule_minute": 0,
        "schedule_weekdays": (1, 2, 3, 4, 5),
    },
}


def deployment_for(market: str) -> Dict[str, str]:
    market = market.upper()
    if market not in MARKET_DEPLOYMENTS:
        raise ValueError(f"unsupported market deployment: {market}")
    return MARKET_DEPLOYMENTS[market]


def validate_market_symbols(
    market: str,
    symbols: Iterable[str] | None,
) -> List[str] | None:
    if symbols is None:
        return None
    market = market.upper()
    requested = list(symbols)
    wrong_market = [
        symbol for symbol in requested if not symbol.endswith(f".{market}")
    ]
    if wrong_market:
        raise ValueError(
            f"cross-market symbols are not allowed in {market} deployment: "
            f"{wrong_market}"
        )
    return requested


def _decision_audit_complete(signal: Dict) -> bool:
    audit = signal.get("decision_audit")
    if not isinstance(audit, dict):
        return False
    trend = audit.get("trend")
    structure = audit.get("structure")
    sequence = audit.get("sequence")
    decision = audit.get("decision")
    if not all(
        isinstance(value, dict)
        for value in (trend, structure, sequence, decision)
    ):
        return False
    return bool(
        trend.get("market_state")
        and trend.get("event")
        and isinstance(structure.get("bottom"), dict)
        and isinstance(structure.get("top"), dict)
        and structure.get("effect")
        and sequence.get("stage")
        and sequence.get("direction")
        and sequence.get("alignment")
        and sequence.get("effect")
        and decision.get("signal_type")
        and isinstance(decision.get("reason_codes"), list)
        and decision.get("candidate_action") in {"BUY", "SELL", "HOLD"}
        and decision.get("emitted_action") == signal.get("action")
    )


def run_market(
    market: str,
    *,
    symbols: Iterable[str] | None = None,
    now: datetime | None = None,
    data_source: CompletedDailyDataSource | None = None,
    model: NorthstarD1Model | None = None,
) -> Dict:
    """Scan one market without loading VP, LLM, FusionController or an account."""

    market = market.upper()
    deployment = deployment_for(market)
    source = data_source or CompletedDailyDataSource()
    source_contract_enforced = bool(
        getattr(source, "live_contract_enforced", False)
    )
    northstar = model or NorthstarD1Model(source.config)
    requested = validate_market_symbols(market, symbols)
    universe = source.symbols(market, requested)

    generated_at = now or datetime.now(ZoneInfo(deployment["timezone"]))
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=ZoneInfo(deployment["timezone"]))

    signals: List[Dict] = []
    errors: List[Dict] = []
    for symbol in universe:
        try:
            bars = source.fetch(symbol, now=now)
            info = source.universe.get_info(symbol) or {}
            signal = northstar.analyze(
                bars,
                symbol=symbol,
                name=info.get("name", symbol),
                market=market,
                bar_confirmed=bool(bars.attrs.get("bar_confirmed", False)),
            )
            signal["deployment_id"] = deployment["deployment_id"]
            signals.append(signal)
        except Exception as exc:
            errors.append(
                {
                    "symbol": symbol,
                    "error": str(exc),
                    "fail_closed": True,
                    "signal_emitted": False,
                }
            )

    signals.sort(
        key=lambda item: (
            {"BUY": 0, "SELL": 1, "HOLD": 2}.get(item["action"], 3),
            -item["confidence"],
            item["symbol"],
        )
    )
    signal_dates = sorted({item["signal_asof"] for item in signals})
    all_confirmed = bool(signals) and all(item["bar_confirmed"] for item in signals)
    all_fresh = source_contract_enforced and bool(signals) and all(
        item["data_fresh"]
        and item["market_data"].get("source") == "FUTU_OPEND_LIVE"
        and item["market_data"].get("cache_used") is False
        and item["market_data"].get("fallback_used") is False
        for item in signals
    )
    data_ready = (
        bool(signals)
        and not errors
        and all_confirmed
        and all_fresh
        and len(signal_dates) == 1
    )
    # The model has contract/unit validation but no out-of-sample approval.
    validation_status = "NOT_EVALUATED"
    formal_publish_allowed = data_ready and validation_status == "VALIDATED"
    freshness_ready = all_fresh and not errors

    paper_intents = [
        {
            "symbol": item["symbol"],
            "name": item["name"],
            "market": market,
            "deployment_id": deployment["deployment_id"],
            "signal_asof": item["signal_asof"],
            "action": item["action"],
            "raw_signal_fraction": item["raw_signal_fraction"],
            "risk_capped_fraction": item["risk_capped_fraction"],
            "fraction_semantics": item["fraction_semantics"],
            "confidence": item["confidence"],
            "not_an_order": True,
        }
        for item in signals
        if item["action"] in ("BUY", "SELL")
        and source_contract_enforced
        and item["signal_eligible"]
        and item["data_fresh"]
    ]

    run_gate_checks = {
        "deployment_id_match": deployment["deployment_id"]
        == f"NORTHSTAR_D1_{market}",
        "market_match": market in {"HK", "US"},
        "single_market_only": True,
        "combined_run_disallowed": True,
        "data_ready": data_ready,
        "freshness_ready": freshness_ready,
        "summary_errors_zero": len(errors) == 0,
        "errors_empty": errors == [],
        "execution_disabled": True,
        "account_access_disabled": True,
        "order_api_not_called": True,
        "orders_empty": True,
        "fills_empty": True,
    }
    report_quality_checks = {
        "company_names_complete": bool(signals)
        and all(
            bool(str(item.get("name") or "").strip())
            and str(item.get("name") or "").strip() != item.get("symbol")
            for item in signals
        ),
        "decision_audit_complete": bool(signals)
        and all(_decision_audit_complete(item) for item in signals),
        "private_detail_policy_enforced": bool(signals)
        and all(
            item.get("decision_audit", {}).get("detail_policy")
            == "STATE_AND_EFFECTS_ONLY_NO_FORMULAS"
            for item in signals
        ),
    }
    report_quality_ready = all(report_quality_checks.values())
    run_verdict = (
        "RUN_PASS"
        if all(run_gate_checks.values()) and report_quality_ready
        else "RUN_FAILED_CLOSED"
    )

    return {
        "schema_version": "2.2",
        "model_id": "NORTHSTAR_D1",
        "model_name": "Northstar-D1",
        "model_version": "1.0.0",
        "deployment_id": deployment["deployment_id"],
        "deployment_name": deployment["deployment_name"],
        "mode": "RESEARCH_ONLY",
        "market": market,
        "market_timezone": deployment["timezone"],
        "generated_at": generated_at.isoformat(),
        "signal_asof": signal_dates[0] if len(signal_dates) == 1 else None,
        "data_ready": data_ready,
        "freshness_ready": freshness_ready,
        "run_verdict": run_verdict,
        "run_gate_checks": run_gate_checks,
        "report_quality_ready": report_quality_ready,
        "report_quality_checks": report_quality_checks,
        "validation_status": validation_status,
        "formal_publish_allowed": formal_publish_allowed,
        "universe": universe,
        "architecture": {
            "role": "INDEPENDENT_MARKET_DEPLOYMENT",
            "controller_dependencies": [],
            "data": "Futu OpenD live snapshot + calendar + QFQ completed daily bars",
        },
        "market_isolation": {
            "single_market_only": True,
            "combined_run_allowed": False,
            "universe_market": market,
            "output_partition": market.lower(),
        },
        "market_data_contract": {
            "required_source": "FUTU_OPEND_LIVE",
            "runtime_source_contract_enforced": source_contract_enforced,
            "new_query_per_symbol_per_scan": True,
            "cache_allowed": False,
            "fallback_allowed": False,
            "live_snapshot_required": True,
            "opend_server_clock_check_required": True,
            "completed_daily_session_match_required": True,
            "signal_price_semantics": "completed_qfq_daily_close",
            "snapshot_price_semantics": "live_or_latest_trade_audit_only",
            "failure_policy": "FAIL_CLOSED_NO_SIGNAL",
        },
        "signals": signals,
        "paper_intents": paper_intents,
        "orders": [],
        "fills": [],
        "execution": {
            "enabled": False,
            "account_access": False,
            "order_api_called": False,
        },
        "errors": errors,
        "summary": {
            "total": len(signals),
            "buy": sum(item["action"] == "BUY" for item in signals),
            "sell": sum(item["action"] == "SELL" for item in signals),
            "hold": sum(item["action"] == "HOLD" for item in signals),
            "paper_intents": len(paper_intents),
            "errors": len(errors),
        },
    }


def _fraction_semantics_label(value: str | None) -> str:
    return {
        "desired_long_exposure": "BUY目标研究暴露",
        "fraction_of_existing_position_to_reduce": "SELL减少既有暴露",
        "no_position_change": "无仓位变化",
    }.get(str(value or ""), "UNKNOWN")


def _schedule_lag_label(value: Any) -> str:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return "UNKNOWN"
    direction = "延迟" if seconds >= 0 else "提前"
    return f"{direction} {abs(seconds):.1f}s"


def render_markdown(payload: Dict) -> str:
    """Render a compact auditable Markdown report."""

    summary = payload["summary"]
    run = payload.get("run") or {}
    run_verdict = payload.get("run_verdict") or (
        "RUN_PASS"
        if payload.get("data_ready") and payload.get("freshness_ready")
        else "RUN_FAILED_CLOSED"
    )
    lines = [
        f"# Northstar-D1 {payload['market']} 运行报告",
        "",
        f"- 运行判定：`{run_verdict}`",
        f"- 研究状态：`{payload['mode']} / {payload['validation_status']}`",
        "- 判定边界：运行通过只代表数据与安全门禁通过，不代表模型已经验证",
        f"- 模型：`{payload['model_id']}` v{payload['model_version']}",
        f"- 独立部署：`{payload['deployment_id']}`",
        f"- 信号日：`{payload.get('signal_asof') or 'MIXED/UNKNOWN'}`",
        "- 日线完成性：`COMPLETED_SESSION_ONLY`",
        f"- 模型生成时间：`{payload.get('generated_at') or 'UNKNOWN'}`",
        f"- 报告生成时间：`{payload.get('report_generated_at_utc') or 'UNKNOWN'}`",
        f"- 数据就绪：`{str(payload['data_ready']).lower()}`",
        f"- 新鲜度门禁：`{str(payload['freshness_ready']).lower()}`",
        f"- 决策审计完整：`{str(payload.get('report_quality_ready', False)).lower()}`",
        f"- 正式发布：`{str(payload['formal_publish_allowed']).lower()}`",
        "- 数据：每标的每次调用均重新直连 Futu OpenD；实时 snapshot + 交易日历 + QFQ 已完成日线",
        "- 缓存/Fallback：禁止；任一新鲜度校验失败即关闭该标的信号",
        "- 价格口径：日线收盘价用于模型；实时价仅用于本次调用的新鲜度审计",
        "- 架构：独立模型，不依赖外部中控",
        "- 执行：关闭；paper_intents 不是订单",
    ]
    if run.get("run_id"):
        lines.extend(
            [
                f"- Run ID：`{run['run_id']}`",
                f"- 调度槽：`{run.get('slot_key', 'UNKNOWN')}`",
                f"- 计划时点：`{run.get('scheduled_at_local', 'UNKNOWN')}`",
                f"- 实际开始：`{run.get('started_at_local', 'UNKNOWN')}`",
                f"- 调度偏差：`{_schedule_lag_label(run.get('schedule_lag_seconds'))}`",
                f"- 尝试次数：`{run.get('attempt', 1)}`",
                f"- 调用来源：`{run.get('invocation_source', 'LOCAL_OR_UNATTRIBUTED')}`",
                f"- 运行类型：`{run.get('invocation_run_kind') or 'NOT_PROVIDED'}`",
                f"- Windows 任务：`{run.get('scheduler_task_name') or 'NOT_PROVIDED'}`",
                f"- 调度合同 SHA256：`{run.get('scheduler_contract_sha256') or 'NOT_PROVIDED'}`",
                f"- Automation ID：`{run.get('automation_id') or 'NOT_PROVIDED'}`",
                f"- Automation Run ID：`{run.get('automation_run_id') or 'NOT_PROVIDED'}`",
                f"- Automation Conversation ID：`{run.get('automation_conversation_id') or 'NOT_PROVIDED'}`",
                f"- Automation Run Kind：`{run.get('automation_run_kind') or 'NOT_PROVIDED'}`",
                f"- 调用策略：`{run.get('invocation_policy', 'UNKNOWN')}`",
                f"- 产物策略：`{run.get('artifact_policy', 'UNKNOWN')}`",
            ]
        )
    lines.extend(
        [
            "",
            "## 汇总",
            "",
            f"BUY {summary['buy']} / SELL {summary['sell']} / "
            f"HOLD {summary['hold']} / 错误 {summary['errors']}",
            "",
            "## 信号",
            "",
            "| 标的代码 | 公司名称 | 日线收盘 | Futu实时/最新价 | Snapshot更新 | 动作 | 比例语义 | 原始比例 | 风险约束后比例 | 置信度 |",
            "|---|---|---:|---:|---|---|---|---:|---:|---:|",
        ]
    )
    for item in payload["signals"]:
        snapshot = item["market_data"]["snapshot"]
        lines.append(
            "| {symbol} | {name} | {close:.2f} | {live:.2f} | {updated} | "
            "{action} | {semantics} | {raw:.0%} | {capped:.0%} | "
            "{confidence:.0%} |".format(
                symbol=item["symbol"],
                name=item["name"],
                close=item["close"],
                live=snapshot["last_price"],
                updated=snapshot["update_time"],
                action=item["action"],
                semantics=_fraction_semantics_label(
                    item.get("fraction_semantics")
                ),
                raw=item["raw_signal_fraction"],
                capped=item["risk_capped_fraction"],
                confidence=item["confidence"],
            )
        )

    if payload["signals"]:
        structure_stage_labels = {
            "NEW_CONFIRMED": "新结构",
            "CONFIRMED": "结构持续",
            "NEW_PLATEAU": "新钝化",
            "PLATEAU": "钝化持续",
            "PLATEAU_DISAPPEARED": "钝化消失",
            "NONE": "无",
        }
        structure_effect_labels = {
            "ALIGNED_ADD": "顺势增强",
            "OPPOSED_TRIM": "逆向修边",
            "PRIMARY_TRIGGER": "结构触发",
            "WATCH_ONLY": "仅观察",
            "OBSERVED_NO_ADJUSTMENT": "已观察未调整",
            "NONE": "无调整",
        }
        sequence_stage_labels = {
            "REACHED": "已完成",
            "NEAR": "临近完成",
            "BUILDING": "形成中",
            "NONE": "无",
        }
        sequence_alignment_labels = {
            "ALIGNED": "同向",
            "OPPOSED": "反向",
            "NOT_ACTIVE": "未激活",
            "NOT_APPLICABLE": "无动作可调整",
        }
        sequence_effect_labels = {
            "CONFIDENCE_UP": "置信度上调",
            "CONFIDENCE_DOWN": "置信度下调",
            "NO_ACTION_TO_MODIFY": "不产生动作",
            "NONE": "无调整",
        }
        lines.extend(
            [
                "",
                "## 三层决策审计（私有）",
                "",
                "仅展示模型当次使用的状态与影响，不披露公式、周期或阈值。",
                "",
                "### 结构信号",
                "",
                "| 标的代码 | 公司名称 | 候选→输出 | 趋势状态 | 趋势事件 | 底部结构 | 顶部结构 | 结构作用 | 比例变化 | 置信度变化 | 模型说明 |",
                "|---|---|---|---|---|---|---|---|---:|---:|---|",
            ]
        )
        for item in payload["signals"]:
            audit = item.get("decision_audit") or {}
            trend_audit = audit.get("trend") or {}
            structure_audit = audit.get("structure") or {}
            bottom = structure_audit.get("bottom") or {}
            top = structure_audit.get("top") or {}
            decision = audit.get("decision") or {}
            candidate = decision.get("candidate_action", "UNKNOWN")
            emitted = decision.get("emitted_action", item.get("action", "UNKNOWN"))
            action_path = f"{candidate}→{emitted}"
            reason = decision.get("reason") or item.get("reason") or "—"
            lines.append(
                "| {symbol} | {name} | {action_path} | {market_state} | {event} | "
                "{bottom_stage}({bottom_days}日) | {top_stage}({top_days}日) | "
                "{effect} | {fraction_delta:+.0%} | {confidence_delta:+.0f}pp | "
                "{reason} |".format(
                    symbol=item["symbol"],
                    name=item["name"],
                    action_path=action_path,
                    market_state=trend_audit.get("market_state", "UNKNOWN"),
                    event=trend_audit.get("event", "UNKNOWN"),
                    bottom_stage=structure_stage_labels.get(
                        bottom.get("stage"), bottom.get("stage", "UNKNOWN")
                    ),
                    bottom_days=int(bottom.get("persistence_days") or 0),
                    top_stage=structure_stage_labels.get(
                        top.get("stage"), top.get("stage", "UNKNOWN")
                    ),
                    top_days=int(top.get("persistence_days") or 0),
                    effect=structure_effect_labels.get(
                        structure_audit.get("effect"),
                        structure_audit.get("effect", "UNKNOWN"),
                    ),
                    fraction_delta=float(
                        structure_audit.get("fraction_delta") or 0.0
                    ),
                    confidence_delta=float(
                        structure_audit.get("confidence_delta") or 0.0
                    ) * 100,
                    reason=reason,
                )
            )

        lines.extend(
            [
                "",
                "### 序列信号",
                "",
                "| 标的代码 | 公司名称 | 计数 | 阶段 | 方向 | 与动作关系 | 序列作用 | 置信度变化 |",
                "|---|---|---:|---|---|---|---|---:|",
            ]
        )
        for item in payload["signals"]:
            sequence = (item.get("decision_audit") or {}).get("sequence") or {}
            lines.append(
                "| {symbol} | {name} | {count} | {stage} | {direction} | {alignment} | "
                "{effect} | {confidence_delta:+.0f}pp |".format(
                    symbol=item["symbol"],
                    name=item["name"],
                    count=int(sequence.get("count") or 0),
                    stage=sequence_stage_labels.get(
                        sequence.get("stage"), sequence.get("stage", "UNKNOWN")
                    ),
                    direction=sequence.get("direction", "UNKNOWN"),
                    alignment=sequence_alignment_labels.get(
                        sequence.get("alignment"),
                        sequence.get("alignment", "UNKNOWN"),
                    ),
                    effect=sequence_effect_labels.get(
                        sequence.get("effect"),
                        sequence.get("effect", "UNKNOWN"),
                    ),
                    confidence_delta=float(
                        sequence.get("confidence_delta") or 0.0
                    ) * 100,
                )
            )

        lines.extend(
            [
                "",
                "## 数据新鲜度证据",
                "",
                "查询 RT 是本次 OpenD 往返耗时；最近成交年龄、OpenD 时钟差和实时门禁分别展示。负年龄表示 Futu 时间略领先本机，不等于未来数据。",
                "",
                "| 标的代码 | 公司名称 | 来源 | 查询RT(s) | 最近成交年龄(s) | OpenD时钟差(s) | Market live | Age门禁 | 市场状态 | 新鲜度依据 | Cache | Fallback |",
                "|---|---|---|---:|---:|---:|---|---|---|---|---|---|",
            ]
        )
        for item in payload["signals"]:
            evidence = item["market_data"]
            snapshot = evidence["snapshot"]
            round_trip = evidence.get("round_trip_seconds")
            snapshot_age = snapshot.get("age_seconds")
            age_limit = snapshot.get("age_limit_seconds")
            market_live = bool(snapshot.get("market_live", False))
            age_text = (
                f"{float(snapshot_age):.3f}"
                if snapshot_age is not None else "—"
            )
            if snapshot_age is not None and float(snapshot_age) < 0:
                age_text += "（时钟差）"
            age_gate = (
                f"≤{float(age_limit):.0f}s"
                if age_limit is not None else "N/A（非交易时段）"
            )
            lines.append(
                "| {symbol} | {name} | {source} | {rt} | {age} | {clock_skew} | "
                "{market_live} | {age_gate} | {state} | {basis} | "
                "{cache} | {fallback} |".format(
                    symbol=item["symbol"],
                    name=item["name"],
                    source=evidence.get("source", "UNKNOWN"),
                    rt=(f"{float(round_trip):.3f}" if round_trip is not None else "—"),
                    age=age_text,
                    clock_skew=(
                        f"{float(evidence['opend_clock_skew_seconds']):.3f}"
                        if evidence.get("opend_clock_skew_seconds") is not None
                        else "—"
                    ),
                    market_live=str(market_live).lower(),
                    age_gate=age_gate,
                    state=snapshot.get("market_state", "UNKNOWN"),
                    basis=snapshot.get("freshness_basis", "UNKNOWN"),
                    cache=str(evidence.get("cache_used")).lower(),
                    fallback=str(evidence.get("fallback_used")).lower(),
                )
            )

    lines.extend(["", "## Paper intents（非订单）", ""])
    if payload["paper_intents"]:
        name_by_symbol = {
            item["symbol"]: item.get("name", "") for item in payload["signals"]
        }
        for intent in payload["paper_intents"]:
            company_name = intent.get("name") or name_by_symbol.get(
                intent["symbol"], ""
            )
            lines.append(
                f"- {intent['symbol']} {company_name} · {intent['action']} "
                f"{intent['risk_capped_fraction']:.0%} · "
                f"{_fraction_semantics_label(intent.get('fraction_semantics'))} · "
                f"confidence {intent['confidence']:.0%}"
            )
    else:
        lines.append("- 无（本次没有 paper intents）。")

    if payload["errors"]:
        lines.extend(["", "## 数据错误", ""])
        for error in payload["errors"]:
            lines.append(
                f"- {error['symbol']}: {error['error']} · fail_closed=true · no_signal"
            )

    lines.extend(
        [
            "",
            "## 综述结论",
            "",
            f"本次运行判定为 `{run_verdict}`。该判定仅说明运行与数据门禁状态；"
            f"模型验证仍为 `{payload['validation_status']}`，正式发布为 "
            f"`{str(payload['formal_publish_allowed']).lower()}`。",
            "Northstar-D1 不连接账户、不生成订单，也不构成投资建议。",
            "",
        ]
    )
    return "\n".join(lines)


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest().upper()


def write_report(
    payload: Dict,
    output_root: Path = OUTPUT_ROOT,
    *,
    run_id: str | None = None,
) -> Dict[str, Any]:
    """Write an immutable run archive, then atomically refresh canonical files."""

    output_dir = Path(output_root) / payload["market"].lower()
    output_dir.mkdir(parents=True, exist_ok=True)
    asof = payload.get("signal_asof") or datetime.now().strftime("%Y-%m-%d")
    stem = f"{asof}_northstar_d1_{payload['market'].lower()}"

    resolved_run_id = safe_run_id(
        run_id
        or (payload.get("run") or {}).get("run_id")
        or f"ADHOC-{uuid.uuid4().hex.upper()}"
    )
    archive_dir = output_dir / "runs" / resolved_run_id
    archive_dir.mkdir(parents=True, exist_ok=False)

    payload_to_write = copy.deepcopy(payload)
    payload_to_write.setdefault("run", {})
    payload_to_write["run"].update(
        {
            "run_id": resolved_run_id,
            "artifact_policy": "IMMUTABLE_RUN_ARCHIVE_PLUS_ATOMIC_CANONICAL",
        }
    )
    payload_to_write.setdefault(
        "run_verdict",
        "RUN_PASS"
        if payload_to_write.get("data_ready")
        and payload_to_write.get("freshness_ready")
        else "RUN_FAILED_CLOSED",
    )

    json_content = (
        json.dumps(payload_to_write, ensure_ascii=False, indent=2) + "\n"
    )
    markdown_content = render_markdown(payload_to_write)
    json_hash = _content_hash(json_content)
    markdown_hash = _content_hash(markdown_content)

    archive_json = archive_dir / f"{stem}.json"
    archive_markdown = archive_dir / f"{stem}.md"
    archive_manifest = archive_dir / f"{stem}.manifest.json"
    archive_json.write_text(json_content, encoding="utf-8", newline="\n")
    archive_markdown.write_text(markdown_content, encoding="utf-8", newline="\n")

    canonical_json = output_dir / f"{stem}.json"
    canonical_markdown = output_dir / f"{stem}.md"
    canonical_manifest = output_dir / f"{stem}.manifest.json"
    manifest = {
        "schema_version": "1.0",
        "run_id": resolved_run_id,
        "deployment_id": payload_to_write.get("deployment_id"),
        "market": payload_to_write.get("market"),
        "signal_asof": payload_to_write.get("signal_asof"),
        "run_verdict": payload_to_write.get("run_verdict"),
        "report_quality_ready": payload_to_write.get("report_quality_ready"),
        "validation_status": payload_to_write.get("validation_status"),
        "formal_publish_allowed": payload_to_write.get("formal_publish_allowed"),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "canonical": {
            "json": str(canonical_json),
            "markdown": str(canonical_markdown),
            "manifest": str(canonical_manifest),
        },
        "archive": {
            "json": str(archive_json),
            "markdown": str(archive_markdown),
            "manifest": str(archive_manifest),
        },
        "sha256": {
            "json": json_hash,
            "markdown": markdown_hash,
        },
    }
    manifest_content = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    archive_manifest.write_text(
        manifest_content,
        encoding="utf-8",
        newline="\n",
    )

    _atomic_write_text(canonical_json, json_content)
    _atomic_write_text(canonical_markdown, markdown_content)
    _atomic_write_text(canonical_manifest, manifest_content)
    return {
        "json": str(canonical_json),
        "markdown": str(canonical_markdown),
        "manifest": str(canonical_manifest),
        "archive_json": str(archive_json),
        "archive_markdown": str(archive_markdown),
        "archive_manifest": str(archive_manifest),
        "hashes": {
            "archive_json": json_hash,
            "archive_markdown": markdown_hash,
        },
    }


def build_market_parser(market: str) -> argparse.ArgumentParser:
    deployment = deployment_for(market)
    parser = argparse.ArgumentParser(
        description=f"{deployment['deployment_name']} independent scanner"
    )
    parser.add_argument("--symbol", action="append", help="Repeat to scan a subset")
    parser.add_argument("--no-write", action="store_true", help="Do not write JSON/Markdown")
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    return parser


def main_for_market(market: str, argv: List[str] | None = None) -> int:
    market = market.upper()
    args = build_market_parser(market).parse_args(argv)
    deployment = deployment_for(market)
    coordinator: RunCoordinator | None = None
    paths: Dict[str, Any] | None = None

    if not args.no_write:
        coordinator = RunCoordinator(
            market=market,
            deployment=deployment,
            output_root=args.output_root,
        )
        decision = coordinator.acquire()
        if decision.action == "SKIPPED_EARLY":
            print(
                f"[{deployment['deployment_id']}] SKIPPED_EARLY "
                f"slot={decision.slot_key} reason={decision.reason}"
            )
            return 0
        if decision.action == "DUPLICATE_SUPPRESSED":
            print(
                f"[{deployment['deployment_id']}] DUPLICATE_SUPPRESSED "
                f"slot={decision.slot_key} run_id={decision.run_id}"
            )
            for label, path in (decision.prior_state or {}).get(
                "artifacts", {}
            ).items():
                print(f"  {label}: {path}")
            return 0
        if not decision.should_run:
            print(
                f"[{deployment['deployment_id']}] {decision.action} "
                f"slot={decision.slot_key} reason={decision.reason}",
                file=sys.stderr,
            )
            return 3

    try:
        payload = run_market(market, symbols=args.symbol)
        if coordinator is not None:
            payload["run"] = coordinator.metadata()
        payload["report_generated_at_utc"] = datetime.now(timezone.utc).isoformat()
        summary = payload["summary"]
        print(
            f"[{payload['deployment_id']}] verdict={payload['run_verdict']} "
            f"asof={payload.get('signal_asof')} BUY={summary['buy']} "
            f"SELL={summary['sell']} HOLD={summary['hold']} "
            f"errors={summary['errors']} data_ready={payload['data_ready']} "
            f"validation={payload['validation_status']}"
        )
        if not args.no_write:
            paths = write_report(
                payload,
                args.output_root,
                run_id=(payload.get("run") or {}).get("run_id"),
            )
            print(f"  JSON:     {paths['json']}")
            print(f"  MD:       {paths['markdown']}")
            print(f"  Manifest: {paths['manifest']}")
            print(f"  Archive:  {paths['archive_json']}")

        if payload["run_verdict"] == "RUN_PASS":
            if coordinator is not None and paths is not None:
                coordinator.mark_success(paths)
            return 0

        if coordinator is not None:
            coordinator.mark_failed(
                "run gate checks did not all pass",
                exit_code=1,
                paths=paths,
            )
        return 1
    except Exception as exc:
        if coordinator is not None:
            coordinator.mark_failed(str(exc), exit_code=1, paths=paths)
        print(
            f"[{deployment['deployment_id']}] FAILED_CLOSED: {exc}",
            file=sys.stderr,
        )
        return 1


def main(argv: List[str] | None = None) -> int:
    print(
        "Northstar-D1 markets are isolated. Use either "
        "`python -m research.northstar_d1.hk` or "
        "`python -m research.northstar_d1.us`.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
