# Milestone C + Runtime Recovery (Framework-Agnostic)

## Primary Goal

Deliver **Milestone C (Persistent Repository Memory)** and resolve the newly observed runtime/debugging failure class as one combined objective.

This document is intentionally **not Angular-specific**. Angular is only failure evidence. The fixes must generalize across frameworks, languages, OSes, and repository states.

## Hard Constraints (Must-Pass)

- Framework, language, and OS agnostic behavior is mandatory.
- No stack-specific patching (for example, no Angular-only targeting logic in core resolution).
- No solution may only fix the current app; it must fix the underlying orchestrator behavior.
- Must work for new repos, existing repos, legacy repos, and sparse repos.
- Evidence-first + confidence-gated resolution only; no hardcoded entrypoint/file assumptions.
- Preserve architecture constraints in `ARCHITECTURE_AND_CODING_GUIDELINES.md` (contract boundaries, deterministic safety, explicit taxonomy).

## Evidence Artifacts

### Current Angular attempt (available in workspace)

- Run ID: `8034dc38-2fa8-4b35-b300-89d9b74d7dad`
- Artifacts:
  - `.orchestrator/runs/8034dc38-2fa8-4b35-b300-89d9b74d7dad/events.jsonl`
  - `.orchestrator/runs/8034dc38-2fa8-4b35-b300-89d9b74d7dad/task_outcomes.json`
  - `.orchestrator/runs/8034dc38-2fa8-4b35-b300-89d9b74d7dad/summary.json`
  - `.orchestrator/runs/8034dc38-2fa8-4b35-b300-89d9b74d7dad/metrics.json`

### Prior runtime class (referenced evidence)

- Run IDs:
  - `b2c80fc9-9bc2-4849-a525-09a55cb2fd25`
  - `c17ff8cf-e8c4-40d8-915b-6636dd41bd98`
- If not under workspace `.orchestrator/runs/`, use relocated copies mentioned in user notes and include:
  - `events.jsonl`
  - implementation/post-handoff/post-mutation cognition snapshots

## What Broke (Root-Cause Chain)

1. **Target-intent drift during implementation**
   - Planned targets: `app.component.ts`, `app.component.html`, `app.module.ts`.
   - Actual mutation evidence in `events.jsonl` shows repeated writes to `src/main.ts`.
   - Result: template/NgModule code ended up in the wrong file, producing parser and module-resolution cascades.

2. **Nested-root drift increased targeting ambiguity**
   - Active root became `projects/phoneword-converter/projects/phoneword-converter` after bootstrap.
   - This inflated path ambiguity and made wrong candidate selection more likely.

3. **Validation executability gap**
   - Preflight inferred no executable validation commands from natural-language requirements.
   - Validation phase was skipped (`validation_skipped_non_executable`) instead of translating requirements into runnable checks.

4. **Compile gate quality gap**
   - Final state failed with `[FINAL_COMPILE] no terminating compile/build command inferred.`
   - Outcome: run ended `implementation_failed` without effective runtime/compile debugging loop.

5. **Memory gap across passes**
   - Second implementation pass repeated wrong-target behavior instead of using prior failure signals to re-rank/reject candidates.

## Why It Stopped Without Debugging

- Runtime/build checks were not reliably executable from inferred validation contract.
- No persistent per-target memory prevented the system from learning from failed candidate choices in-pass.
- Failure classification and targeted recovery routing were insufficient for automatic second-pass correction.

## Milestone C Scope (From Roadmap)

Use `documentation/plan-next-steps.md` Milestone C deliverables and implement as concrete runtime behavior.

### 1) Persistent memory model

Extend `services/dev/types/dev_graph_state.py` with structured memory:

- files inspected
- symbols discovered
- assumptions and confidence
- prior candidate attempts and rejection reasons
- prior correction attempts
- command/validation failures and extracted file refs

### 2) Memory lifecycle wiring

Integrate read/write memory in:

- `services/dev/dev_master_graph.py`
- `services/dev/phases/execute_implementation_target.py`
- `services/dev/phases/execute_validation_phase.py`
- `services/dev/phases/execute_final_compile_gate.py`

### 3) Behavioral guarantees

- Do not repeat low-confidence rejected candidates without new evidence.
- Penalize previously failed candidate paths; boost diagnostics-linked alternatives.
- Preserve framework neutrality and confidence gating for all stacks.

## Additional Remediation Required (Same Workstream)

### Validation command inference hardening

- Convert safe natural-language validation requirements into executable commands when possible (stack-neutral policy).
- Keep non-executable/manual checks tracked separately but never confuse them with executed validations.

### Target-intent guardrails

- Enforce intent-to-target checks before apply (file role, extension/type expectation, path-family consistency).
- Block apply when intended target class does not match selected file candidate.

### Bootstrap/root normalization

- Normalize nested project roots during scaffold/bootstrap to prevent duplicated segment roots.

## Files To Touch

- `services/dev/types/dev_graph_state.py`
- `services/dev/dev_master_graph.py`
- `services/dev/phases/execute_implementation_target.py`
- `services/dev/phases/execute_validation_phase.py`
- `services/dev/phases/execute_final_compile_gate.py`
- `services/dev/dev_executor.py` (if failure taxonomy/diagnostics payload needs expansion for memory routing)
- tests under `tests/` for regression + parity

## Test Plan (Must Include)

1. Target intent alignment regression:
   - ensure `app.component.*` intent cannot be satisfied by writing `main.ts`.
2. Nested-root normalization regression:
   - prevent `.../project/project` active-root drift after scaffold.
3. Validation inference regression:
   - when PM gives NL validation for a known stack, at least one executable command is inferred when safe.
4. Memory behavior regression:
   - second pass must avoid previously failed candidate path unless confidence increases due to new evidence.
5. Cross-OS parity:
   - Ubuntu + Windows path and command behavior consistency.

## Acceptance Criteria

- Underlying failure class is fixed across stacks (not only Angular).
- Runtime/compile checks are executed or explicitly and correctly classified as non-executable with fallback policy.
- Wrong-target mutation drift is prevented by guardrails and memory-based re-ranking.
- Milestone C memory is persisted and reused within run and across consecutive correction passes.
- Safety and architecture constraints remain intact.
