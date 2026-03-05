# ADR Index

This file tracks architecture decisions for the overhaul.

## ADR-001: OpenHands-First Runtime

- Status: accepted
- Decision: replace single-executor DEV runtime with OpenHands-first multi-role loop.
- Rationale: stronger capability model and clearer policy controls.

## ADR-002: Deterministics Isolation

- Status: accepted
- Decision: all stack-specific logic lives under `deterministics/`.
- Rationale: preserve core portability and deterministic governance.

## ADR-003: Immutable Evidence Contracts

- Status: accepted
- Decision: enforce schema-versioned artifacts and immutable event logging.
- Rationale: pharma-grade traceability and reproducible audits.

## ADR-004: Aggressive Legacy Cleanup

- Status: accepted
- Decision: remove superseded docs/artifacts once replacement + validation gates pass.
- Rationale: reduce ambiguity and architecture drift.
