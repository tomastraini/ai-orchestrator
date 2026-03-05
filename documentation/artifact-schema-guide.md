# Artifact Schema Guide

## Canonical `.ai/` Structure

- `.ai/spec.json` - normalized requirement/spec contract.
- `.ai/plan.json` - executable plan produced by planner.
- `.ai/audit.json` - stage decisions, policy outcomes, and provenance.
- `.ai/worklog.jsonl` - immutable event stream in execution order.
- `.ai/evidence/` - test logs, screenshots, command outputs, and gate payloads.
- `.ai/final-report.md` - final summary for reviewers.

## Schema Versioning

Every artifact must include:

- `schema_version` (semantic version string)
- `request_id`
- `correlation_id`
- `generated_at` (ISO timestamp)

Schema changes require:

- backwards compatibility statement
- migration note in `migration-status.md`
- ADR entry when contract semantics change

## AC-To-Evidence Traceability

Each acceptance criterion maps to one or more evidence records:

- `ac_id`
- `artifact_path`
- `evidence_type` (`test`, `runtime`, `ui`, `security`, `review`)
- `stage`
- `status`
- `notes`

`audit.json` is the source of truth for this mapping.
