# Project Roadmap

QuantBot Engine's roadmap focuses on making safety-first quantitative infrastructure easier to inspect, reproduce, and extend. It does not promise a delivery date or investment performance.

## Current foundation

- HK/US orchestration with a Futu simulation adapter
- technical, ML, XMM, market-state, and LLM-assisted research components
- deterministic fusion and hard-risk gates
- structured signals, reports, journals, and reconciliation utilities
- offline smoke and execution-safety tests
- maintainer-controlled Codex review with strict, locally validated output
- versioned public release notes and repository code ownership

## Near-term priorities

1. **Reproducible onboarding** — smaller example configurations, synthetic/sample datasets, and clearer platform-neutral setup paths.
2. **Adapter contracts** — documented interfaces for market data, research factors, and simulated brokers so contributors can add providers without changing the decision core.
3. **Evaluation quality** — stronger walk-forward, leakage, freshness, and provenance checks for factor research.
4. **Maintenance automation** — expand the current safe Codex review, dependency updates, documentation checks, and regression-test generation.
5. **Release discipline** — continue post-v0.1.0 community releases with changelogs, migration notes, and known limitations.
6. **Contributor growth** — issue labels, scoped starter tasks, and ownership of well-defined modules.

## Good contribution areas

- deterministic fixtures that replace private or provider-specific data;
- cross-platform setup and test improvements;
- data-quality, calendar, and completed-candle validation;
- additional tests for safety invariants and audit provenance;
- provider adapters with mock implementations;
- documentation, examples, and architecture diagrams.

## Explicit non-goals

- real-money order execution from the public project;
- autonomous LLM control of accounts or broker APIs;
- personalized investment advice or return guarantees;
- publishing credentials, private account state, or licensed datasets.

Roadmap proposals should be opened as GitHub issues and should describe their validation and safety implications.
