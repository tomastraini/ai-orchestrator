# AI Orchestrator Architecture And Coding Guidelines

## Purpose

This repository uses a PM-led architecture:

- PM interprets requirements and emits a validated plan contract.
- Orchestrator manages approval and runtime wiring.
- Claude Code CLI performs implementation.

The system no longer contains an internal DEV graph/orchestration module.

## Module Boundaries

### PM (`services/pm`)

- Owns requirement clarification and planning contract generation.
- Must not execute filesystem mutations or shell commands.

### Orchestrator (`orchestrator.py`)

- Owns phase transitions: planning, approval, execution.
- Must remain thin and avoid embedding PM logic.

### Execution (`services/execution`)

- Owns Claude CLI invocation, runtime logs, timeout/exit handling, and run artifact persistence.
- Must not depend on PM storage internals beyond the plan payload.

## Dependency Rules

- Allowed: `orchestrator -> services.pm`, `orchestrator -> services.execution`
- Forbidden: any import from `services.dev.*`
- Forbidden: PM importing execution runtime internals
- Forbidden: execution importing PM context store internals

## Security and Reliability Rules

- Use `subprocess` with explicit argument lists (`shell=False`).
- Enforce timeout on external CLI execution.
- Persist machine-readable run metadata (`summary.json`) and textual logs (`cli_output.log`).
- Treat non-zero exit codes as failed execution status.

## Coding Standards

- Python naming: modules `snake_case`, classes `PascalCase`, functions `snake_case`.
- Keep files focused; split when responsibilities diverge.
- Prefer explicit typed dict payloads over opaque ad-hoc strings.
- Avoid broad `except` unless the failure is intentionally non-fatal and logged.

## Testing Standards

- Unit tests for PM contract generation behavior.
- Unit tests for executor process handling and artifact persistence.
- Orchestrator tests for `plan`, `full`, and `execute` mode behavior.
- No tests should reference removed `services/dev` runtime.

