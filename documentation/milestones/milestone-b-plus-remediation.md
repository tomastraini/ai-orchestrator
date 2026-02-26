# Milestone B+ Remediation

## Purpose
Deliver Milestone B (structured editing engine) and fix the newly observed runtime-reliability failures in recent orchestrator runs, without introducing framework-specific behavior.

This document uses run evidence from:

- `.orchestrator/runs/b2c80fc9-9bc2-4849-a525-09a55cb2fd25/`
- `.orchestrator/runs/c17ff8cf-e8c4-40d8-915b-6636dd41bd98/`

## Problem Statement

Recent runs show that Dev execution is still overly string-replacement driven and can drift from target intent:

1. rename intent was treated like content update instead of path rename
2. target intent drifted to wrong files
3. compile/runtime failures were detected but persisted diagnostics were not rich enough for deterministic second-pass correction

These are Milestone B concerns (edit operation semantics and verification), not application-specific concerns.

## Agnosticness Requirement

The solution must remain:

- framework agnostic
- OS agnostic (Ubuntu and Windows baseline)
- language and technology agnostic

No stack-specific hardcoded path assumptions are allowed in core edit/validation flow.

## Remediation Scope

### In scope

- structured edit contracts and operation execution
- first-class rename operation
- pre-apply and post-apply edit validation gates
- richer compile/runtime diagnostic persistence
- regression coverage for observed failure classes

### Out of scope

- intent router/artifact mode expansion
- framework-specific product heuristics

## Technical Plan

### 1) Structured edit contract

Add typed operation and validation contracts in `shared/edit_schemas.py` to standardize:

- operation type
- operation parameters
- pre-check and post-check results
- mutation evidence

### 2) Edit operation layer

Enhance `services/dev/edit_primitives.py`:

- keep existing generic operations
- add `update_imports`
- add `rename_path` as first-class operation with scope checks

### 3) Validation layer

Add `services/dev/edit_validator.py`:

- pre-apply checks:
  - target existence policy
  - basic syntax sanity
  - diff safety
- post-apply checks:
  - syntax sanity
  - intended region changed
  - import/reference integrity hints

### 4) Execution integration

Integrate checks in:

- `services/dev/phases/execute_implementation_target.py`
- `services/dev/dev_master_graph.py`

Each mutation must emit:

- selected operation
- candidate confidence
- pre-check result
- post-check result

### 5) Diagnostics and replay quality

Enrich `services/dev/dev_executor.py` task outcomes with:

- stdout/stderr previews
- failure class details
- key diagnostic hints

This improves next-run auto-remediation quality.

## Test Plan

Add tests:

- `tests/test_edit_primitives.py`
- `tests/test_edit_validator.py`
- `tests/test_milestone_b_regression_world_time_app.py`

Coverage should include:

- rename mismatch class (`main.ts` vs `main.tsx`)
- wrong-file mutation drift class
- compile failure diagnostics persistence
- Ubuntu/Windows path behavior parity

## Exit Criteria

- rename intents execute as actual renames
- mutation blocked when post-check fails
- final compile failures persist actionable diagnostics
- no framework-specific hardcoded behavior in core implementation flow
- regression suite passes for both observed run classes

## Roadmap Link

This is the detailed execution plan for Milestone B+ and should be referenced from `documentation/plan-next-steps.md`.
