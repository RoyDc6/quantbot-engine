# Security Policy

QuantBot Engine handles market data, optional API credentials, simulated account state, and order-routing code. Security reports that could expose credentials, corrupt research provenance, or weaken the simulation-only boundary are taken seriously.

## Supported version

Security fixes are applied to the latest revision of the `main` branch. Older commits, local forks, archived research artifacts, and third-party services are not independently supported.

## Reporting a vulnerability

Do not open a public issue containing a credential, exploit, account identifier, private dataset, or reproducible method for bypassing a safety guard.

Use GitHub's private vulnerability reporting flow from the repository **Security** tab when it is available. If that option is unavailable, contact the primary maintainer through the contact method on the [`RoyDc6` GitHub profile](https://github.com/RoyDc6) and share only enough information to establish a private channel.

Please include:

- affected commit and file paths;
- impact and realistic attack conditions;
- minimal reproduction steps;
- whether a credential or external account may already be exposed;
- a suggested remediation, if known.

The maintainer will acknowledge and assess reports on a best-effort basis, coordinate a fix, and credit reporters who want attribution when disclosure is safe.

## Credential incidents

If a real credential is committed or printed to a public log:

1. revoke or rotate it at the provider immediately;
2. remove it from the current tree and add a regression check;
3. assess whether Git history, forks, caches, or build logs must be remediated;
4. do not treat deletion in a follow-up commit as revocation.

## Safety scope

Reports are in scope when they show that repository code can:

- use a real-money broker environment contrary to the documented boundary;
- bypass dry-run, confirmation, or deterministic hard gates;
- expose secrets or private account/runtime data;
- let untrusted LLM output invoke execution paths;
- falsify or silently break signal, order, or reconciliation provenance.

Trading performance, investment losses, strategy disagreement, market-data outages, and behavior caused solely by an unsupported local modification are not security vulnerabilities.

## Operational disclaimer

This project is research and paper-trading software. Never test a report against a real-money account or with credentials you cannot revoke.
