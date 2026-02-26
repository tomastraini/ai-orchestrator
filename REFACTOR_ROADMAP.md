# AI Orchestrator Refactor Roadmap

## 1) Goal

Refactor `ai-orchestrator` toward the selected target architecture (Option A: Clean Architecture + Ports/Adapters) with minimal delivery disruption and measurable quality gains.

Guiding priorities:

1. safety first
2. modular decomposition
3. PM/Dev/PR boundary enforcement
4. repository cognition enablement
5. structured editing and intent routing

---

## 2) Phase Plan

## Phase 0 - Safety hardening (first)

### Scope

- strengthen command validation policy for model-generated commands
- reduce `shell=True` risk surface and improve guard rails
- enforce bounded state/log/event retention
- tighten prompt/payload sanitization boundaries

### Candidate files

- `services/dev/dev_executor.py`
- `services/dev/command_policy.py`
- `services/dev/utils/subprocess_utils.py`
- `services/dev/types/dev_graph_state.py`
- `shared/pathing.py`

### Exit criteria

- command policy denies risky patterns by default
- state/log growth has configured bounds
- no regression in existing run artifact generation

## Phase 1 - Structural decomposition

### Scope

- split `services/dev/dev_master_graph.py` into responsibility-focused modules
- continue extraction of executor concerns into `services/dev/executor/`
- reduce static-method concentration

### Candidate files

- `services/dev/dev_master_graph.py`
- `services/dev/phases/*`
- `services/dev/executor/*`
- `services/dev/utils/*`

### Exit criteria

- no orchestration file >500 lines without exception
- clearer unit-test boundaries per module responsibility

## Phase 2 - Boundary decoupling (PM/Dev/PR)

### Scope

- remove Dev -> PM storage coupling
- enforce contract-only communication across PM/Dev/PR boundaries
- document and codify forbidden dependency directions

### Candidate files

- `services/dev/dev_service.py`
- `services/pm/dev_handoff_store.py`
- `shared/schemas.py`
- `shared/dev_schemas.py`

### Exit criteria

- no direct Dev imports of PM persistence internals
- PM/Dev/PR share contracts through approved shared interfaces only

## Phase 3 - Compatibility cleanup

### Scope

- deprecate and remove flat compatibility shims (`services/*.py` re-exports)
- standardize all imports to canonical package paths

### Candidate files

- `services/pm_service.py`
- `services/pm_context_store.py`
- `services/dev_service.py`
- `services/dev_executor.py`
- `services/dev_master_graph.py`
- `orchestrator.py`
- test files importing flat shims

### Exit criteria

- single canonical import style
- migration notes and deprecation removal completed

## Phase 4 - Repository cognition layer

### Scope

- add repository cognition index service:
  - symbols
  - imports
  - dependencies
  - entrypoints
  - test/config topology
- persist incremental runtime map snapshots

### Candidate files

- `services/workspace/project_index.py` (or successor service)
- new cognition module package under `services/` or `src/infrastructure/filesystem/`
- `services/dev/dev_master_graph.py` integration points

### Exit criteria

- implementation target resolution uses cognition map rather than only file-path hints
- entrypoint alias mismatches (`index.*` vs `main.*`) auto-resolve where confident

## Phase 5 - Editing and intent upgrades

### Scope

- introduce structured edit operations (symbol/region focused)
- add semantic diff acceptance checks
- add intent router:
  - analysis
  - docs/artifact generation
  - modification
  - execution

### Candidate files

- `services/dev/dev_master_graph.py` (or decomposed workflow package)
- `services/pm/pm_service.py`
- `orchestrator.py`
- new artifact generation and intent routing modules

### Exit criteria

- non-code requests complete without unnecessary execution flow
- code edits are validated semantically before checklist acceptance

---

## 3) File Touch Plan (Consolidated)

Primary touchpoints:

- `services/dev/dev_master_graph.py`
- `services/dev/dev_executor.py`
- `services/dev/dev_service.py`
- `services/pm/pm_service.py`
- `services/workspace/project_index.py`
- `shared/schemas.py`
- `shared/dev_schemas.py`
- `shared/pathing.py`
- `orchestrator.py`
- compatibility shim modules in `services/`

Secondary touchpoints:

- `tests/` suites for PM contracts, pathing, graph phases, executor, and validation behavior
- `documentation/` updates per phase milestone

---

## 4) Migration Safeguards

## 4.1 Backward compatibility

- keep compatibility layer for a bounded migration window
- use deprecation warnings and migration notes before removal

## 4.2 Test and quality gates per phase

- unit tests for split modules and contract boundaries
- regression tests for path resolution and checklist progression
- integration tests for scaffold -> modify -> validate flows
- security tests for command policy edge cases

## 4.3 Cross-platform acceptance (required)

- Windows 10 and Ubuntu parity:
  - path normalization
  - command execution behavior
  - artifact paths
  - scope enforcement

---

## 5) Success Metrics

Track these metrics at run level:

1. path-resolution success rate for implementation targets
2. checklist completion rate without cascade failure
3. count of unresolved target errors per run
4. median retries per task and recovery success %
5. monolithic hotspot reduction (max file size / complexity trend)
6. docs/artifact generation success rate for non-code intents
7. cross-platform parity pass rate (Windows + Ubuntu)

---

## 6) Immediate Execution Order

1. Phase 0 (safety hardening)
2. Phase 1 (decomposition of Dev orchestration)
3. Phase 2 (PM/Dev/PR boundary decoupling)
4. Phase 3 (compatibility cleanup)
5. Phase 4 (repository cognition layer)
6. Phase 5 (structured editing + intent router)

This sequence minimizes risk while enabling fast gains in correctness and long-term maintainability.
