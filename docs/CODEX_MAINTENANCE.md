# Codex-Assisted Maintenance

QuantBot Engine includes an optional, maintainer-controlled pull-request review
workflow based on the official `openai/codex-action`. It turns the project's
safety boundaries into a repeatable review prompt and a strict JSON output
contract. Codex output is advisory; maintainers remain responsible for every
decision and change.

The workflow is intentionally separate from the research and paper-trading
runtime. It cannot place orders, connect to Futu OpenD, or change repository
files.

## What is included

| Path | Purpose |
| --- | --- |
| `.github/workflows/codex-review.yml` | Trusted-event preflight, read-only review, and comment publishing |
| `.github/codex/prompts/review.md` | Project-specific review priorities and prompt-injection boundary |
| `.github/codex/schemas/review.schema.json` | Bounded structured output contract |
| `scripts/validate_codex_review.py` | Dependency-free local artifact validation |
| `tests/smoke/test_codex_maintenance.py` | Offline regression tests for permissions, prompt, and schema |
| `AGENTS.md` | Repository-wide instructions for coding agents and reviewers |

## One-time setup

1. Create a project-scoped OpenAI API key with the narrowest practical access.
2. In the GitHub repository, open **Settings -> Secrets and variables ->
   Actions**.
3. Add the key as a repository secret named `OPENAI_API_KEY`.
4. Keep account-level spending and rate limits appropriate for the maintenance
   workload.

Never place the key in a workflow file, issue, pull request, artifact, or log.
Until the secret is configured, the workflow completes its preflight and safely
skips the model call instead of failing unrelated pull requests.

## Running a review

Reviews run for open, non-draft pull requests when the repository secret is
available. A maintainer can also request a review explicitly:

```bash
gh workflow run codex-review.yml -f pr_number=123
```

The action checks out GitHub's pull-request merge ref with persisted Git
credentials disabled. Codex runs with a read-only sandbox and `drop-sudo`. A
separate job, which has no OpenAI secret and no checked-out code, formats the
structured result and creates or updates one PR comment.

The workflow deliberately uses `pull_request`, not `pull_request_target`.
Secrets are unavailable to forked pull requests, and a missing secret produces
a safe skip.

## Output and local validation

The model must return JSON containing an overall risk level, an evidence-based
summary, line-anchored findings, confidence values, safety-boundary categories,
and offline validation recommendations.

Validate a saved result without installing another package:

```bash
python scripts/validate_codex_review.py path/to/codex-review.json
```

A known-good fixture is included:

```bash
python scripts/validate_codex_review.py tests/fixtures/codex_review/valid_review.json
```

The validator fails closed on unknown keys, missing fields, invalid enums,
unbounded strings, non-finite confidence values, or more findings than the
contract allows. Error output reports field paths but never echoes model text.

## Security model

- Pull-request content is treated as untrusted data, not instructions.
- The model cannot write to the checkout or use repository credentials.
- The API key is provided only to the Codex action through GitHub Secrets.
- The comment-publishing job receives model output but never receives the API
  key or source checkout.
- Model output is HTML-escaped, length-limited, and labeled as advisory.
- The normal smoke suite remains completely offline and requires no OpenAI key.

No automated review can prove correctness or safety. A maintainer must reproduce
findings, run the required tests, and reject speculative recommendations.

## Evaluation plan

Maintenance quality should be tracked with reviewable metrics rather than model
enthusiasm:

- percentage of reviews that produce a reproducible finding;
- false-positive and duplicate-finding rates;
- findings caught before human review versus after CI;
- regression tests added for accepted findings;
- API cost and latency per reviewed pull request;
- zero secret exposure and zero execution-boundary violations.

Future API-credit work will expand deterministic fixtures for provenance,
fallback status, stale-data handling, and research-output quality. Those evals
must remain separate from broker execution and must run without private market
or account data.

## Reference

See the official [Codex GitHub Action documentation](https://learn.chatgpt.com/docs/github-action)
for action inputs, permission guidance, and the security checklist.
