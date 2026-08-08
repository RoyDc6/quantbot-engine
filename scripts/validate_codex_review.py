#!/usr/bin/env python3
"""Validate a Codex review artifact without external dependencies.

This validator mirrors the repository's JSON output contract closely enough to
fail closed in CI or local evaluation. It reports field paths, never full model
output, so a malformed artifact cannot accidentally print sensitive content.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any


TOP_LEVEL_KEYS = {
    "summary",
    "risk_level",
    "findings",
    "validation_recommended",
}
RISK_LEVELS = {"critical", "high", "medium", "low", "none"}
FINDING_KEYS = {
    "severity",
    "title",
    "path",
    "line",
    "body",
    "confidence",
    "safety_boundary",
}
SEVERITIES = {"critical", "high", "medium", "low"}
SAFETY_BOUNDARIES = {
    "execution",
    "credentials",
    "provenance",
    "market_separation",
    "reproducibility",
    "correctness",
    "none",
}


def _bounded_string(value: Any, minimum: int, maximum: int) -> bool:
    return isinstance(value, str) and minimum <= len(value) <= maximum


def validate_review(review: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(review, dict):
        return ["$: expected an object"]

    keys = set(review)
    missing = TOP_LEVEL_KEYS - keys
    extra = keys - TOP_LEVEL_KEYS
    if missing:
        errors.append(f"$: missing keys {sorted(missing)}")
    if extra:
        errors.append(f"$: unexpected keys {sorted(extra)}")

    if not _bounded_string(review.get("summary"), 1, 1200):
        errors.append("$.summary: expected 1..1200 characters")
    if review.get("risk_level") not in RISK_LEVELS:
        errors.append("$.risk_level: invalid value")

    findings = review.get("findings")
    if not isinstance(findings, list):
        errors.append("$.findings: expected an array")
        findings = []
    elif len(findings) > 20:
        errors.append("$.findings: maximum 20 items")

    for index, finding in enumerate(findings):
        prefix = f"$.findings[{index}]"
        if not isinstance(finding, dict):
            errors.append(f"{prefix}: expected an object")
            continue
        finding_keys = set(finding)
        missing_finding = FINDING_KEYS - finding_keys
        extra_finding = finding_keys - FINDING_KEYS
        if missing_finding:
            errors.append(f"{prefix}: missing keys {sorted(missing_finding)}")
        if extra_finding:
            errors.append(f"{prefix}: unexpected keys {sorted(extra_finding)}")
        if finding.get("severity") not in SEVERITIES:
            errors.append(f"{prefix}.severity: invalid value")
        if not _bounded_string(finding.get("title"), 1, 160):
            errors.append(f"{prefix}.title: expected 1..160 characters")
        if not _bounded_string(finding.get("path"), 1, 500):
            errors.append(f"{prefix}.path: expected 1..500 characters")
        line = finding.get("line")
        if isinstance(line, bool) or not isinstance(line, int) or line < 1:
            errors.append(f"{prefix}.line: expected a positive integer")
        if not _bounded_string(finding.get("body"), 1, 2000):
            errors.append(f"{prefix}.body: expected 1..2000 characters")
        confidence = finding.get("confidence")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence)
            or not 0 <= confidence <= 1
        ):
            errors.append(f"{prefix}.confidence: expected a finite value from 0 to 1")
        if finding.get("safety_boundary") not in SAFETY_BOUNDARIES:
            errors.append(f"{prefix}.safety_boundary: invalid value")

    validation = review.get("validation_recommended")
    if not isinstance(validation, list):
        errors.append("$.validation_recommended: expected an array")
    else:
        if len(validation) > 12:
            errors.append("$.validation_recommended: maximum 12 items")
        for index, command in enumerate(validation):
            if not _bounded_string(command, 1, 500):
                errors.append(
                    f"$.validation_recommended[{index}]: expected 1..500 characters"
                )

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path, help="Path to the JSON review artifact")
    args = parser.parse_args(argv)

    try:
        review = json.loads(args.artifact.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"Invalid review artifact: {type(exc).__name__}", file=sys.stderr)
        return 1

    errors = validate_review(review)
    if errors:
        print("Codex review validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    findings = len(review["findings"])
    print(f"Codex review validation passed: {findings} finding(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
