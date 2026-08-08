# Contributing to QuantBot Engine

Thank you for helping improve QuantBot Engine. The project welcomes focused contributions to reliability, reproducibility, documentation, data adapters, factor evaluation, and paper-trading safety.

## Before you start

- Search existing issues and pull requests before opening a duplicate.
- Open an issue before a large architectural change so the scope and safety impact can be discussed.
- Keep each pull request narrowly focused and explain how it was validated.
- Do not include proprietary datasets, account information, credentials, or generated portfolio/runtime state.
- Review the repository-wide coding-agent boundaries in [`AGENTS.md`](AGENTS.md).

## Project boundaries

Contributions must preserve these invariants:

1. The public repository remains research and paper-trading software.
2. Futu order submission remains pinned to `TrdEnv.SIMULATE`.
3. LLM-assisted code may produce research evidence but must not place orders or bypass hard gates.
4. Dry-run remains the default behavior.
5. Execution claims require adapter results and audit records; a signal or order intent alone is insufficient.

Changes that weaken these boundaries will not be accepted.

## Development setup

```bash
git clone https://github.com/RoyDc6/quantbot-engine.git
cd quantbot-engine
python -m venv .venv
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Activate the virtual environment using the command appropriate for your shell.

## Safe validation

Run the same offline checks used by CI:

```bash
python -m compileall -q unified_runner.py reports/fusion_report_v3.py core
python -m pytest tests/smoke/
python scripts/check_secrets.py
python scripts/validate_codex_review.py tests/fixtures/codex_review/valid_review.json
```

These commands must not connect to a broker or submit orders. Tests that need external services should use mocks or be clearly separated from the default suite.

## Making a change

- Follow PEP 8 and use UTF-8 source files.
- Add type hints to new public interfaces where practical.
- Keep external API calls behind adapters with explicit timeouts and failure handling.
- Add a regression test for bug fixes and safety-sensitive behavior.
- Update user-facing documentation when behavior or configuration changes.
- Avoid broad refactors in the same pull request as a behavior change.

Recommended commit prefixes are `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `perf:`, and `chore:`.

## Credentials and data

Use environment variables or a local secret manager. Never commit real values for `NVIDIA_API_KEY`, `TICKFLOW_API_KEY`, broker credentials, tokens, private keys, or account identifiers.

Before opening a pull request:

```bash
python scripts/check_secrets.py
git diff --check
```

If a secret is committed, revoke or rotate it immediately. Deleting it in a later commit is not sufficient because the value remains in Git history.

## Pull request checklist

In the pull request description, include:

- the problem and the proposed change;
- affected modules and safety boundaries;
- commands run and their results;
- any external service required for manual validation;
- screenshots or sample output only when they contain no private data.

Maintainers may ask for a smaller scope or additional tests before review.

Optional Codex review comments are advisory. Reproduce every accepted finding
locally and add a regression test; do not merge solely because an automated
review reports no findings.
