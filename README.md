# QuantBot Engine

[![Smoke & Safety](https://github.com/RoyDc6/quantbot-engine/actions/workflows/smoke.yml/badge.svg)](https://github.com/RoyDc6/quantbot-engine/actions/workflows/smoke.yml)
[![Codex Maintainer Review](https://github.com/RoyDc6/quantbot-engine/actions/workflows/codex-review.yml/badge.svg)](https://github.com/RoyDc6/quantbot-engine/actions/workflows/codex-review.yml)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

QuantBot Engine is a safety-first quantitative research and paper-trading engine for Hong Kong and U.S. equities. It combines market data adapters, technical and machine-learning factors, market-regime classification, LLM-assisted research signals, deterministic fusion rules, and structured audit outputs in one inspectable Python codebase.

The project is built for transparent experimentation: contributors can study how heterogeneous signals move through research, decision, risk-control, and simulated execution layers without giving an LLM authority to place orders.

> [!IMPORTANT]
> QuantBot Engine is research software, not investment advice. The public project does not support real-money trading. Its Futu order adapter is pinned to `TrdEnv.SIMULATE`, and CI tests enforce that boundary.

## Why this project exists

Many quantitative examples stop at a notebook or a single backtest. QuantBot Engine explores the less visible engineering work required to turn research into an auditable, continuously tested paper-trading pipeline:

- multi-source factor normalization and deterministic signal fusion;
- explicit market-regime and hard-risk gates;
- separation of LLM-assisted research from rule-based execution;
- structured signals, reports, reconciliation data, and order journals;
- regression tests for execution safety and order consistency;
- HK/US market adapters with completed-market-data provenance.

This makes the repository useful as a reference implementation for researchers and maintainers working on reliable AI-assisted financial software, even when they use different strategies or brokers.

## Architecture

```text
Market data adapters
        |
        v
Research factors (TA / ML / XMM / LLM-assisted context)
        |
        v
FusionEngine + MarketState + deterministic HardGate
        |
        v
Order directives + risk controls + audit journal
        |
        +--> structured signals and Markdown reports
        |
        +--> optional Futu SIMULATE adapter
```

The core design rule is **research -> decision -> rule execution**. LLM output is evidence for the research and fusion layers; it cannot call a broker or bypass deterministic risk controls.

| Area | Key paths | Purpose |
| --- | --- | --- |
| Orchestration | `unified_runner.py`, `core/` | HK/US pipeline, safeguards, reconciliation, and audit state |
| Signal fusion | `fusion_framework/` | Typed signals, fusion matrix, and hard gates |
| Market context | `market_state/` | Regime classification and event-quality controls |
| Research | `research/`, `factor_research/`, `ml_alpha/`, `xmm-strategy/` | Factor experiments and validation utilities |
| Paper trading | `paper_trading/`, `futu_trader/`, `us_trader/` | Simulation workflows and report generation |
| Verification | `tests/smoke/`, `.github/workflows/smoke.yml` | Offline safety and regression checks |

For a more detailed map, see [`ARCHITECTURE_MAP.md`](ARCHITECTURE_MAP.md).

## Safety guarantees

The repository treats execution safety as a testable interface rather than a convention:

- the default runner mode is dry-run;
- the Futu adapter uses `TrdEnv.SIMULATE`, never `TrdEnv.REAL`;
- any simulated submission request requires both `--live` and explicit confirmation;
- scheduled HK/US jobs are covered by invariant tests;
- CI does not connect to Futu OpenD or any trading account;
- credentials and local portfolio/runtime state are excluded from version control;
- LLM-assisted components have no execution authority.

The `--live` flag is retained for compatibility with the internal runner terminology. In this public repository it only enables submission to the broker's **simulation environment** after the confirmation guard passes; it does not enable real-money trading.

## Quick start

### Requirements

- Python 3.12+
- Windows, Linux, or macOS for offline tests
- Windows and Futu OpenD only for optional Futu simulation workflows

### Install

```bash
git clone https://github.com/RoyDc6/quantbot-engine.git
cd quantbot-engine
python -m venv .venv
```

Activate the environment, then install the dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### Verify the safe, offline path

```bash
python -m compileall -q unified_runner.py reports/fusion_report_v3.py core
python -m pytest tests/smoke/
python scripts/check_secrets.py
```

These checks do not require market credentials or a broker connection.

### Validate the Codex review contract

The optional maintainer workflow has a dependency-free offline validator and a
known-good fixture:

```bash
python scripts/validate_codex_review.py tests/fixtures/codex_review/valid_review.json
```

This command validates the structured output contract without making an API
request.

### Inspect the runner

```bash
python unified_runner.py --help
```

Running a market pipeline requires a local Futu OpenD connection and suitable market access. Start without `--live`; the default path is dry-run.

```bash
python unified_runner.py --market HK
python unified_runner.py --market US
```

## Configuration and secrets

Optional integrations use environment variables:

| Variable | Used for | Required for offline tests |
| --- | --- | --- |
| `NVIDIA_API_KEY` | Optional LLM-assisted research calls | No |
| `TICKFLOW_API_KEY` | Optional historical-data research utilities | No |

Never commit credentials. Set them in your shell, OS credential store, or another local secret manager. A legacy local-file example is provided at `config/tickflow_key.example.txt`; the corresponding real file is ignored by Git.

Futu workflows expect OpenD at `127.0.0.1:11111` by default. Account and market access remain local to the contributor's environment.

## Outputs and provenance

Runtime outputs are intentionally separated from source code and ignored by Git. Depending on the workflow, the engine records structured signals, Markdown reports, reconciliation results, and order-journal entries. These artifacts are designed to preserve the path from source data and factor evidence to final simulated directives.

Generated signals or order intents are not evidence of broker execution. Any execution claim must be verified against the corresponding adapter result and audit record.

## Contributing

Contributions that improve testability, documentation, reproducibility, data-source adapters, factor evaluation, or safety controls are welcome. Start with [`CONTRIBUTING.md`](CONTRIBUTING.md), review the project boundaries in [`SECURITY.md`](SECURITY.md), and open an issue before proposing a large architectural change.

Every pull request should:

1. avoid committing credentials, account data, or generated runtime state;
2. preserve the simulation-only execution boundary;
3. include tests or a reproducible validation note;
4. pass the smoke, safety, and repository-secret checks.

## Codex-assisted maintenance

The repository includes an optional pull-request review workflow built on the
official `openai/codex-action`. It runs only when a maintainer-configured
`OPENAI_API_KEY` GitHub Secret is available; otherwise it safely skips the model
call. Reviews use trusted events, a read-only sandbox, a strict JSON schema, and
a separate secret-free comment job.

See [`AGENTS.md`](AGENTS.md) for repository-wide coding-agent boundaries and
[`docs/CODEX_MAINTENANCE.md`](docs/CODEX_MAINTENANCE.md) for setup, security,
validation, and evaluation details. Codex output is advisory and cannot approve
changes or access any execution path.

## Project status

QuantBot Engine is actively maintained as an experimental research and paper-trading project. Interfaces may evolve while the project improves packaging, sample datasets, contributor documentation, and cross-platform reproducibility. No performance, availability, or return guarantees are made.

Public changes are tracked in [`CHANGELOG.md`](CHANGELOG.md), starting with the
`v0.1.0` community release.

## Security

Please do not report credentials or exploitable trading-safety issues in a public issue. Follow the private reporting process in [`SECURITY.md`](SECURITY.md).

## License

Copyright 2026 RoyDc6. Licensed under the [Apache License 2.0](LICENSE).
