# Migration Status

## Phases

| Phase | Scope | Status | Exit Gate |
| --- | --- | --- | --- |
| 0 | Inventory and mapping freeze | done | inventory approved |
| 1 | Canonical docs + OpenHands runtime foundation | in_progress | docs complete + smoke pass |
| 2 | Full minion loop hardening | pending | integration + replay pass |
| 3 | Legacy cleanup and enforcement | pending | CI guardrails green |

## Decision Gates

- Gate A: docs IA and ownership approved.
- Gate B: OpenHands dispatcher contract tests green.
- Gate C: full minion loop smoke run passes with deterministic audit trail.
- Gate D: legacy cleanup dry-run passes.
- Gate E: go-live checklist signed off.

## Current Notes

- Legacy docs are replaced by canonical files in this folder.
- OpenHands runtime is the target primary execution path.
- Deprecated artifacts will be removed after validation phase completion.
