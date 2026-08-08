#!/usr/bin/env python3
"""Fail when high-confidence credential patterns appear in repository files.

The scanner reports only the rule name and location. It intentionally never
prints the matching value. It is a lightweight CI guard, not a replacement for
provider-side credential rotation or a dedicated history scanner.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[bytes]


RULES = (
    Rule(
        "GitHub access token",
        re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    ),
    Rule(
        "OpenAI API key",
        re.compile(rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    ),
    Rule(
        "NVIDIA API key",
        re.compile(rb"\bnvapi-[A-Za-z0-9_-]{20,}\b"),
    ),
    Rule(
        "AWS access key",
        re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    ),
    Rule(
        "Private key material",
        re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    ),
    Rule(
        "Hard-coded named credential",
        re.compile(
            rb"(?i)(?:TICKFLOW_API_KEY|NVIDIA_API_KEY|OPENAI_API_KEY|GITHUB_TOKEN)"
            rb"\s*=\s*['\"][^'\"\r\n]{12,}['\"]"
        ),
    ),
)


def repository_files() -> list[Path]:
    """Return tracked and non-ignored untracked files from the worktree."""

    proc = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / raw.decode("utf-8", errors="surrogateescape") for raw in proc.stdout.split(b"\0") if raw]


def scan_file(path: Path) -> list[tuple[str, int]]:
    try:
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            return []
        data = path.read_bytes()
    except OSError:
        return []

    if b"\0" in data:
        return []

    findings: list[tuple[str, int]] = []
    for rule in RULES:
        for match in rule.pattern.finditer(data):
            line = data.count(b"\n", 0, match.start()) + 1
            findings.append((rule.name, line))
    return findings


def is_sensitive_path(relative: str) -> bool:
    path = Path(relative)
    name = path.name.lower()
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return True
    if path.suffix.lower() in {".pem", ".key", ".p12"}:
        return True
    return relative == "config/tickflow_key.txt"


def main() -> int:
    findings: list[tuple[str, int, str]] = []
    files = repository_files()
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        if is_sensitive_path(relative):
            findings.append((relative, 1, "Sensitive file path"))
        for rule_name, line in scan_file(path):
            findings.append((relative, line, rule_name))

    if findings:
        print("Potential credentials detected (values suppressed):", file=sys.stderr)
        for relative, line, rule_name in findings:
            print(f"- {relative}:{line}: {rule_name}", file=sys.stderr)
        print("Revoke real credentials before removing them from the repository.", file=sys.stderr)
        return 1

    print(f"Secret check passed: {len(files)} repository files scanned.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
