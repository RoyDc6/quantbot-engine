# Maintainers and Governance

## Current maintainer

- [RoyDc6](https://github.com/RoyDc6) — founder and primary maintainer

QuantBot Engine currently uses a maintainer-led governance model. The primary maintainer is responsible for scope, releases, security coordination, repository administration, and final merge decisions.

## How decisions are made

Routine fixes and documentation changes are decided through pull-request review. Larger changes should begin with an issue that records the problem, alternatives, safety impact, and validation plan.

Decisions prioritize, in order:

1. simulation-only execution safety and protection of contributor data;
2. reproducibility and provenance;
3. correctness and test coverage;
4. maintainability and contributor experience;
5. new research capability.

The maintainer may reject changes that increase strategy capability while weakening auditability, deterministic risk controls, or the separation between LLM research and execution.

## Becoming a maintainer

As the community grows, regular contributors may be invited to maintain a defined area after demonstrating sustained, constructive participation; careful review judgment; and respect for security and paper-trading boundaries. Expanded responsibilities and ownership areas will be recorded in this file.

## Releases

Community releases should be tagged from a passing `main` commit and include a concise changelog, known limitations, and any configuration or compatibility changes. Research results and performance claims are not release guarantees.
