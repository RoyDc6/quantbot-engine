from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from research.northstar_d1.delivery_readiness import (
    DeliveryNotReady,
    REQUIRED_MARKDOWN_SECTIONS,
    inspect_delivery,
)
from research.northstar_d1.runner import render_markdown
from research.northstar_d1.tools.configure_workbuddy_delivery_only import (
    CONTRACTS,
    _next_occurrence,
    _prompt,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _build_ready(tmp_path: Path) -> tuple[Path, Path]:
    market_root = tmp_path / "hk"
    run_id = "NORTHSTAR_D1_HK-20260819T1620-A01"
    archive = market_root / "runs" / run_id
    report = archive / "2026-08-19_northstar_d1_hk.md"
    payload_path = archive / "2026-08-19_northstar_d1_hk.json"
    manifest_path = archive / "2026-08-19_northstar_d1_hk.manifest.json"
    archive.mkdir(parents=True)
    report.write_text(
        "# Northstar-D1 HK 运行报告\n\n"
        + "\n\n".join(REQUIRED_MARKDOWN_SECTIONS)
        + "\n\n| 标的代码 | 公司名称 | 日线收盘 |\n"
        + "| 标的代码 | 公司名称 | 候选→输出 |\n"
        + "| 标的代码 | 公司名称 | 计数 |\n"
        + "| 标的代码 | 公司名称 | 来源 |\n"
        + ("| 09988.HK | 阿里巴巴 | row |\n" * 4)
        + "\n",
        encoding="utf-8",
    )
    payload = {
        "deployment_id": "NORTHSTAR_D1_HK",
        "market": "HK",
        "run_verdict": "RUN_PASS",
        "mode": "RESEARCH_ONLY",
        "validation_status": "NOT_EVALUATED",
        "formal_publish_allowed": False,
        "data_ready": True,
        "freshness_ready": True,
        "report_quality_ready": True,
        "execution": {
            "enabled": False,
            "account_access": False,
            "order_api_called": False,
        },
        "orders": [],
        "fills": [],
        "errors": [],
        "signals": [{"symbol": "09988.HK", "name": "阿里巴巴"}],
        "paper_intents": [],
        "signal_asof": "2026-08-19",
        "summary": {"buy": 1, "sell": 0, "hold": 6, "errors": 0},
    }
    _write_json(payload_path, payload)
    manifest = {
        "run_id": run_id,
        "deployment_id": "NORTHSTAR_D1_HK",
        "market": "HK",
        "run_verdict": "RUN_PASS",
        "archive": {
            "json": str(payload_path),
            "markdown": str(report),
            "manifest": str(manifest_path),
        },
        "sha256": {"json": _sha(payload_path), "markdown": _sha(report)},
    }
    _write_json(manifest_path, manifest)
    state = {
        "status": "SUCCESS",
        "run_id": run_id,
        "slot_key": "HK:2026-08-19:16:20:Asia/Hong_Kong",
        "invocation_source": "WINDOWS_TASK_SCHEDULER",
        "invocation_run_kind": "SCHEDULED",
        "scheduler_task_name": r"\NorthstarD1-HK",
        "artifacts": {
            "archive_json": str(payload_path),
            "archive_markdown": str(report),
            "archive_manifest": str(manifest_path),
        },
        "artifact_hashes": {
            "archive_json": _sha(payload_path),
            "archive_markdown": _sha(report),
        },
    }
    _write_json(market_root / ".runtime" / "latest.json", state)
    return report, market_root / ".runtime" / "latest.json"


def test_ready_report_returns_one_immutable_markdown(tmp_path: Path):
    report, _ = _build_ready(tmp_path)

    result = inspect_delivery(
        "HK",
        output_root=tmp_path,
        observed_at=datetime(2026, 8, 19, 8, 40, tzinfo=timezone.utc),
    )

    assert result["status"] == "DELIVERY_READY"
    assert result["run_id"] == "NORTHSTAR_D1_HK-20260819T1620-A01"
    assert result["report_path"] == str(report)
    assert result["report_sha256"] == _sha(report)
    assert result["present_files_count"] == 1
    assert result["present_only_this_markdown"] is True


def test_rejects_old_workbuddy_source(tmp_path: Path):
    _, state_path = _build_ready(tmp_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["invocation_source"] = "WORKBUDDY_AUTOMATION"
    _write_json(state_path, state)

    with pytest.raises(DeliveryNotReady, match="SOURCE_NOT_WINDOWS_TASK_SCHEDULER"):
        inspect_delivery(
            "HK",
            output_root=tmp_path,
            observed_at=datetime(2026, 8, 19, 8, 40, tzinfo=timezone.utc),
        )


def test_rejects_markdown_hash_drift(tmp_path: Path):
    report, _ = _build_ready(tmp_path)
    report.write_text("tampered", encoding="utf-8")

    with pytest.raises(DeliveryNotReady, match="MARKDOWN_STATE_HASH_MISMATCH"):
        inspect_delivery(
            "HK",
            output_root=tmp_path,
            observed_at=datetime(2026, 8, 19, 8, 40, tzinfo=timezone.utc),
        )


def test_rejects_missing_presentation_section(tmp_path: Path):
    report, state_path = _build_ready(tmp_path)
    report.write_text("# Northstar-D1 HK 运行报告\n", encoding="utf-8")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["artifact_hashes"]["archive_markdown"] = _sha(report)
    _write_json(state_path, state)
    manifest_path = Path(state["artifacts"]["archive_manifest"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sha256"]["markdown"] = _sha(report)
    _write_json(manifest_path, manifest)

    with pytest.raises(DeliveryNotReady, match="MARKDOWN_SECTION_MISSING"):
        inspect_delivery(
            "HK",
            output_root=tmp_path,
            observed_at=datetime(2026, 8, 19, 8, 40, tzinfo=timezone.utc),
        )


def test_rejects_company_name_missing_from_detail_tables(tmp_path: Path):
    report, state_path = _build_ready(tmp_path)
    report.write_text(
        report.read_text(encoding="utf-8").replace(
            "| 09988.HK | 阿里巴巴 | row |", "| 09988.HK | row |", 1
        ),
        encoding="utf-8",
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["artifact_hashes"]["archive_markdown"] = _sha(report)
    _write_json(state_path, state)
    manifest_path = Path(state["artifacts"]["archive_manifest"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sha256"]["markdown"] = _sha(report)
    _write_json(manifest_path, manifest)

    with pytest.raises(
        DeliveryNotReady, match="COMPANY_NAME_TABLE_COVERAGE_INCOMPLETE"
    ):
        inspect_delivery(
            "HK",
            output_root=tmp_path,
            observed_at=datetime(2026, 8, 19, 8, 40, tzinfo=timezone.utc),
        )


def test_accepts_hash_bound_non_overwriting_company_name_correction(tmp_path: Path):
    report, state_path = _build_ready(tmp_path)
    corrected_markdown = report.read_text(encoding="utf-8")
    report.write_text(
        corrected_markdown.replace(
            "| 09988.HK | 阿里巴巴 | row |", "| 09988.HK | row |", 1
        ),
        encoding="utf-8",
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["artifact_hashes"]["archive_markdown"] = _sha(report)
    _write_json(state_path, state)
    source_manifest_path = Path(state["artifacts"]["archive_manifest"])
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    source_manifest["sha256"]["markdown"] = _sha(report)
    _write_json(source_manifest_path, source_manifest)

    run_id = state["run_id"]
    correction_root = tmp_path / "hk" / "delivery_corrections" / run_id
    corrected_path = correction_root / f"{report.stem}.corrected.md"
    corrected_manifest_path = correction_root / f"{report.stem}.corrected.manifest.json"
    corrected_path.parent.mkdir(parents=True)
    corrected_path.write_text(corrected_markdown, encoding="utf-8")
    _write_json(
        corrected_manifest_path,
        {
            "source_run_id": run_id,
            "source_json_sha256": _sha(Path(state["artifacts"]["archive_json"])),
            "source_markdown_sha256": _sha(report),
            "corrected_markdown_sha256": _sha(corrected_path),
        },
    )

    result = inspect_delivery(
        "HK",
        output_root=tmp_path,
        observed_at=datetime(2026, 8, 19, 8, 40, tzinfo=timezone.utc),
    )

    assert result["report_path"] == str(corrected_path)
    assert result["report_sha256"] == _sha(corrected_path)


def test_delivery_prompts_cannot_invoke_model_or_workbuddy_runner():
    for contract in CONTRACTS.values():
        prompt = _prompt(contract)
        assert f"-m {contract['module']} --wait-seconds 600" in prompt
        assert "C:/Users/RoyGoode/" in prompt
        assert r"C:\Users\RoyGoode" not in prompt
        assert "research.northstar_d1.workbuddy_hk" not in prompt
        assert "research.northstar_d1.workbuddy_us" not in prompt
        assert "research.northstar_d1.windows_scheduler_hk" not in prompt
        assert "research.northstar_d1.windows_scheduler_us" not in prompt
        assert prompt.count("present_files") >= 3


def test_delivery_schedule_uses_next_valid_day_after_cutoff():
    contract = CONTRACTS["automation-1785736457372"]
    next_run = _next_occurrence(
        contract,
        datetime(2026, 8, 19, 9, 0, tzinfo=timezone.utc),
    )

    assert next_run.isoformat() == "2026-08-20T16:40:00+08:00"


def test_empty_paper_intents_section_is_rendered_explicitly():
    payload = {
        "market": "HK",
        "model_id": "NORTHSTAR_D1",
        "model_version": "1.0.0",
        "deployment_id": "NORTHSTAR_D1_HK",
        "run_verdict": "RUN_PASS",
        "mode": "RESEARCH_ONLY",
        "validation_status": "NOT_EVALUATED",
        "formal_publish_allowed": False,
        "signal_asof": "2026-08-19",
        "generated_at": "2026-08-19T08:20:00+00:00",
        "report_generated_at_utc": "2026-08-19T08:20:01+00:00",
        "data_ready": True,
        "freshness_ready": True,
        "decision_audit_ready": True,
        "report_quality_ready": True,
        "summary": {"buy": 0, "sell": 0, "hold": 0, "errors": 0},
        "signals": [],
        "paper_intents": [],
        "errors": [],
        "run": {},
    }

    markdown = render_markdown(payload)

    assert "## Paper intents（非订单）" in markdown
    assert "- 无（本次没有 paper intents）。" in markdown
