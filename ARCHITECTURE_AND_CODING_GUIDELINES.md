# AI Orchestrator Architecture And Coding Guidelines

## Purpose

This repository uses a PM-led, OpenHands-first architecture:

- PM interprets requirements and emits a validated plan contract.
- Orchestrator manages approval and runtime wiring.
- OpenHands dispatcher/minion runtime performs implementation and validation.

## Module Boundaries

### PM (`services/pm`)

- Owns requirement clarification and planning contract generation.
- Must not execute filesystem mutations or shell commands.

### Orchestrator (`orchestrator.py`)

- Owns phase transitions: planning, approval, execution.
- Must remain thin and avoid embedding PM logic.

### Execution (`services/execution`)

- Owns OpenHands invocation, dispatcher policy, stage transitions, timeout/exit handling, and run artifact persistence.
- Must not depend on PM storage internals beyond the plan payload.

### Deterministics (`deterministics`)

- Owns stack-specific commands, gates, repo hints, prompts, and schema variants.
- Core runtime must consume these through contracts, not hardcoded framework logic.

## Dependency Rules

- Allowed: `orchestrator -> services.pm`, `orchestrator -> services.execution`
- Allowed: `services.execution -> shared` contracts
- Forbidden: any import from `services.dev.*`
- Forbidden: PM importing execution runtime internals
- Forbidden: execution importing PM context store internals
- Forbidden: core/orchestrator importing stack-specific deterministic internals directly

## Security and Reliability Rules

- Use `subprocess` with explicit argument lists (`shell=False`).
- Enforce timeout on external CLI execution.
- Enforce role-based tool policy checks before tool execution.
- Persist machine-readable run metadata and immutable stage events.
- Treat non-zero exit codes as failed execution status.
- Include `request_id` and `correlation_id` in all audit artifacts.

## Coding Standards

- Python naming: modules `snake_case`, classes `PascalCase`, functions `snake_case`.
- Keep files focused; split when responsibilities diverge.
- Prefer explicit typed dict payloads over opaque ad-hoc strings.
- Avoid broad `except` unless the failure is intentionally non-fatal and logged.

## Testing Standards

- Unit tests for PM contract generation behavior.
- Unit tests for OpenHands dispatcher, policy checks, and artifact persistence.
- Orchestrator tests for `plan`, `full`, and `execute` mode behavior.
- Integration tests for stage ordering, retry budget behavior, and resume semantics.
- No tests should reference removed `services/dev` runtime.

