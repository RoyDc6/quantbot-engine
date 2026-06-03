# QuantBot Optimization Roadmap

This roadmap focuses on engineering reliability before strategy changes. The goal is to make the repo easier to run, verify, and recover without changing trading behavior.

## Phase 1: Fast Safety Checks

- Keep `pytest` fast by default with smoke tests only.
- Add tests for pure helpers first: symbol conversion, JSON serialization, risk math, position sizing.
- Keep integration tests for Futu, OKX, TickFlow, and LLM behind explicit markers.

## Phase 2: Repository Hygiene

- Stop tracking regenerated runtime files such as signal JSON, scanner caches, event caches, logs, state files, and local screenshots.
- Keep generated artifacts outside source control unless they are intentionally curated research reports.
- Use `git rm --cached` for files already tracked but covered by `.gitignore`.

## Phase 3: Path And Environment Portability

- Replace hard-coded `E:/quant` and `D:/new_quant/...` paths with one project-root helper.
- Read external repo locations from environment variables, with local defaults documented in `.env.example`.
- Keep Windows scheduled tasks calling stable entry points instead of module-internal scripts.

## Phase 4: Production Guardrails

- Make dry-run the default everywhere.
- Add a pre-trade summary showing market, account mode, symbol count, exposure, and order count.
- Require an explicit live flag for any order path.

## Phase 5: CI And Release Discipline

- Add GitHub Actions for smoke tests and syntax checks.
- Tag known-good paper-trading versions.
- Keep release notes focused on behavior changes, data source changes, and risk-rule changes.
