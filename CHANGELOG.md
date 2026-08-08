# Changelog

All notable changes to QuantBot Engine are documented in this file. The project
uses semantic versioning for public community releases.

## [Unreleased]

### Planned

- additional deterministic research fixtures and provider-adapter contracts;
- broader cross-platform onboarding and evaluation coverage.

## [0.1.0] - 2026-08-08

### Added

- the first public, Apache-2.0 licensed community release;
- English onboarding, architecture, contribution, security, conduct, and
  maintainer documentation;
- offline smoke tests for execution safety, provenance, order consistency, and
  repository hygiene;
- a repository secret scanner and CI enforcement;
- optional, maintainer-controlled Codex PR review with strict structured output;
- repository-wide coding-agent instructions and code ownership;
- Dependabot configuration for Python and GitHub Actions dependencies.

### Security

- documented and tested simulation-only execution boundaries;
- environment-variable credential loading and ignored local runtime state;
- private vulnerability reporting guidance;
- read-only Codex sandboxing, trusted triggers, and secret-safe review posting.

### Known limitations

- the project is experimental research and paper-trading software;
- optional market workflows require contributor-provided data access and Futu
  OpenD, while the default test suite remains offline;
- Codex review requires a maintainer-provided `OPENAI_API_KEY` GitHub Secret and
  is advisory rather than an approval gate.

[Unreleased]: https://github.com/RoyDc6/quantbot-engine/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/RoyDc6/quantbot-engine/releases/tag/v0.1.0
