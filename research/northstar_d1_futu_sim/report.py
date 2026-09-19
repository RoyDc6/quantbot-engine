"""Build a combined Northstar signal and Futu SIMULATE forward report."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from .runner import RECEIPTS, _sha256


ROOT = Path(__file__).resolve().parent
REPORTS = ROOT / "reports"
FORWARD_RECEIPTS = ROOT / "forward_receipts"
NORTHSTAR_OUTPUT = ROOT.parent / "northstar_d1" / "output"
ACTIONABLE_FORWARD_STATUSES = {
    "PASS",
    "ATTENTION_REQUIRED",
    "FAILED_CLOSED",
    "DUPLICATE_SUPPRESSED",
}


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _receipt_local_date(path: Path):
    try:
        return datetime.strptime(path.name[:15], "%Y%m%dT%H%M%S").date()
    except ValueError:
        return None


def _market_session_date(forward: dict[str, Any], fallback):
    observed = str((forward.get("window") or {}).get("observed_at_local") or "")
    if observed:
        try:
            return datetime.fromisoformat(observed).date()
        except ValueError:
            pass
    return fallback


def _latest_forward_receipt(
    market: str,
    forward_receipts_dir: Path = FORWARD_RECEIPTS,
    *,
    expected_local_date,
) -> tuple[Path, dict[str, Any]]:
    candidates: list[tuple[str, Path, dict[str, Any]]] = []
    for path in forward_receipts_dir.glob(f"*_{market.lower()}_forward.json"):
        if _receipt_local_date(path) != expected_local_date:
            continue
        try:
            data = _read_json(path)
        except (OSError, ValueError):
            continue
        if data.get("mode") != "NORTHSTAR_D1_INTEGRATED_FORWARD":
            continue
        if str(data.get("market") or "").upper() != market:
            continue
        if data.get("status") not in ACTIONABLE_FORWARD_STATUSES:
            continue
        candidates.append((path.name, path, data))
    if not candidates:
        raise RuntimeError(f"{market} has no current integrated Forward receipt")
    _, path, data = max(candidates, key=lambda item: item[0])
    return path, data


def _resolve_research_artifacts(
    market: str,
    forward: dict[str, Any],
    *,
    expected_local_date,
    northstar_output: Path,
) -> tuple[str, dict[str, Path], dict[str, Any]]:
    raw_paths = forward.get("research_artifacts") or {}
    run_id = str(forward.get("run_id") or "")

    # The first integrated failure receipt lacked artifact paths. Accept its
    # runtime state only when market and scheduler date exactly match.
    if not raw_paths:
        state_path = northstar_output / market.lower() / ".runtime" / "latest.json"
        state = _read_json(state_path)
        scheduled = datetime.fromisoformat(str(state.get("scheduled_at_local") or ""))
        if (
            str(state.get("market") or "").upper() != market
            or scheduled.date() != expected_local_date
        ):
            raise RuntimeError(f"{market} runtime state does not match report date")
        raw_paths = state.get("artifacts") or {}
        run_id = run_id or str(state.get("run_id") or "")

    paths = {
        key: Path(str(raw_paths.get(key) or ""))
        for key in ("json", "markdown", "manifest")
    }
    if not all(path.is_file() for path in paths.values()):
        raise RuntimeError(f"{market} research artifacts are incomplete")

    manifest = _read_json(paths["manifest"])
    if (
        manifest.get("market") != market
        or not run_id
        or manifest.get("run_id") != run_id
    ):
        raise RuntimeError(f"{market} research run identity mismatch")
    hashes = manifest.get("sha256") or {}
    if _sha256(paths["json"]) != hashes.get("json"):
        raise RuntimeError(f"{market} research JSON hash mismatch")
    if _sha256(paths["markdown"]) != hashes.get("markdown"):
        raise RuntimeError(f"{market} research Markdown hash mismatch")
    return run_id, paths, manifest


def _execution_receipt(
    market: str,
    forward: dict[str, Any],
    *,
    run_id: str,
    receipts_dir: Path,
) -> tuple[Path, dict[str, Any]] | None:
    raw = str(forward.get("execution_receipt") or "")
    if not raw:
        return None
    path = Path(raw)
    try:
        path.relative_to(receipts_dir)
    except ValueError as exc:
        raise RuntimeError(f"{market} execution receipt is outside receipt root") from exc
    if not path.is_file():
        raise RuntimeError(f"{market} execution receipt is missing")
    receipt = _read_json(path)
    source = receipt.get("source") or {}
    if (
        receipt.get("mode") != "FUTU_SIM_FORWARD"
        or receipt.get("market") != market
        or receipt.get("real_trading_allowed") is not False
        or source.get("manifest_run_id") != run_id
    ):
        raise RuntimeError(f"{market} execution receipt identity mismatch")
    source_path = Path(str(source.get("path") or ""))
    if not source_path.is_file() or _sha256(source_path) != source.get("sha256"):
        raise RuntimeError(f"{market} execution source hash mismatch")
    return path, receipt


def _money(value: Any) -> str:
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "UNKNOWN"


def _demote_markdown(markdown: str) -> list[str]:
    lines: list[str] = []
    for line in markdown.splitlines():
        if line.startswith("## "):
            lines.append("#### " + line[3:])
        elif line.startswith("# "):
            lines.append("### " + line[2:])
        else:
            lines.append(line)
    return lines


def _execution_section(path: Path, receipt: dict[str, Any]) -> list[str]:
    account = receipt.get("account_before") or {}
    lines = [
        "## Futu 模拟执行与对账",
        "",
        f"- 执行回执：`{path}`",
        f"- 对账状态：`{receipt.get('reconciliation') or 'UNKNOWN'}`",
        f"- 模拟账户资产：{_money(account.get('total_assets'))}",
        f"- 模拟账户现金：{_money(account.get('cash'))}",
        "",
        "### 本轮计划与跳过项",
        "",
    ]
    planned = receipt.get("planned_orders") or []
    if planned:
        lines.extend(["| 标的 | 动作 | 数量 | 参考限价 |", "|---|---:|---:|---:|"])
        for item in planned:
            lines.append(
                f"| {item.get('symbol')} | {item.get('action')} | {item.get('qty')} | {_money(item.get('price'))} |"
            )
    else:
        lines.append("- 本轮无下单差额。")
    for item in receipt.get("skipped_intents") or []:
        lines.append(
            f"- 跳过 `{item.get('symbol')}` `{item.get('action')}`：`{item.get('reason')}`"
        )

    lines.extend(["", "### 本轮订单结果", ""])
    results = receipt.get("results") or []
    if results:
        lines.extend([
            "| 订单号 | 标的 | 动作 | 数量 | 限价 | 状态 | 已成交 | 成交均价 |",
            "|---|---|---:|---:|---:|---|---:|---:|",
        ])
        for item in results:
            lines.append(
                f"| {item.get('order_id') or '-'} | {item.get('symbol')} | {item.get('action')} | "
                f"{item.get('qty')} | {_money(item.get('price'))} | {item.get('status')} | "
                f"{item.get('dealt_qty', 0)} | {_money(item.get('dealt_avg_price'))} |"
            )
    else:
        lines.append("- 本轮没有向富途提交订单。")

    lines.extend(["", "### 持仓回读", ""])
    positions = receipt.get("positions_after") or []
    if positions:
        lines.extend(["| 标的 | 数量 | 成本 | 市值 |", "|---|---:|---:|---:|"])
        for item in positions:
            lines.append(
                f"| {item.get('symbol')} | {item.get('qty')} | {_money(item.get('cost_price'))} | {_money(item.get('market_val'))} |"
            )
    else:
        lines.append("- 本轮回执生成时无持仓。")
    lines.append("")
    return lines


def build_report(
    *,
    market: str | None = None,
    forward_receipts_dir: Path = FORWARD_RECEIPTS,
    receipts_dir: Path = RECEIPTS,
    reports_dir: Path = REPORTS,
    northstar_output: Path = NORTHSTAR_OUTPUT,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now().astimezone()
    if market is not None:
        market = market.upper()
        if market not in {"HK", "US"}:
            raise ValueError("market must be HK or US")
    selected_markets = (market,) if market else ("HK", "US")
    evidence: dict[str, dict[str, Any]] = {}
    for selected in selected_markets:
        forward_path, forward = _latest_forward_receipt(
            selected,
            forward_receipts_dir,
            expected_local_date=now.date(),
        )
        run_id, research_paths, manifest = _resolve_research_artifacts(
            selected,
            forward,
            expected_local_date=now.date(),
            northstar_output=northstar_output,
        )
        execution = _execution_receipt(
            selected,
            forward,
            run_id=run_id,
            receipts_dir=receipts_dir,
        )
        evidence[selected] = {
            "forward_path": forward_path,
            "forward": forward,
            "run_id": run_id,
            "research_paths": research_paths,
            "manifest": manifest,
            "execution": execution,
        }

    report_date = now.date()
    if market:
        report_date = _market_session_date(evidence[market]["forward"], report_date)
    scope_label = market or "HK + US"
    lines = [
        f"# Northstar-D1 信号与 Futu 模拟前向日报（{scope_label}）· {report_date.isoformat()}",
        "",
        "> NORTHSTAR_D1_INTEGRATED_FORWARD · FUTU SIMULATE · NOT_EVALUATED · real_trading_allowed=false",
        "",
        "## 今日结论",
        "",
    ]
    for selected, item in evidence.items():
        forward = item["forward"]
        execution = item["execution"]
        execution_state = execution[1].get("reconciliation") if execution else "NOT_EXECUTED"
        lines.append(
            f"- {selected}：Forward `{forward.get('status')}`；信号运行 `{item['manifest'].get('run_verdict')}`；"
            f"执行对账 `{execution_state}`。"
        )
    lines.extend([
        "- 报告先展示同一 `run_id` 的完整 Northstar 信号报告，再展示该运行对应的模拟执行结果。",
        "- 若信号运行失败关闭，则明确标记未执行，不读取其他时点的旧交易回执补位。",
        "",
    ])

    for selected, item in evidence.items():
        forward = item["forward"]
        research_paths = item["research_paths"]
        lines.extend([
            f"## {selected} 运行证据",
            "",
            f"- Forward 状态：`{forward.get('status')}`",
            f"- Run ID：`{item['run_id']}`",
            f"- Forward 回执：`{item['forward_path']}`",
            f"- 信号报告：`{research_paths['markdown']}`",
            f"- 信号报告 SHA256：`{_sha256(research_paths['markdown'])}`",
        ])
        if forward.get("error"):
            lines.append(f"- 失败原因：`{forward.get('error')}`")
        lines.extend(["", "## Northstar-D1 信号报告", ""])
        lines.extend(_demote_markdown(research_paths["markdown"].read_text(encoding="utf-8")))
        lines.append("")
        if item["execution"]:
            lines.extend(_execution_section(*item["execution"]))
        else:
            lines.extend([
                "## Futu 模拟执行与对账",
                "",
                "- `NOT_EXECUTED`：本轮信号未通过全部运行门禁，因此没有进入账户、报价或下单阶段。",
                "- 本节不展示其他运行或历史订单，避免把旧交易回执误认为本轮结果。",
                "",
            ])

    lines.extend([
        "## 综述结论",
        "",
        "本报告把信号判定与模拟执行按同一 `run_id` 闭环。`RUN_PASS` 仅代表本轮数据和运行门禁通过；",
        "模型仍为 `NOT_EVALUATED`。模拟订单与成交只用于工程前向验证，真实资金交易保持关闭。",
        "",
    ])

    reports_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"_{market.lower()}" if market else ""
    report_path = reports_dir / f"{report_date.isoformat()}_northstar_d1_futu_sim{suffix}_daily.md"
    content = "\n".join(lines)
    tmp = report_path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, report_path)
    digest = hashlib.sha256(report_path.read_bytes()).hexdigest().upper()
    return {
        "status": "REPORT_READY",
        "report_path": str(report_path),
        "sha256": digest,
        "present_files_count": 1,
        "present_only_this_markdown": True,
        "markets": list(selected_markets),
        "forward_statuses": {
            selected: evidence[selected]["forward"].get("status")
            for selected in selected_markets
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=("HK", "US", "hk", "us"))
    args = parser.parse_args(argv)
    try:
        print(json.dumps(build_report(market=args.market), ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAILED_CLOSED", "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
