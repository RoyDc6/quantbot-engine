## Summary

Describe the problem and the smallest change that solves it.

## Scope

- Affected modules:
- Behavior intentionally unchanged:

## Safety and provenance

- [ ] The change preserves dry-run defaults and Futu `TrdEnv.SIMULATE`.
- [ ] LLM-assisted code cannot invoke execution or bypass deterministic gates.
- [ ] No credentials, account data, private datasets, or generated runtime state are included.
- [ ] Output/provenance behavior is documented when it changes.

## Validation

- [ ] `python -m compileall -q unified_runner.py reports/fusion_report_v3.py core`
- [ ] `python -m pytest tests/smoke/`
- [ ] `python scripts/check_secrets.py`
- [ ] `git diff --check`

Commands run and results:

```text
Paste sanitized output here.
```

## Notes for reviewers

Call out risks, external-service requirements, follow-up work, or areas needing special attention.
