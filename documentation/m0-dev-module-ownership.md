# M0 Dev Module Ownership (Retired)

## Decision

The internal `services/dev` module was fully removed in favor of external implementation through Claude Code CLI.

## Ownership Shift

- PM ownership remains with `services/pm`.
- Execution ownership now belongs to `services/execution`.
- Orchestration ownership remains in `orchestrator.py`.

## Operational Implication

Any future implementation runtime logic should be added to `services/execution` adapters, not by reintroducing `services/dev` internals.

