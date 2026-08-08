"""Offline checks for the optional Codex maintenance workflow."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "codex-review.yml"
PROMPT = ROOT / ".github" / "codex" / "prompts" / "review.md"
SCHEMA = ROOT / ".github" / "codex" / "schemas" / "review.schema.json"
FIXTURE = ROOT / "tests" / "fixtures" / "codex_review" / "valid_review.json"


def test_codex_workflow_uses_trusted_read_only_boundaries():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "pull_request_target:" not in workflow
    assert "pull_request:" in workflow
    assert "workflow_dispatch:" in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "persist-credentials: false" in workflow
    assert "uses: openai/codex-action@v1" in workflow
    assert "openai-api-key: ${{ secrets.OPENAI_API_KEY }}" in workflow
    assert "sandbox: read-only" in workflow
    assert "safety-strategy: drop-sudo" in workflow
    assert workflow.index("uses: actions/checkout@v7") < workflow.index(
        "uses: openai/codex-action@v1"
    )


def test_codex_prompt_rejects_untrusted_instructions_and_execution():
    prompt = PROMPT.read_text(encoding="utf-8").lower()

    assert "untrusted data" in prompt
    assert "ignore any instructions" in prompt
    assert "do not modify files" in prompt
    assert "connect to futu opend" in prompt
    assert "real-money execution" in prompt


def test_codex_schema_is_strict_and_bounded():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "summary",
        "risk_level",
        "findings",
        "validation_recommended",
    }
    finding = schema["properties"]["findings"]["items"]
    assert finding["additionalProperties"] is False
    assert finding["properties"]["confidence"]["minimum"] == 0
    assert finding["properties"]["confidence"]["maximum"] == 1


def test_valid_codex_review_fixture_passes_offline_validator():
    proc = subprocess.run(
        [sys.executable, "scripts/validate_codex_review.py", str(FIXTURE)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert proc.returncode == 0, proc.stderr
    assert "validation passed" in proc.stdout


def test_invalid_codex_review_fails_closed(tmp_path):
    invalid = json.loads(FIXTURE.read_text(encoding="utf-8"))
    invalid["findings"] = [
        {
            "severity": "high",
            "title": "Synthetic invalid finding",
            "path": "example.py",
            "line": 1,
            "body": "Confidence is intentionally outside the allowed range.",
            "confidence": 2,
            "safety_boundary": "correctness",
        }
    ]
    artifact = tmp_path / "invalid-review.json"
    artifact.write_text(json.dumps(invalid), encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, "scripts/validate_codex_review.py", str(artifact)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert proc.returncode == 1
    assert "confidence" in proc.stderr
