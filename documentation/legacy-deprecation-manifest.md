# Legacy Deprecation Manifest

## Keep

- `README.md` (updated, canonical entrypoint)
- `ARCHITECTURE_AND_CODING_GUIDELINES.md` (updated, policy baseline)

## Replace Then Remove

- `documentation/TLDR.md` -> replaced by `documentation/README.md` and `documentation/architecture-overview.md`
- `documentation/application-purpose-and-gap-assessment.md` -> replaced by `documentation/architecture-overview.md`
- `documentation/dev-continuation-loop.md` -> replaced by `documentation/dispatcher-and-minion-policy.md`
- `documentation/m0-dev-module-ownership.md` -> replaced by `documentation/dispatcher-and-minion-policy.md`
- `documentation/plan-next-steps.md` -> replaced by `documentation/migration-status.md`
- `documentation/milestones/*` -> replaced by `documentation/migration-status.md` and ADRs
- `documentation/architecture-deep-dive.md` -> replaced by `documentation/architecture-overview.md`

## Remove Generated Legacy Artifacts

- `.orchestrator/runs/*`
- `.orchestrator/.orchestrator/*`
- `.orchestrator/dev_handoff.yaml.zst`
- redundant handoff payloads not used by runtime

## Removal Order

1. Land replacement docs and runtime contract scaffolding.
2. Pass smoke and schema validation gates.
3. Delete legacy docs and generated artifacts.
4. Enforce CI guardrails preventing reintroduction.
