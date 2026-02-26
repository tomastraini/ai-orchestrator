# DEV Continuation Loop

## Session Lifecycle

- A DEV continuation session persists at `.orchestrator/dev_session.json`.
- Each session tracks:
  - `session_id`, `root_requirement`, timestamps, status
  - ordered `run_chain` entries for every iteration
  - lightweight `session_changelog` summaries
- Session transitions:
  - `active` on create
  - remains active across continuation-eligible outcomes
  - `closed` when user declines follow-up or explicitly closes

## Continuation Contract

- Handoff includes a `continuation` object with:
  - lineage: `session_id`, `parent_request_id`, `iteration_index`
  - intent: `trigger_type`, `delta_requirement`, `prior_run_summary`
  - behavior: `carry_forward_memory`, `continuation_mode`, `continuation_reason`
  - guidance: `continuation_guidance` (structured follow-up recommendations)
- DEV graph rehydrates this contract during ingest and uses it to:
  - carry forward memory deterministically (bounded by memory caps)
  - extend/reconcile checklist continuity
  - emit follow-up eligibility on finalize
  - emit continuation guidance when validation clarification is required

## Interactive Loop Semantics

- Continuation is enforced by default and can be disabled only by explicit deactivation (`continuation_mode=off` or env override).
- Continuation-eligible statuses:
  - `completed`
  - `partial_progress`
  - `recoverable_blocked`
  - `bootstrap_failed`
- In continuation-enabled mode, these statuses keep the session interactive and request the next improvement.
- For non-terminal outcomes, empty follow-up input does not auto-close the session; user must provide a next requirement or explicit end intent.
- Explicit end intent examples: `exit`, `end`, `stop`, `done`, `no more improvements`.
- For `completed`, session can close after explicit user confirmation that no further improvements are needed.

## Failure Policy

- Terminal hard-stop remains unchanged:
  - only approved terminal gate outcomes become `implementation_failed`
  - approved criteria: integrity compromise, LLM budget exhaustion, or catastrophic runtime failure that makes continuation impossible
- Continuation eligibility is exposed for:
  - `completed`
  - `partial_progress`
  - `recoverable_blocked`
  - `bootstrap_failed`
- `implementation_failed` is continuation-blocked with explicit reason.

## Manual And Browser Validation Policy

- If PM validation requirements are non-executable and no deterministic validation command can be inferred, DEV marks validation clarification required instead of terminal closure.
- Validation strategy remains technology-agnostic and can follow:
  - executable commands (when safely inferable)
  - manual validation evidence capture
  - browser automation via optional capability adapter
- Browser automation is adapter-based and optional:
  - if adapter is available and succeeds, validation can complete with captured evidence
  - if adapter is unavailable, system falls back to manual clarification/evidence without blocking continuation
- Missing compile/build inference is treated as recoverable continuation guidance, not an automatic dead-end.

## Artifacts And Telemetry

- Session/continuation artifacts:
  - `.orchestrator/session_summary.json`
  - `.orchestrator/iteration_summaries.jsonl`
  - `.orchestrator/continuation_decisions.jsonl`
  - `.orchestrator/requirement_deltas.jsonl`
- Existing run artifacts remain unchanged and include linked session metadata.
- Key continuation events:
  - `continuation_offered`, `continuation_accepted`, `continuation_declined`
  - `continuation_started`, `continuation_completed`
  - `session_closed`, `checklist_reopened`, `memory_carried_forward`
  - `validation_clarification_required`, `compile_inference_missing`, `continuation_guidance_ready`

## Rollout And Rollback

- Continuation is enabled by default in standard execution mode.
- Explicit deactivation paths remain available for one-shot workflows:
  - `--continuation-mode off`
  - `DEV_CONTINUATION_LOOP_ENABLED=false`
- Rollback:
  - set `DEV_CONTINUATION_LOOP_ENABLED=false`
  - loop behavior disables immediately, one-shot flow continues unchanged
  - persisted session artifacts remain read-only historical data.
