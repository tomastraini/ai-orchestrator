# AI Orchestrator Next Steps (Toward ~70% Cursor-like Behavior)

## Purpose

This document defines the next architecture milestones required to evolve `ai-orchestrator` from an execution-centric workflow engine into a repository-aware cognitive agent with stronger editing precision and artifact flexibility.

## Current baseline

Today the system is strong at:

- PM -> Dev contract flow
- scoped command execution and retries
- telemetry, artifacts, and post-run traceability

It is still weaker at:

- deep repository cognition hardening across long-tail stacks
- symbol/region-precise editing
- intent routing across execution vs analysis vs documentation tasks

---

## Target: ~70% Cursor-like capability

For this roadmap, "~70% Cursor-like" means:

1. The agent reliably locates and edits intended files in scaffolded and existing projects.
2. It can recover from path/entrypoint mismatches without terminal failure in most cases.
3. It supports mixed workloads (code + docs + architecture outputs) in one flow.
4. It can reason over a persistent repository model, not only per-command context.
5. It evaluates semantic impact of its own edits before declaring completion.

---

## Milestone roadmap

## Milestone A: Repository Cognition Index

### Status

Implemented as a production baseline. The orchestrator now builds a richer cognition index and uses it in runtime target resolution and handoff context. Further hardening remains in follow-on milestones.

### Deliverables

- Delivered cognition index layer capturing:
  - symbols (functions/classes/components/routes)
  - import graph
  - dependency graph
  - entrypoint detection
  - test location mapping
  - config and toolchain detection
  - architecture signals (feature-based, layered, MVC-style hints)
- Delivered snapshot persistence per run with phase-based refresh hooks.

### Delivered implementation highlights

- New cognition package under `services/workspace/cognition/` with index builder, scaffold probe, resolver hints, and optional provider capability detection.
- PM -> Dev handoff enrichment with `cognition_snapshot` and `target_file_metadata`.
- Dev runtime integration with layered candidate resolution and post-mutation index refresh.
- Optional tooling adapters and fallback behavior to keep baseline operational without optional providers.

### Success criteria

- >=90% of implementation targets resolve to an existing candidate set before mutation.
- Entrypoint alias mismatches (`index.*` vs `main.*`) are auto-resolved in common scaffolds.

### Remaining A-hardening caveats

- Deeper AST/LSP precision for long-tail ecosystems (Java/C#/C++/COBOL-style repos).
- Stronger incremental/delta indexing for very large repositories.
- Broader cross-OS fixture calibration and confidence threshold tuning.

## Milestone B: Structured Editing Engine

Detailed remediation execution plan:

- `documentation/milestones/milestone-b-plus-remediation.md`

### Deliverables

- Introduce edit primitives:
  - `replace_symbol`
  - `insert_after_symbol`
  - `update_imports`
  - `patch_region`
- Add pre-apply and post-apply checks:
  - syntax sanity
  - local diff safety
  - touched-region verification

### Success criteria

- Reduce no-op/comment-only accepted edits to near zero.
- Improve first-pass checklist completion rate for implementation tasks.

## Milestone C: Persistent Repository Memory

### Deliverables

- Extend Dev graph state with persistent memory for:
  - files inspected
  - symbols found
  - assumptions and confidence
  - previous correction attempts
  - known constraints discovered at runtime
  - it can explore many files, search folders at the same time for increased performance and speed.

### Success criteria

- Agent avoids repeating failed target hypotheses across consecutive passes.
- Recovery decisions improve across attempts inside the same run.

## Milestone D: Locate -> Modify -> Validate Micro-Loop

### Deliverables

- Replace one-shot implementation behavior with per-target micro-loop:
  1. locate candidates
  2. read relevant snippets
  3. propose mutation
  4. apply mutation
  5. re-read and verify intent
  6. run targeted validation
  7. retry with refined hypothesis when mismatch detected

### Success criteria

- Lower cascade checklist failures from one target mismatch.
- Validation phase runs for most tasks instead of being skipped due to early abort.

## Milestone E: Intent-based Capability Router

### Deliverables

- Add intent router at PM->Dev handoff boundary:
  - `analysis_explain`
  - `artifact_generation`
  - `code_modification`
  - `execution_only`
- Route to distinct pipelines instead of always execution-first.

### Success criteria

- Non-code prompts (architecture explanations, docs generation) complete without unnecessary command execution.

## Milestone F: Artifact Generation Mode

### Deliverables

- First-class support for non-code outputs:
  - Markdown docs
  - ADR files
  - architecture maps
  - diagram sources (for example XML/mermaid payload sources)
  - API/spec artifacts
- Add artifact validation rules (schema/format checks where applicable).

### Success criteria

- Agent can produce high-quality repository documentation artifacts without entering compile pipeline.

---

## Repository relationship map proposal

Use a normalized JSON artifact generated and refreshed during runs:

```json
{
  "version": "1.0",
  "generated_at": "ISO-8601",
  "project_root": "projects/<project>",
  "files": [
    {
      "path": "src/App.tsx",
      "kind": "source",
      "language": "typescript",
      "entrypoint_score": 0.85,
      "symbols": [
        { "name": "App", "kind": "component", "range": "L1-L40" }
      ],
      "imports": [
        { "module": "react", "members": ["useEffect", "useState"] },
        { "module": "./components/WorldClock", "members": ["default"] }
      ],
      "exports": [
        { "name": "default", "kind": "component" }
      ],
      "relationships": [
        { "type": "imports", "target": "src/components/WorldClock.tsx" },
        { "type": "used_by", "target": "src/main.tsx" }
      ],
      "confidence": 0.93
    }
  ],
  "entrypoints": ["src/main.tsx"],
  "tests": ["src/**/*.test.tsx"],
  "configs": ["vite.config.ts", "tsconfig.json", "package.json"],
  "graph": {
    "nodes": ["src/main.tsx", "src/App.tsx", "src/components/WorldClock.tsx"],
    "edges": [
      { "from": "src/main.tsx", "to": "src/App.tsx", "type": "imports" },
      { "from": "src/App.tsx", "to": "src/components/WorldClock.tsx", "type": "imports" }
    ]
  }
}
```

### Runtime usage

- choose top-ranked edit targets
- detect missing/renamed entrypoint aliases
- validate that changed files align with intended symbols
- drive post-edit semantic checks

---

## Near-term execution order

1. Milestone D (micro-loop)
2. Milestone B (structured edits)
3. Milestone C (persistent memory)
4. Milestone E (intent router)
5. Milestone F (artifact mode)
6. Milestone A hardening track (precision + ecosystem coverage)

This order maximizes immediate path-awareness gains while progressively unlocking broader Cursor-like behavior.
