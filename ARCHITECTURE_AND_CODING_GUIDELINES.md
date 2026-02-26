# AI Orchestrator Architecture And Coding Guidelines

## 1) Purpose

This document defines the target architecture and coding standards for `ai-orchestrator` as the base for future refactor stories.

Primary optimization goals:

- readability and maintainability
- strong modular boundaries (PM/Dev/PR)
- scalable growth without file explosion
- safer command and path handling
- cross-platform reliability (Windows + Ubuntu)

---

## 2) Current State Snapshot

## 2.1 What is strong

- PM -> Dev structured planning and handoff
- command execution safety envelope (scope checks, retries, telemetry)
- run artifact persistence and debuggability
- recent path-safety upgrades (`creation_policy`, index refresh events, improved mutation semantics)

## 2.2 Current constraints

- `services/dev/dev_master_graph.py` is monolithic and hard to evolve safely.
- `services/dev/dev_executor.py` remains large and mixes multiple concerns.
- Dev depends on PM storage internals in `services/dev/dev_service.py` (coupling leak).
- top-level compatibility shims in `services/*.py` add dual import paths and complexity.

---

## 3) Risk Assessment (Architecture-Relevant)

## 3.1 Security risks

1. `shell=True` execution risk in command runner paths.
2. model-generated command surfaces need stronger allow/deny validation.
3. prompt/payload sanitization boundaries need hardening.
4. secret redaction patterns are useful but still partial.

## 3.2 Scalability risks

1. very large orchestrator modules reduce testability and change safety.
2. unbounded logs/event/state growth can degrade long-running sessions.
3. path normalization logic exists in multiple places, increasing drift risk.

## 3.3 Maintainability risks

1. broad exception swallowing hides failure context.
2. mixed import styles (shim and canonical) increase ambiguity.
3. insufficiently isolated responsibilities across PM/Dev orchestration layers.

---

## 4) Proposed Architecture Options

The team selected **Option A** as the target architecture. Option B is included as a valid alternative for comparison.

## 4.1 Option A (Selected): Clean Architecture + Ports/Adapters

### A) Layers

- **Domain**: pure contracts, invariants, policies
- **Application**: PM/Dev/PR use-cases and workflows
- **Infrastructure**: LLM client, shell runner, filesystem index, persistence
- **Interface**: CLI entrypoints and presentation/adapters

### B) Recommended filesystem layout

```text
src/
  domain/
    contracts/
    policies/
    value_objects/
  application/
    pm/
      use_cases/
      services/
    dev/
      use_cases/
      workflows/
    pr/
      use_cases/
      services/
  infrastructure/
    llm/
    shell/
    filesystem/
    storage/
  interface/
    cli/
    presenters/
shared/
  schemas/
  pathing/
tests/
```

### C) Boundary/dependency rules

- Interface -> Application -> Domain
- Infrastructure implements ports declared in Application/Domain
- Domain never imports Infrastructure
- PM/Dev/PR communicate only via shared contracts and explicit ports
- direct PM storage imports from Dev are forbidden

### D) Tradeoffs

- **Pros**: strongest separation, easiest governance for PM/Dev/PR products
- **Cons**: requires disciplined migration and interface definition

## 4.2 Option B (Alternative): Hexagonal + Vertical Features

### A) Shape

- feature-oriented modules (`pm/`, `dev/`, `pr/`)
- inbound ports (use-cases) and outbound ports (adapters)
- infrastructure adapters per feature

### B) Tradeoffs

- **Pros**: highly testable and scalable feature evolution
- **Cons**: can increase file count and architectural complexity if not curated

---

## 5) PM / Dev / PR Product Boundaries

## 5.1 PM boundary (oversight)

- owns requirement interpretation, clarifications, plan contracts, governance rules
- does not perform implementation mutations

## 5.2 Dev boundary (implementation)

- owns locate/modify/validate execution and command orchestration
- consumes PM contract but does not depend on PM persistence internals
- uses isolated workspace abstraction and explicit execution context

## 5.3 PR boundary (future product)

- owns branch/PR lifecycle and review automation (future)
- consumes summarized run artifacts and contracts only

## 5.4 Forbidden dependencies

- `dev -> pm storage` imports
- `pr -> dev internals` imports
- `application -> interface` imports
- cross-feature direct persistence calls without shared port

---

## 6) Coding And Naming Standards

## 6.1 Naming

- files/modules: `snake_case.py`
- classes/types: `PascalCase`
- functions/variables: `snake_case`
- constants: `UPPER_SNAKE_CASE`
- booleans should read as predicates (for example `is_within_scope`)

## 6.2 File size and split guidance

- target file size: 120-300 lines
- soft split trigger: >300 lines
- hard split trigger: >500 lines
- split by responsibility, not arbitrary line count
- avoid tiny over-fragmentation (<40 lines) unless file is a clear interface/type definition

## 6.3 Function design

- one primary responsibility per function
- explicit input/output contracts
- return structured results over ad-hoc strings where possible
- avoid side effects in utility functions

---

## 7) Good Practices (Required)

1. Contract-first PM -> Dev handoff with explicit target intent.
2. Deterministic target resolution against real indexed files.
3. Bounded telemetry/state retention with truncation policy.
4. Post-edit intent checks (semantic diff + targeted validation).
5. Clear phase events for observability and forensic debugging.
6. Cross-platform path handling via a single shared path utility policy.

---

## 8) Bad Practices (Banned)

1. speculative file creation without existence policy.
2. direct cross-layer imports (for example Dev importing PM storage internals).
3. monolithic orchestration classes that mix unrelated concerns.
4. unvalidated model-generated command execution.
5. broad exception swallowing without structured logging.
6. duplicate path normalization logic scattered across modules.

---

## 9) Repository Cognition Runtime Map (JSON)

This map should be generated incrementally and reused across locate/modify/validate loops.

```json
{
  "version": "1.0",
  "project_root": "projects/projectname",
  "generated_at": "2026-01-01T00:00:00Z",
  "files": [
    {
      "file": "projectname/src/App.tsx",
      "kind": "source",
      "language": "typescript",
      "imports": [
        { "module": "react", "members": ["useEffect", "useState"] },
        { "module": "./components/WorldClock", "members": ["default"] }
      ],
      "symbols": [
        { "name": "App", "type": "component", "range": "L1-L60" }
      ],
      "relationships": [
        { "type": "imports", "target": "projectname/src/components/WorldClock.tsx" },
        { "type": "used_by", "target": "projectname/src/main.tsx" }
      ],
      "entrypoint_score": 0.78,
      "confidence": 0.93
    }
  ],
  "graph": {
    "nodes": [
      "projectname/src/main.tsx",
      "projectname/src/App.tsx",
      "projectname/src/components/WorldClock.tsx"
    ],
    "edges": [
      {
        "from": "projectname/src/main.tsx",
        "to": "projectname/src/App.tsx",
        "type": "imports"
      }
    ]
  }
}
```

Runtime use:

- rank candidate files by relationship + symbol + intent relevance
- resolve entrypoint aliases (`index.*` vs `main.*`) without hard failure
- validate whether applied edits match intended symbols and dependencies

---

## 10) Adoption Rule

New user stories and refactors should conform to this document unless an Architecture Decision Record explicitly overrides a rule.
