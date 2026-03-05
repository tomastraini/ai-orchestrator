# PLAN.md — Development Roadmap

## Milestone Overview

| Milestone | Name | Description |
|-----------|------|-------------|
| 1 | Core Foundation | Project setup, configuration, base schemas, and CLI skeleton |
| 2 | Orchestrator Core | LangGraph state machine, state management, basic node flow |
| 3 | Clarification System | Ambiguity detection, confidence scoring, clarification loop |
| 4 | Deterministic Scaffolding | Policy engine, stack definitions, gate runner, contract loader |
| 5 | Repository Intelligence | tree-sitter parsing, ripgrep search, symbol index, repo map |
| 6 | Agent Framework | Base agent, planner, dispatcher, engineer agents |
| 7 | Sandbox Execution | OpenHands integration, Docker sandbox management |
| 8 | Multi-Agent Orchestration | Full task graph execution, parallel dispatch, review cycle |
| 9 | Integration Wrapper | CLI refinement, abstract integration layer, future-proofing |
| 10 | Autonomous Engineering | End-to-end autonomous workflow, self-healing, production hardening |

---

## Milestone 1 — Core Foundation

### Plan 1.1 — Initialize Project Repository

**Purpose:** Establish the Python project with proper packaging, dependencies, and tooling.

**Expected outcome:** A clean Python project with `pyproject.toml`, virtual environment support, dev tooling, and passing CI-ready test infrastructure.

**Dependencies:** None.

**Implementation steps:**

1. Create `pyproject.toml` with project metadata, Python 3.12+ requirement, and dependency groups
2. Create `requirements.txt` with pinned core dependencies
3. Create `.gitignore` for Python projects
4. Create top-level `__init__.py` files for all planned packages (empty stubs)
5. Create `tests/conftest.py` with basic pytest configuration
6. Create `tests/__init__.py`
7. Create a trivial `tests/test_smoke.py` that asserts `True` to validate the test runner works

**Files to create:**

```
pyproject.toml
requirements.txt
.gitignore
orchestrator/__init__.py
agents/__init__.py
clarification/__init__.py
contracts/__init__.py
deterministics/__init__.py
repo_intelligence/__init__.py
sandbox/__init__.py
integrations/__init__.py
integrations/inbound/__init__.py
integrations/outbound/__init__.py
memory/__init__.py
tools/__init__.py
tests/__init__.py
tests/conftest.py
tests/test_smoke.py
```

**Libraries to install:**

```
langgraph>=0.2
langchain-core>=0.3
langchain-anthropic>=0.3
pydantic>=2.0
pyyaml>=6.0
rich>=13.0
httpx>=0.27
pytest>=8.0
pytest-asyncio>=0.24
```

**Testing strategy:** Run `pytest tests/test_smoke.py` — must pass.

**Completion criteria:**
- `pytest` runs successfully with 1 passing test
- All package directories exist with `__init__.py`
- `pip install -e .` succeeds

---

### Plan 1.2 — Implement Configuration System

**Purpose:** Create a centralized configuration system that all components use for settings, paths, and runtime options.

**Expected outcome:** A `config.py` module that loads configuration from environment variables and optional YAML files, with Pydantic validation.

**Dependencies:** Plan 1.1

**Implementation steps:**

1. Create `orchestrator/config.py` with a Pydantic `Settings` class
2. Define configuration fields: `llm_provider`, `llm_model`, `llm_api_key`, `sandbox_type`, `log_level`, `deterministics_path`, `contracts_path`, `workspace_path`
3. Support loading from environment variables (prefixed `ORCHESTRATOR_`) and an optional `config.yaml` file
4. Create `orchestrator/config.yaml.example` showing all configurable fields
5. Write tests validating default values, environment variable override, and validation errors

**Files to create:**

```
orchestrator/config.py
orchestrator/config.yaml.example
tests/test_config.py
```

**Libraries:** No new libraries beyond Plan 1.1.

**Testing strategy:** Unit tests for config loading, defaults, env var override, and validation.

**Completion criteria:**
- Config loads from env vars and YAML
- Invalid configs raise clear Pydantic validation errors
- All tests pass

---

### Plan 1.3 — Define Core Data Schemas

**Purpose:** Define the Pydantic data models used throughout the system — requirements, tasks, agent messages, execution results.

**Expected outcome:** A shared schema module that all components import for type-safe data exchange.

**Dependencies:** Plan 1.1

**Implementation steps:**

1. Create `orchestrator/state.py` with the core state models:
   - `Requirement`: raw requirement text, source, metadata
   - `ClarifiedRequirement`: requirement + clarification Q&A + confidence score
   - `Task`: id, description, agent_type, dependencies, acceptance_criteria, status
   - `TaskGraph`: ordered collection of Tasks with dependency edges
   - `AgentMessage`: role, content, metadata, timestamp
   - `ExecutionResult`: task_id, status (success/failure/retry), output, errors, duration
   - `OrchestratorState`: the LangGraph state object combining all above
2. All models must use Pydantic v2 `BaseModel` with strict validation
3. Add `Status` enum: `pending`, `in_progress`, `clarifying`, `executing`, `reviewing`, `completed`, `failed`
4. Write comprehensive tests for serialization, validation, and edge cases

**Files to create:**

```
orchestrator/state.py
tests/test_state.py
```

**Testing strategy:** Unit tests covering model creation, validation, serialization to/from dict and JSON, and rejection of invalid data.

**Completion criteria:**
- All models are importable from `orchestrator.state`
- Serialization round-trips work correctly
- Invalid data raises `ValidationError`
- All tests pass

---

### Plan 1.4 — Create CLI Entry Point

**Purpose:** Build the initial CLI that accepts a requirement as input and displays structured output.

**Expected outcome:** A `main.py` entry point that accepts a prompt, wraps it as a `Requirement`, and prints a structured response. This is the first inbound integration.

**Dependencies:** Plan 1.2, Plan 1.3

**Implementation steps:**

1. Create `main.py` at project root as the primary entry point
2. Create `integrations/inbound/cli.py` implementing a simple CLI input loop using `rich` for formatting
3. Create `integrations/outbound/console.py` implementing console output with `rich`
4. Create `integrations/base.py` with abstract `InboundIntegration` and `OutboundIntegration` base classes
5. Wire `main.py` to: read prompt → create `Requirement` → print it → exit
6. This is a skeleton — no orchestrator logic yet, just the I/O path

**Files to create:**

```
main.py
integrations/base.py
integrations/inbound/cli.py
integrations/outbound/console.py
tests/test_cli.py
```

**Testing strategy:** Test that CLI integration creates valid `Requirement` objects. Test that console output renders without errors.

**Completion criteria:**
- `python main.py "Build a REST API"` prints a structured requirement summary
- Integration base classes define clear abstract interfaces
- All tests pass

---

## Milestone 2 — Orchestrator Core

### Plan 2.1 — Create LangGraph State Machine Skeleton

**Purpose:** Implement the core LangGraph graph that defines the orchestration workflow.

**Expected outcome:** A LangGraph `StateGraph` with placeholder nodes for each phase: `plan`, `clarify`, `dispatch`, `execute`, `review`, `complete`.

**Dependencies:** Plan 1.3

**Implementation steps:**

1. Create `orchestrator/graph.py` defining the `StateGraph` using `OrchestratorState`
2. Define nodes: `plan`, `clarify`, `dispatch`, `execute`, `review`, `complete`, `failed`
3. Define edges and conditional routing:
   - `START` → `plan`
   - `plan` → `clarify` (if confidence < threshold) OR `dispatch` (if confidence >= threshold)
   - `clarify` → `plan` (loop back with answers)
   - `dispatch` → `execute`
   - `execute` → `review`
   - `review` → `complete` (if approved) OR `execute` (if needs revision)
   - Any node → `failed` (on unrecoverable error)
4. Each node is a stub function that logs entry and returns state unchanged
5. Create `orchestrator/nodes.py` with stub implementations for each node function
6. Write tests that compile the graph and run it with mock state

**Files to create:**

```
orchestrator/graph.py
orchestrator/nodes.py
tests/test_orchestrator.py
```

**Testing strategy:** Test that the graph compiles. Test that running the graph with a mock state traverses expected nodes. Test conditional routing based on confidence score.

**Completion criteria:**
- `StateGraph` compiles without errors
- Running with confidence >= threshold skips clarification
- Running with confidence < threshold enters clarification loop
- All node stubs execute without errors
- All tests pass

---

### Plan 2.2 — Implement Execution Memory and Audit Trail

**Purpose:** Create the execution memory system that records all state transitions, agent actions, and decisions.

**Expected outcome:** A memory module that logs every orchestrator event with timestamps, enabling replay and debugging.

**Dependencies:** Plan 1.3

**Implementation steps:**

1. Create `memory/execution_log.py` with an `ExecutionLog` class
2. Implement methods: `log_event(event_type, data)`, `get_events(filter)`, `get_timeline()`
3. Events are stored in-memory as a list of `LogEntry` Pydantic models
4. Each `LogEntry` has: `timestamp`, `event_type`, `node_name`, `data`, `state_snapshot_id`
5. Create `memory/store.py` with a simple file-backed JSON store for persistence between runs
6. Create `memory/task_graph.py` with `TaskGraphManager` for managing task lifecycle
7. Write tests for logging, retrieval, filtering, and persistence

**Files to create:**

```
memory/execution_log.py
memory/task_graph.py
memory/store.py
tests/test_memory.py
```

**Testing strategy:** Unit tests for event logging, filtering by type, timeline ordering, and JSON persistence round-trip.

**Completion criteria:**
- Events are logged with timestamps
- Events can be filtered by type and time range
- Task graph tracks task status transitions
- Persistence saves and loads correctly
- All tests pass

---

### Plan 2.3 — Wire Orchestrator to CLI

**Purpose:** Connect the CLI entry point to the LangGraph orchestrator so that a prompt flows through the full (stubbed) pipeline.

**Expected outcome:** Running `python main.py "Build a REST API"` creates a `Requirement`, runs it through the LangGraph state machine, and prints the execution trace.

**Dependencies:** Plan 1.4, Plan 2.1, Plan 2.2

**Implementation steps:**

1. Update `main.py` to instantiate the LangGraph graph and invoke it with the CLI input
2. Update `orchestrator/nodes.py` stub for `plan` node to set confidence to 1.0 (skip clarification for now)
3. Wire `ExecutionLog` into the orchestrator so each node logs its execution
4. Update console output to display the execution trace after completion
5. Write an integration test that runs the full pipeline end-to-end

**Files to modify:**

```
main.py
orchestrator/nodes.py
```

**Files to create:**

```
tests/test_integration_basic.py
```

**Testing strategy:** Integration test that runs full pipeline with a test prompt and verifies all expected nodes are visited.

**Completion criteria:**
- CLI input flows through the full LangGraph pipeline
- Execution log captures all node transitions
- Console output shows the execution trace
- Integration test passes

---

## Milestone 3 — Clarification System

### Plan 3.1 — Implement Ambiguity Analyzer

**Purpose:** Build the component that analyzes a requirement and identifies areas of ambiguity.

**Expected outcome:** An analyzer that takes a `Requirement` and produces a list of ambiguity signals with associated questions.

**Dependencies:** Plan 1.3

**Implementation steps:**

1. Create `clarification/schemas.py` with data models:
   - `AmbiguitySignal`: category (scope, technical, constraint, acceptance), description, severity, suggested_question
   - `AmbiguityReport`: list of signals, overall_ambiguity_score (0.0-1.0)
   - `ClarificationQuestion`: question text, category, priority, context
   - `ClarificationAnswer`: question_id, answer text, confidence_delta
2. Create `clarification/analyzer.py` with `AmbiguityAnalyzer` class
3. The analyzer uses an LLM call with a structured prompt to identify ambiguities
4. The prompt instructs the LLM to output structured JSON matching `AmbiguityReport`
5. Parse and validate the LLM response using Pydantic
6. Write tests with mock LLM responses

**Files to create:**

```
clarification/schemas.py
clarification/analyzer.py
tests/test_clarification.py
```

**Testing strategy:** Unit tests with mocked LLM responses. Test that valid LLM output parses correctly. Test that malformed output is handled gracefully.

**Completion criteria:**
- Analyzer accepts a requirement and returns an `AmbiguityReport`
- Output is structured and validated
- Mock tests pass for various ambiguity scenarios

---

### Plan 3.2 — Implement Confidence Scoring

**Purpose:** Build the confidence scoring system that determines when a requirement is clear enough to execute.

**Expected outcome:** A confidence scorer that computes a score from 0.0 to 1.0 based on requirement completeness, ambiguity signals, and clarification history.

**Dependencies:** Plan 3.1

**Implementation steps:**

1. Create `clarification/confidence.py` with `ConfidenceScorer` class
2. Define scoring dimensions:
   - `scope_clarity`: Is the scope well-defined? (0.0-1.0)
   - `technical_clarity`: Are technical requirements clear? (0.0-1.0)
   - `acceptance_clarity`: Are acceptance criteria defined? (0.0-1.0)
   - `constraint_clarity`: Are constraints and limitations clear? (0.0-1.0)
3. Overall confidence = weighted average of dimensions
4. Default threshold for execution: 0.8
5. The scorer uses an LLM call with the requirement + clarification history to produce dimension scores
6. Each clarification answer should increase relevant dimension scores
7. Write tests with various requirement scenarios

**Files to create:**

```
clarification/confidence.py
tests/test_confidence.py
```

**Testing strategy:** Unit tests verifying score computation, threshold checking, and score improvement after clarification answers.

**Completion criteria:**
- Confidence scorer produces a 0.0-1.0 score with dimension breakdown
- Scores below threshold trigger clarification
- Scores above threshold allow execution
- All tests pass

---

### Plan 3.3 — Implement Clarification Loop

**Purpose:** Build the clarification loop that iterates between ambiguity analysis, question generation, user answers, and confidence re-scoring.

**Expected outcome:** A self-contained clarification loop that can be invoked by the orchestrator and returns a `ClarifiedRequirement` when confidence is sufficient.

**Dependencies:** Plan 3.1, Plan 3.2, Plan 1.4

**Implementation steps:**

1. Create `clarification/loop.py` with `ClarificationLoop` class
2. Implement the loop:
   ```
   while confidence < threshold and iterations < max_iterations:
       report = analyzer.analyze(requirement, history)
       questions = generate_questions(report)
       answers = await integration.send_clarification(questions)
       history.append((questions, answers))
       confidence = scorer.score(requirement, history)
   ```
3. `generate_questions` prioritizes questions by severity and information gain
4. Maximum iterations configurable (default: 5) to prevent infinite loops
5. If max iterations reached without threshold, proceed with warning
6. Wire into the `clarify` node in `orchestrator/nodes.py`
7. Write tests with simulated Q&A exchanges

**Files to create:**

```
clarification/loop.py
```

**Files to modify:**

```
orchestrator/nodes.py  (update clarify node to use ClarificationLoop)
integrations/inbound/cli.py  (add interactive Q&A support)
```

**Testing strategy:** Unit tests simulating multi-turn clarification. Test that confidence increases with each answer. Test max iteration safety. Test integration with orchestrator node.

**Completion criteria:**
- Clarification loop runs until confidence threshold met
- Questions are prioritized and non-redundant
- CLI supports interactive Q&A
- Loop respects max iteration limit
- All tests pass

---

## Milestone 4 — Deterministic Scaffolding

### Plan 4.1 — Create Deterministic File Loader

**Purpose:** Build the system that dynamically loads YAML configuration files for stacks, policies, and gates.

**Expected outcome:** A loader that reads YAML files from the `deterministics/` directory, validates them against Pydantic schemas, and makes them available to agents.

**Dependencies:** Plan 1.1

**Implementation steps:**

1. Create `deterministics/loader.py` with `DeterministicLoader` class
2. Implement methods: `load_stack(name)`, `load_policy(name)`, `load_gate(name)`, `list_stacks()`, `list_policies()`, `list_gates()`
3. Each method reads from the appropriate subdirectory and validates against Pydantic models
4. Create Pydantic schemas in `deterministics/__init__.py`:
   - `StackDefinition`: name, language, framework, package_manager, test_runner, build_command, dev_command, source_dirs, test_dirs, config_files, conventions
   - `PolicyRule`: id, severity, description, threshold (optional), pattern (optional)
   - `PolicyDefinition`: name, rules list
   - `GateCheck`: name, required, command
   - `GateDefinition`: name, checks list
5. Support Jinja2-style variable substitution in gate commands (e.g., `{{stack.test_runner}}`)
6. Create template files for each type
7. Write tests for loading, validation, and variable substitution

**Files to create:**

```
deterministics/loader.py
deterministics/stacks/_template.yaml
deterministics/stacks/python-fastapi.yaml
deterministics/stacks/react-nextjs.yaml
deterministics/stacks/node-nestjs.yaml
deterministics/policies/_template.yaml
deterministics/policies/coding_standards.yaml
deterministics/policies/security_baseline.yaml
deterministics/gates/_template.yaml
deterministics/gates/pre_commit.yaml
deterministics/gates/pre_merge.yaml
tests/test_deterministics.py
tests/fixtures/sample_stack.yaml
```

**Libraries:** No new libraries. PyYAML already in requirements.

**Testing strategy:** Unit tests for loading each file type. Tests for validation errors on malformed YAML. Tests for variable substitution.

**Completion criteria:**
- All YAML files load and validate correctly
- Variable substitution works in gate commands
- Invalid files produce clear errors
- At least 3 stack definitions exist
- All tests pass

---

### Plan 4.2 — Implement Policy Engine

**Purpose:** Build the policy evaluation engine that checks agent output against loaded policies.

**Expected outcome:** A policy engine that receives code changes or execution results and evaluates them against loaded policy rules, returning pass/fail results.

**Dependencies:** Plan 4.1

**Implementation steps:**

1. Create `deterministics/policy_engine.py` with `PolicyEngine` class
2. Implement `evaluate(policy_name, context) -> PolicyResult`
3. `PolicyResult` contains: `passed` (bool), `violations` (list of `PolicyViolation`), `warnings` (list)
4. `PolicyViolation`: rule_id, severity, message, file_path (optional), line (optional)
5. Policy evaluation is extensible — each rule type has an evaluator:
   - `pattern` rules: regex match against file contents
   - `threshold` rules: numeric comparison (e.g., test coverage)
   - `command` rules: run a shell command and check exit code
   - `llm` rules: ask LLM to evaluate against description (fallback for complex rules)
6. Severity levels: `info`, `warning`, `error`, `critical`
7. `error` and `critical` violations cause task failure
8. Write tests for each rule type

**Files to create:**

```
deterministics/policy_engine.py
tests/test_policy_engine.py
```

**Testing strategy:** Unit tests for each evaluator type. Test pass/fail scenarios. Test severity-based outcome determination.

**Completion criteria:**
- Policy engine evaluates all rule types
- Violations are reported with clear context
- Critical/error violations cause failure
- All tests pass

---

### Plan 4.3 — Implement Gate Runner

**Purpose:** Build the gate runner that executes pre-defined checks at workflow checkpoints.

**Expected outcome:** A gate runner that executes a gate definition's checks (tests, lints, security scans) and returns aggregate pass/fail.

**Dependencies:** Plan 4.1, Plan 4.2

**Implementation steps:**

1. Create `deterministics/gate_runner.py` with `GateRunner` class
2. Implement `run_gate(gate_name, stack, sandbox) -> GateResult`
3. `GateResult`: `passed` (bool), `check_results` (list of `CheckResult`)
4. `CheckResult`: `name`, `passed`, `required`, `output`, `duration`
5. Gate runner resolves variable substitutions using the stack definition
6. Required checks must all pass for the gate to pass
7. Optional checks are reported but don't block
8. Gate runner executes checks inside the sandbox (or locally for now, sandbox in Milestone 7)
9. Write tests with mock command execution

**Files to create:**

```
deterministics/gate_runner.py
tests/test_gate_runner.py
```

**Testing strategy:** Unit tests with mocked command execution. Test required vs optional check behavior. Test variable substitution from stack definitions.

**Completion criteria:**
- Gate runner executes all checks in a gate definition
- Required checks block on failure
- Variable substitution works
- All tests pass

---

### Plan 4.4 — Implement Contract Loader and Validator

**Purpose:** Build the project contract system that defines per-project configuration, policies, and constraints.

**Expected outcome:** A contract loader that reads project contracts and a validator that checks compliance.

**Dependencies:** Plan 4.1, Plan 4.2

**Implementation steps:**

1. Create `contracts/schemas.py` with Pydantic models:
   - `ProjectContract`: name, repository, stack (reference), policies (list of references), gates (list of references), acceptance_criteria (list of strings), constraints
   - `ContractConstraints`: max_file_changes, restricted_paths, required_reviewers, timeout
2. Create `contracts/loader.py` with `ContractLoader` class
3. Implement `load(path) -> ProjectContract` with validation
4. The loader resolves references: stack name → loaded StackDefinition, policy names → loaded PolicyDefinitions
5. Create `contracts/validator.py` with `ContractValidator` class
6. Implement `validate(contract, changes) -> ValidationResult` — checks changes against contract constraints
7. Create a sample contract for testing
8. Write tests for loading, reference resolution, and validation

**Files to create:**

```
contracts/schemas.py
contracts/loader.py
contracts/validator.py
tests/test_contracts.py
tests/fixtures/sample_contract.yaml
```

**Testing strategy:** Unit tests for contract loading, reference resolution, constraint validation (file count, restricted paths).

**Completion criteria:**
- Contracts load and validate correctly
- Stack and policy references resolve
- Constraint validation catches violations
- All tests pass

---

## Milestone 5 — Repository Intelligence

### Plan 5.1 — Implement File Search with ripgrep

**Purpose:** Build the fast file search layer using ripgrep.

**Expected outcome:** A file search module that wraps ripgrep for fast full-text search and file listing across repositories.

**Dependencies:** Plan 1.1

**Implementation steps:**

1. Create `repo_intelligence/file_search.py` with `FileSearch` class
2. Implement methods:
   - `search(pattern, path, file_types=None, max_results=100) -> list[SearchResult]`
   - `find_files(glob_pattern, path) -> list[str]`
   - `search_and_replace(pattern, replacement, path, dry_run=True) -> list[Change]`
3. `SearchResult`: file_path, line_number, line_content, context_before, context_after
4. Wrap ripgrep via subprocess (`rg` command). Fall back to Python `re` + `os.walk` if ripgrep is unavailable
5. Handle encoding issues, binary files, and large result sets gracefully
6. Write tests against a fixture repository

**Files to create:**

```
repo_intelligence/file_search.py
tests/test_file_search.py
tests/fixtures/sample_repo/src/main.py
tests/fixtures/sample_repo/src/utils.py
tests/fixtures/sample_repo/tests/test_main.py
tests/fixtures/sample_repo/package.json
tests/fixtures/sample_repo/README.md
```

**Libraries:** ripgrep must be available on PATH (system dependency, not pip). No new Python libraries.

**Testing strategy:** Unit tests searching fixture repo for known patterns. Test file type filtering. Test fallback behavior.

**Completion criteria:**
- Text search returns accurate results with line numbers
- File glob search works
- Graceful fallback when ripgrep is unavailable
- All tests pass

---

### Plan 5.2 — Implement AST Parser with tree-sitter

**Purpose:** Build the AST parsing layer for language-aware code understanding.

**Expected outcome:** A parser that extracts functions, classes, imports, and other symbols from source files using tree-sitter.

**Dependencies:** Plan 1.1

**Implementation steps:**

1. Create `repo_intelligence/ast_parser.py` with `ASTParser` class
2. Implement methods:
   - `parse_file(path) -> ParsedFile`
   - `extract_symbols(path) -> list[Symbol]`
   - `extract_imports(path) -> list[Import]`
3. `ParsedFile`: file_path, language, symbols, imports, tree (raw AST)
4. `Symbol`: name, kind (function/class/method/variable), start_line, end_line, parent (optional)
5. `Import`: module, names, alias, is_relative
6. Support initial languages: Python, TypeScript/JavaScript, Go, Java, Rust
7. Use `tree-sitter` Python bindings and install language grammars
8. Detect language from file extension
9. Write tests parsing fixture files in multiple languages

**Files to create:**

```
repo_intelligence/ast_parser.py
tests/test_ast_parser.py
tests/fixtures/sample_repo/src/example.ts
tests/fixtures/sample_repo/src/example.go
```

**Libraries to install:**

```
tree-sitter>=0.22
tree-sitter-python>=0.23
tree-sitter-javascript>=0.23
tree-sitter-typescript>=0.23
tree-sitter-go>=0.23
tree-sitter-java>=0.23
tree-sitter-rust>=0.23
```

**Why tree-sitter:** tree-sitter is the industry standard for fast, incremental, multi-language AST parsing. It is used by GitHub, Neovim, Helix, and many code intelligence tools. It provides consistent AST APIs across languages, is extremely fast, and has mature Python bindings.

**Testing strategy:** Parse fixture files in multiple languages and verify extracted symbols and imports.

**Completion criteria:**
- Parses Python, TypeScript, Go, Java files correctly
- Extracts functions, classes, methods, imports
- Handles parse errors gracefully (returns partial results)
- All tests pass

---

### Plan 5.3 — Implement Symbol Index

**Purpose:** Build a symbol index that provides fast lookups for definitions and references across an entire repository.

**Expected outcome:** A symbol index that indexes all symbols in a repo and supports queries like "find definition of function X" or "find all usages of class Y".

**Dependencies:** Plan 5.2

**Implementation steps:**

1. Create `repo_intelligence/symbol_index.py` with `SymbolIndex` class
2. Implement methods:
   - `build(repo_path) -> None` — index all files
   - `find_definition(name) -> list[Symbol]`
   - `find_references(name) -> list[Reference]`
   - `find_symbols(query, kind=None) -> list[Symbol]`
   - `get_file_symbols(path) -> list[Symbol]`
3. `Reference`: file_path, line, column, context
4. Index is stored in-memory as dictionaries keyed by symbol name
5. Support incremental updates: `update_file(path)` re-indexes a single file
6. Write tests against the fixture repository

**Files to create:**

```
repo_intelligence/symbol_index.py
tests/test_symbol_index.py
```

**Testing strategy:** Build index from fixture repo, test definition and reference lookups, test incremental update.

**Completion criteria:**
- Full repo indexing completes in reasonable time
- Definition lookups return correct results
- Incremental updates work correctly
- All tests pass

---

### Plan 5.4 — Implement Dependency Graph and Repo Map

**Purpose:** Build the dependency graph extractor and high-level repository map generator.

**Expected outcome:** A module that maps import dependencies between files and generates a structural overview of any repository.

**Dependencies:** Plan 5.2, Plan 5.3

**Implementation steps:**

1. Create `repo_intelligence/dependency_graph.py` with `DependencyGraph` class
2. Implement methods:
   - `build(repo_path) -> None` — analyze all imports
   - `get_dependencies(file_path) -> list[str]` — files this file depends on
   - `get_dependents(file_path) -> list[str]` — files that depend on this file
   - `get_import_chain(from_file, to_file) -> list[str]` — shortest import path
3. Create `repo_intelligence/repo_map.py` with `RepoMap` class
4. Implement `generate(repo_path) -> RepoMapResult`:
   - Directory tree with annotations
   - Key files (entry points, configs, tests)
   - Module structure
   - Technology stack detection
5. Create `repo_intelligence/change_impact.py` with `ChangeImpact` class
6. Implement `predict(changed_files) -> ImpactReport`:
   - Affected files (transitive dependents)
   - Affected tests
   - Risk assessment
7. Write tests using fixture repository

**Files to create:**

```
repo_intelligence/dependency_graph.py
repo_intelligence/repo_map.py
repo_intelligence/change_impact.py
tests/test_dependency_graph.py
tests/test_repo_map.py
```

**Testing strategy:** Build dependency graph from fixture repo. Verify edges are correct. Test repo map generation produces valid output. Test change impact prediction.

**Completion criteria:**
- Dependency graph correctly maps imports to files
- Repo map produces useful structural overview
- Change impact correctly identifies affected files
- All tests pass

---

### Plan 5.5 — Create Repo Intelligence Indexer

**Purpose:** Build the orchestrating indexer that coordinates all repo intelligence components.

**Expected outcome:** A single `Indexer` class that runs the full indexing pipeline and provides a unified interface for agents to query repo intelligence.

**Dependencies:** Plan 5.1, Plan 5.2, Plan 5.3, Plan 5.4

**Implementation steps:**

1. Create `repo_intelligence/indexer.py` with `Indexer` class
2. Implement `index(repo_path) -> RepoIndex`:
   - Run file search to enumerate files
   - Parse all supported files with AST parser
   - Build symbol index
   - Build dependency graph
   - Generate repo map
3. `RepoIndex` provides unified query interface:
   - `search(query)` — delegates to file search
   - `find_symbol(name)` — delegates to symbol index
   - `get_map()` — delegates to repo map
   - `predict_impact(files)` — delegates to change impact
4. Support incremental re-indexing on file changes
5. Write integration tests

**Files to create:**

```
repo_intelligence/indexer.py
tests/test_indexer_integration.py
```

**Testing strategy:** Integration test that indexes the fixture repo and runs queries through the unified interface.

**Completion criteria:**
- Full indexing pipeline runs end-to-end
- Unified query interface works for all query types
- Incremental re-indexing works
- Integration test passes

---

## Milestone 6 — Agent Framework

### Plan 6.1 — Implement Base Agent Class

**Purpose:** Create the base agent class that all specialized agents inherit from.

**Expected outcome:** An abstract base agent with standard lifecycle methods, tool access, LLM interaction, and deterministic policy integration.

**Dependencies:** Plan 1.3, Plan 4.1

**Implementation steps:**

1. Create `agents/base.py` with `BaseAgent` class
2. Define agent lifecycle methods:
   - `async execute(task: Task, context: AgentContext) -> ExecutionResult`
   - `async plan(task: Task) -> list[Step]` — break task into steps
   - `async act(step: Step) -> StepResult` — execute a single step
   - `async validate(result: ExecutionResult) -> ValidationResult` — check against policies
3. `AgentContext`: project_contract, stack_definition, policies, repo_index, sandbox, memory
4. Define tool interface that agents use to interact with the environment:
   - `read_file`, `write_file`, `search`, `run_command`, `find_symbol`
5. LLM interaction through a configurable `LLMClient` wrapper
6. Create `agents/llm_client.py` wrapping LangChain's chat model interface
7. Write tests with a concrete test agent

**Files to create:**

```
agents/base.py
agents/llm_client.py
tests/test_base_agent.py
```

**Testing strategy:** Create a `TestAgent` subclass that implements abstract methods. Test lifecycle execution. Test tool delegation. Mock LLM calls.

**Completion criteria:**
- Base agent defines clear lifecycle
- Tool interface is clean and extensible
- LLM client wraps LangChain correctly
- Test agent executes successfully
- All tests pass

---

### Plan 6.2 — Implement Planner Agent

**Purpose:** Build the planner (PM) agent responsible for requirement analysis, task decomposition, and acceptance criteria generation.

**Expected outcome:** A planner agent that takes a clarified requirement and produces a detailed task graph with dependencies and acceptance criteria.

**Dependencies:** Plan 6.1, Plan 3.1, Plan 5.5

**Implementation steps:**

1. Create `agents/planner.py` with `PlannerAgent(BaseAgent)` class
2. Implement task decomposition:
   - Analyze the clarified requirement
   - Use repo intelligence to understand existing codebase
   - Decompose into tasks with clear descriptions
   - Assign agent types to tasks (backend, frontend, qa, etc.)
   - Define dependencies between tasks
   - Generate acceptance criteria per task
3. Create `agents/prompts/planner_system.md` with the planner system prompt
4. The prompt instructs the LLM to output structured JSON matching `TaskGraph`
5. Parse and validate LLM output
6. Wire into the `plan` node in `orchestrator/nodes.py`
7. Write tests with mock LLM responses

**Files to create:**

```
agents/planner.py
agents/prompts/planner_system.md
tests/test_planner.py
```

**Files to modify:**

```
orchestrator/nodes.py  (update plan node to use PlannerAgent)
```

**Testing strategy:** Unit tests with mocked LLM. Test task decomposition produces valid TaskGraph. Test dependency ordering. Test acceptance criteria generation.

**Completion criteria:**
- Planner produces a valid TaskGraph from a requirement
- Tasks have correct agent type assignments
- Dependencies are acyclic
- Acceptance criteria are specific and testable
- All tests pass

---

### Plan 6.3 — Implement Dispatcher

**Purpose:** Build the dispatcher that routes tasks to appropriate agents based on type, dependencies, and availability.

**Expected outcome:** A dispatcher that reads a task graph, resolves execution order, and dispatches tasks to agents sequentially (parallel in Milestone 8).

**Dependencies:** Plan 6.1, Plan 6.2

**Implementation steps:**

1. Create `agents/dispatcher.py` with `Dispatcher` class
2. Implement task scheduling:
   - Topological sort of task graph based on dependencies
   - Execute tasks in dependency order
   - Track task status (pending → in_progress → completed/failed)
   - Handle task failure: retry once, then mark as failed and report
3. Implement agent routing:
   - Map agent types to agent classes
   - Instantiate appropriate agent for each task
   - Pass relevant context (contract, stack, repo index)
4. Wire into the `dispatch` node in `orchestrator/nodes.py`
5. Write tests with mock agents

**Files to create:**

```
agents/dispatcher.py
tests/test_dispatcher.py
```

**Files to modify:**

```
orchestrator/nodes.py  (update dispatch and execute nodes)
```

**Testing strategy:** Unit tests for topological sort. Test sequential execution respects dependencies. Test failure handling and retry. Mock all agent execution.

**Completion criteria:**
- Tasks execute in correct dependency order
- Failed tasks are retried once then reported
- Agent routing maps correctly
- All tests pass

---

### Plan 6.4 — Implement Engineer Agent

**Purpose:** Build the general-purpose engineering agent that writes code.

**Expected outcome:** An engineer agent that receives a task, reads relevant code, generates changes, and validates them against policies.

**Dependencies:** Plan 6.1, Plan 5.5, Plan 4.2

**Implementation steps:**

1. Create `agents/engineer.py` with `EngineerAgent(BaseAgent)` class
2. Implement the engineering workflow:
   - Read task description and acceptance criteria
   - Query repo intelligence for relevant files and symbols
   - Generate a plan for code changes
   - Write code changes (create/modify files)
   - Run policy validation
   - Run gate checks if applicable
3. Create `agents/prompts/engineer_system.md` with the engineer system prompt
4. The prompt emphasizes: follow project conventions, respect restricted paths, write tests, use stack definitions
5. Output is a list of file changes (create, modify, delete) with diffs
6. Write tests with mocked LLM and repo intelligence

**Files to create:**

```
agents/engineer.py
agents/prompts/engineer_system.md
tests/test_engineer.py
```

**Testing strategy:** Unit tests with mocked dependencies. Test that the agent queries repo intelligence. Test that output respects contract constraints. Test policy validation.

**Completion criteria:**
- Engineer agent produces file changes for a given task
- Changes respect contract constraints
- Policy validation runs on output
- All tests pass

---

### Plan 6.5 — Implement QA and Reviewer Agents

**Purpose:** Build the QA agent (test execution) and code reviewer agent (policy compliance checking).

**Expected outcome:** A QA agent that runs tests and reports results, and a reviewer agent that checks code changes against policies and best practices.

**Dependencies:** Plan 6.1, Plan 4.2, Plan 4.3

**Implementation steps:**

1. Create `agents/qa.py` with `QAAgent(BaseAgent)` class:
   - Run test suite via sandbox
   - Parse test results
   - Report coverage
   - Identify failing tests
   - Suggest fixes for failures
2. Create `agents/reviewer.py` with `ReviewerAgent(BaseAgent)` class:
   - Review code changes against loaded policies
   - Check for common issues (security, performance, style)
   - Generate review comments
   - Approve or request changes
3. Create prompts for both agents
4. Wire into the `review` node in `orchestrator/nodes.py`
5. Write tests for both agents

**Files to create:**

```
agents/qa.py
agents/reviewer.py
agents/prompts/qa_system.md
agents/prompts/reviewer_system.md
tests/test_qa_agent.py
tests/test_reviewer_agent.py
```

**Files to modify:**

```
orchestrator/nodes.py  (update review node)
```

**Testing strategy:** Unit tests with mocked sandbox and LLM. Test QA agent parses test results correctly. Test reviewer catches policy violations.

**Completion criteria:**
- QA agent runs tests and reports structured results
- Reviewer agent identifies policy violations
- Both agents produce actionable output
- All tests pass

---

## Milestone 7 — Sandbox Execution

### Plan 7.1 — Implement Sandbox Manager

**Purpose:** Build the sandbox manager that creates and manages Docker containers for code execution.

**Expected outcome:** A sandbox manager that can create ephemeral Docker containers, execute commands, and manage lifecycle.

**Dependencies:** Plan 1.2

**Implementation steps:**

1. Create `sandbox/manager.py` with `SandboxManager` class
2. Implement methods:
   - `create(project_contract) -> Sandbox` — create a container based on stack definition
   - `destroy(sandbox_id)` — tear down container
   - `list_active() -> list[Sandbox]`
3. `Sandbox` object with methods:
   - `run_command(cmd) -> CommandResult`
   - `read_file(path) -> str`
   - `write_file(path, content) -> None`
   - `copy_repo(repo_path) -> None`
4. Create `sandbox/config.py` with sandbox configuration (image mappings, resource limits, timeouts)
5. Map stack definitions to Docker base images:
   - Python stacks → `python:3.12-slim`
   - Node stacks → `node:20-slim`
   - Go stacks → `golang:1.22-alpine`
   - Java stacks → `eclipse-temurin:21-jdk`
   - Rust stacks → `rust:1.77-slim`
6. Write tests (mock Docker for unit tests)

**Files to create:**

```
sandbox/manager.py
sandbox/config.py
tests/test_sandbox.py
```

**Libraries to install:**

```
docker>=7.0
```

**Why Docker Python SDK:** The `docker` package is the official Python SDK for the Docker Engine API. It provides programmatic container management and is the most stable way to control Docker from Python.

**Testing strategy:** Unit tests with mocked Docker client. Test container creation with correct image. Test command execution. Test cleanup.

**Completion criteria:**
- Sandbox manager creates containers from stack definitions
- Commands execute inside containers
- Containers are cleaned up on destruction
- All tests pass (with mocked Docker)

---

### Plan 7.2 — Implement OpenHands Runtime Wrapper

**Purpose:** Integrate OpenHands as the agent runtime for sandboxed code execution.

**Expected outcome:** A runtime wrapper that connects agents to OpenHands for executing code, running tests, and interacting with the filesystem inside sandboxes.

**Dependencies:** Plan 7.1, Plan 6.1

**Implementation steps:**

1. Create `sandbox/runtime.py` with `OpenHandsRuntime` class
2. Implement the agent-runtime bridge:
   - `initialize(sandbox, task) -> RuntimeSession`
   - `execute_action(action) -> ActionResult`
   - `get_observation() -> Observation`
3. Map agent tool calls to OpenHands actions:
   - `read_file` → OpenHands file read
   - `write_file` → OpenHands file write
   - `run_command` → OpenHands shell command
   - `search` → file search in sandbox
4. Handle runtime errors, timeouts, and recovery
5. Expose the runtime through the `AgentContext` so agents interact with it transparently
6. Write tests with mocked OpenHands runtime

**Files to create:**

```
sandbox/runtime.py
tests/test_runtime.py
```

**Libraries to install:**

```
openhands-ai>=0.25
```

**Why OpenHands:** OpenHands is the required agent runtime per project specification. It provides sandboxed execution, tool use, and agent-environment interaction out of the box.

**Testing strategy:** Unit tests with mocked OpenHands. Test action execution, file I/O, and error handling.

**Completion criteria:**
- Runtime wrapper translates agent tools to OpenHands actions
- File I/O works through the runtime
- Command execution works through the runtime
- Errors are handled gracefully
- All tests pass

---

### Plan 7.3 — Wire Sandbox into Agent Execution

**Purpose:** Connect the sandbox system to the agent execution pipeline so agents execute code inside containers.

**Expected outcome:** When the orchestrator runs agents, they automatically execute inside sandboxed containers via the OpenHands runtime.

**Dependencies:** Plan 7.1, Plan 7.2, Plan 6.4

**Implementation steps:**

1. Update `agents/base.py` to accept an `OpenHandsRuntime` in `AgentContext`
2. Update agent tool methods (`read_file`, `write_file`, `run_command`) to route through the runtime
3. Update `agents/dispatcher.py` to create a sandbox per task (or per project)
4. Update `orchestrator/nodes.py` execute node to provision sandbox before agent execution
5. Ensure sandbox cleanup after task completion
6. Write integration test that runs a simple task through sandbox

**Files to modify:**

```
agents/base.py
agents/dispatcher.py
orchestrator/nodes.py
```

**Files to create:**

```
tests/test_sandbox_integration.py
```

**Testing strategy:** Integration test (with mocked Docker/OpenHands) verifying end-to-end agent execution through sandbox.

**Completion criteria:**
- Agents execute tools through the sandbox runtime
- Sandbox is provisioned and destroyed automatically
- Integration test passes

---

## Milestone 8 — Multi-Agent Orchestration

### Plan 8.1 — Implement Parallel Task Dispatch

**Purpose:** Enable the dispatcher to execute independent tasks in parallel.

**Expected outcome:** Tasks without mutual dependencies execute concurrently, reducing total execution time.

**Dependencies:** Plan 6.3

**Implementation steps:**

1. Update `agents/dispatcher.py` to support parallel execution:
   - Identify tasks with no unresolved dependencies (ready set)
   - Launch ready tasks concurrently using `asyncio.gather`
   - When a task completes, re-evaluate which tasks become ready
   - Continue until all tasks completed or failed
2. Add concurrency limit configuration (max parallel agents)
3. Handle partial failures: if one parallel task fails, others continue
4. Update execution memory to log parallel execution
5. Write tests verifying parallel scheduling

**Files to modify:**

```
agents/dispatcher.py
```

**Files to create:**

```
tests/test_parallel_dispatch.py
```

**Testing strategy:** Test with a task graph containing parallel branches. Verify independent tasks run concurrently. Verify dependent tasks wait. Test partial failure handling.

**Completion criteria:**
- Independent tasks execute in parallel
- Dependency ordering is preserved
- Partial failures don't block independent tasks
- Concurrency limit is respected
- All tests pass

---

### Plan 8.2 — Implement Review-Revise Cycle

**Purpose:** Build the review-revise loop where reviewer feedback triggers engineer revisions.

**Expected outcome:** After code generation, the reviewer checks output. If issues are found, the engineer revises. This repeats until approved or max revisions reached.

**Dependencies:** Plan 6.4, Plan 6.5

**Implementation steps:**

1. Update `orchestrator/nodes.py` review node:
   - After engineer produces changes, reviewer evaluates
   - If reviewer approves → proceed to completion
   - If reviewer requests changes → send feedback to engineer for revision
   - Maximum revision cycles: configurable (default: 3)
2. Track revision history in execution memory
3. Each revision includes the reviewer's feedback as context for the engineer
4. Wire QA agent into the cycle: tests must pass before review
5. Write tests for the review-revise loop

**Files to modify:**

```
orchestrator/nodes.py
orchestrator/graph.py  (add review-revise edge)
```

**Files to create:**

```
tests/test_review_cycle.py
```

**Testing strategy:** Test that review rejection triggers revision. Test max revision limit. Test successful approval flow.

**Completion criteria:**
- Review rejection triggers engineer revision with feedback
- Max revision limit prevents infinite loops
- Successful approval proceeds to completion
- QA validation runs before review
- All tests pass

---

### Plan 8.3 — Implement MCP Tool Registry

**Purpose:** Build the MCP-compatible tool registry that agents use to discover and invoke tools.

**Expected outcome:** A tool registry that exposes file operations, shell commands, search, and git operations as MCP-compatible tools.

**Dependencies:** Plan 6.1, Plan 7.2

**Implementation steps:**

1. Create `tools/mcp_registry.py` with `ToolRegistry` class
2. Define standard tools following MCP tool protocol:
   - `read_file(path) -> content`
   - `write_file(path, content) -> success`
   - `search(query, path) -> results`
   - `run_command(cmd) -> output`
   - `git_diff() -> diff`
   - `git_commit(message) -> commit_hash`
   - `find_symbol(name) -> locations`
3. Create `tools/file_ops.py` — file operation implementations
4. Create `tools/shell.py` — shell command implementations
5. Create `tools/git_ops.py` — git operation implementations
6. Tools route through the sandbox runtime when available
7. Write tests for each tool

**Files to create:**

```
tools/mcp_registry.py
tools/file_ops.py
tools/shell.py
tools/git_ops.py
tests/test_tools.py
```

**Testing strategy:** Unit tests for each tool with mocked runtime. Test tool discovery. Test error handling.

**Completion criteria:**
- All standard tools are registered and invocable
- Tools route through sandbox when available
- Tool schema follows MCP conventions
- All tests pass

---

### Plan 8.4 — End-to-End Integration Test

**Purpose:** Create a comprehensive integration test that validates the entire pipeline from requirement to code output.

**Expected outcome:** A test that submits a requirement, runs it through clarification, planning, dispatch, execution, review, and produces valid code changes.

**Dependencies:** All plans in Milestones 1-8

**Implementation steps:**

1. Create a comprehensive integration test scenario:
   - Input: "Add a health check endpoint to the API"
   - Contract: a sample FastAPI project
   - Expected flow: plan → dispatch → engineer writes endpoint → QA runs tests → reviewer approves
2. Use mocked LLM responses for deterministic test behavior
3. Use mocked sandbox for test portability
4. Verify:
   - Correct nodes visited in correct order
   - Task graph created with appropriate tasks
   - Agent output contains expected file changes
   - Policy validation passes
   - Execution log records all events
5. This test validates the entire system architecture

**Files to create:**

```
tests/test_e2e_integration.py
tests/fixtures/sample_fastapi_project/
tests/fixtures/sample_fastapi_project/main.py
tests/fixtures/sample_fastapi_project/requirements.txt
tests/fixtures/sample_contract_fastapi.yaml
```

**Testing strategy:** Full pipeline integration test with mocked external dependencies (LLM, Docker, OpenHands).

**Completion criteria:**
- End-to-end test passes
- All pipeline stages execute correctly
- Execution log is complete and accurate
- Test is deterministic and reproducible

---

## Milestone 9 — Integration Wrapper

### Plan 9.1 — Refine CLI Integration

**Purpose:** Polish the CLI interface with rich output, progress tracking, and interactive clarification.

**Expected outcome:** A production-quality CLI that shows real-time progress, supports interactive Q&A, and displays results clearly.

**Dependencies:** Plan 2.3, Plan 3.3

**Implementation steps:**

1. Update `integrations/inbound/cli.py`:
   - Rich-formatted requirement input with syntax highlighting
   - Interactive clarification Q&A with numbered questions
   - Progress spinner during execution
   - Ability to view execution log during run
2. Update `integrations/outbound/console.py`:
   - Rich-formatted task graph display
   - Real-time node transition updates
   - Color-coded status indicators
   - Final summary with file changes, test results, review outcome
3. Add `--verbose`, `--dry-run`, and `--contract` CLI flags
4. Write tests for CLI formatting

**Files to modify:**

```
main.py
integrations/inbound/cli.py
integrations/outbound/console.py
```

**Files to create:**

```
tests/test_cli_formatting.py
```

**Testing strategy:** Test CLI output rendering. Test flag handling. Test interactive mode.

**Completion criteria:**
- CLI shows real-time progress
- Interactive clarification works smoothly
- Output is well-formatted and informative
- All tests pass

---

### Plan 9.2 — Build Abstract Integration Layer

**Purpose:** Formalize the integration abstraction so future GitHub, Jira, and Slack integrations are drop-in.

**Expected outcome:** A clean integration layer with abstract interfaces, event routing, and adapter registration.

**Dependencies:** Plan 1.4

**Implementation steps:**

1. Update `integrations/base.py` with finalized interfaces:
   - `InboundIntegration`: `receive()`, `send_clarification()`, `report_status()`
   - `OutboundIntegration`: `notify()`, `create_pull_request()`, `update_status()`
   - `IntegrationEvent`: standardized event envelope
2. Create `integrations/router.py` with `IntegrationRouter`:
   - Register inbound/outbound integrations
   - Route events to appropriate handlers
   - Support multiple simultaneous integrations
3. Create integration stubs (not implemented, just interface):
   - `integrations/inbound/github_webhook.py` — stub
   - `integrations/inbound/jira_webhook.py` — stub
   - `integrations/outbound/github_api.py` — stub
   - `integrations/outbound/slack_api.py` — stub
4. Write tests for the router and event handling

**Files to create:**

```
integrations/router.py
integrations/inbound/github_webhook.py
integrations/inbound/jira_webhook.py
integrations/outbound/github_api.py
integrations/outbound/slack_api.py
tests/test_integration_router.py
```

**Files to modify:**

```
integrations/base.py
```

**Testing strategy:** Test router registration and event dispatch. Test that stubs raise `NotImplementedError`. Test CLI integration works through router.

**Completion criteria:**
- Integration router correctly dispatches events
- All interfaces are clearly defined
- Stubs exist for future integrations
- CLI works through the router
- All tests pass

---

### Plan 9.3 — Implement Git Operations for PR Creation

**Purpose:** Build the git operations layer that commits changes and creates pull requests (locally and via GitHub API stub).

**Expected outcome:** After agents complete work, changes are committed to a branch and a PR description is generated. Actual GitHub PR creation is stubbed for future implementation.

**Dependencies:** Plan 8.3

**Implementation steps:**

1. Update `tools/git_ops.py` with full git workflow:
   - `create_branch(name)` — create feature branch
   - `stage_changes(files)` — git add specific files
   - `commit(message)` — commit with structured message
   - `generate_pr_description(task_graph, changes)` — LLM-generated PR description
   - `push(remote, branch)` — push to remote (disabled by default, requires explicit opt-in)
2. Generate structured commit messages from task descriptions
3. Generate PR descriptions from the task graph and execution summary
4. Wire git operations into the `complete` node in `orchestrator/nodes.py`
5. Write tests for git operations (using temporary git repos)

**Files to modify:**

```
tools/git_ops.py
orchestrator/nodes.py  (update complete node)
```

**Files to create:**

```
tests/test_git_ops.py
```

**Testing strategy:** Test git operations against temporary repositories. Test branch creation, commit, PR description generation.

**Completion criteria:**
- Feature branch created automatically
- Changes committed with structured messages
- PR description generated from execution context
- Push requires explicit opt-in
- All tests pass

---

## Milestone 10 — Autonomous Engineering

### Plan 10.1 — Implement Self-Healing and Retry Logic

**Purpose:** Build robust error handling that allows the system to recover from common failures automatically.

**Expected outcome:** When agents fail (test failures, lint errors, runtime errors), the system automatically diagnoses and retries with corrective action.

**Dependencies:** Plan 8.2

**Implementation steps:**

1. Create a failure taxonomy in `orchestrator/failures.py`:
   - `TestFailure` — tests didn't pass → re-run engineer with test output
   - `LintFailure` — lint errors → auto-fix or re-run with lint feedback
   - `RuntimeError` — execution error → analyze stack trace and retry
   - `TimeoutError` — execution took too long → simplify approach or split task
   - `PolicyViolation` — policy check failed → re-run with violation details
2. Update `agents/dispatcher.py` with smart retry:
   - Classify failure type
   - Inject failure context into retry attempt
   - Maximum retries per failure type (configurable)
3. Update execution memory to track failure patterns
4. Write tests for each failure type and retry behavior

**Files to create:**

```
orchestrator/failures.py
tests/test_self_healing.py
```

**Files to modify:**

```
agents/dispatcher.py
```

**Testing strategy:** Simulate each failure type and verify correct retry behavior. Test max retry limits. Test failure context injection.

**Completion criteria:**
- Each failure type triggers appropriate recovery strategy
- Retry context helps agents fix issues
- Max retries prevent infinite loops
- Failure patterns are logged
- All tests pass

---

### Plan 10.2 — Implement Full Autonomous Workflow

**Purpose:** Wire everything together into a single autonomous flow: requirement → clarification → plan → execute → test → review → commit → PR.

**Expected outcome:** Running `python main.py "Build a REST API with user authentication"` executes the entire autonomous workflow end-to-end.

**Dependencies:** All previous plans

**Implementation steps:**

1. Update `orchestrator/graph.py` with the complete state machine including all edges and conditional routing
2. Update `orchestrator/nodes.py` with production implementations for all nodes
3. Update `main.py` with the complete CLI flow:
   - Accept requirement
   - Optionally specify project contract
   - Run autonomous workflow
   - Display real-time progress
   - Show final results (changes, tests, PR description)
4. Create a demonstration contract and stack definition for testing
5. Write a comprehensive walkthrough test
6. Update README.md with usage instructions

**Files to modify:**

```
main.py
orchestrator/graph.py
orchestrator/nodes.py
```

**Files to create:**

```
tests/test_autonomous_workflow.py
contracts/demo_project.yaml
```

**Testing strategy:** Full walkthrough test with mocked external dependencies. Verify every pipeline stage executes correctly.

**Completion criteria:**
- Complete autonomous workflow runs end-to-end
- Clarification loop works interactively
- Tasks are planned, dispatched, and executed
- Code changes are reviewed and committed
- PR description is generated
- All tests pass

---

### Plan 10.3 — Production Hardening

**Purpose:** Add production-quality logging, error reporting, graceful shutdown, and operational tooling.

**Expected outcome:** The system is robust enough for daily use with clear observability and error handling.

**Dependencies:** Plan 10.2

**Implementation steps:**

1. Add structured logging throughout with configurable levels:
   - Use Python `logging` with JSON formatter
   - Log every state transition, agent action, and decision
2. Add graceful shutdown handling:
   - Signal handlers for SIGINT/SIGTERM
   - Clean up sandboxes on shutdown
   - Save execution state for potential resume
3. Add health check and status reporting:
   - Current state of the orchestrator
   - Active sandboxes
   - Task graph progress
4. Add execution summary generation:
   - Total duration
   - Tasks completed/failed
   - Files changed
   - Tests run/passed/failed
   - Policy violations found
5. Write operational documentation

**Files to create:**

```
orchestrator/logging.py
orchestrator/shutdown.py
orchestrator/health.py
orchestrator/summary.py
tests/test_operational.py
```

**Testing strategy:** Test logging output format. Test graceful shutdown cleanup. Test summary generation.

**Completion criteria:**
- Structured logging captures all significant events
- Graceful shutdown cleans up resources
- Execution summary provides clear overview
- All tests pass

---

### Plan 10.4 — Documentation and Developer Guide

**Purpose:** Create comprehensive documentation for the platform, including setup guide, architecture docs, and contributor guide.

**Expected outcome:** Anyone can set up, run, and extend the platform by reading the documentation.

**Dependencies:** Plan 10.2

**Implementation steps:**

1. Update `README.md` with:
   - Quick start guide
   - Installation instructions
   - Configuration reference
   - Usage examples
2. Create `docs/architecture.md` — detailed architecture documentation
3. Create `docs/agents.md` — how to create custom agents
4. Create `docs/deterministics.md` — how to create stack definitions, policies, and gates
5. Create `docs/contracts.md` — how to create project contracts
6. Create `docs/integrations.md` — how to add new integrations
7. Create `docs/contributing.md` — contributor guide

**Files to create:**

```
docs/architecture.md
docs/agents.md
docs/deterministics.md
docs/contracts.md
docs/integrations.md
docs/contributing.md
```

**Files to modify:**

```
README.md  (add quick start and usage sections)
```

**Testing strategy:** Review documentation for accuracy and completeness. Verify all code examples work.

**Completion criteria:**
- Setup guide enables new users to get running
- Architecture docs explain the system clearly
- Extension guides cover agents, deterministics, contracts, and integrations
- All code examples are accurate

---

## Dependency Graph

```
Plan 1.1 ──┬── Plan 1.2 ──┬── Plan 1.4 ──── Plan 2.3 ──── Plan 9.1
            │              │                      │
            ├── Plan 1.3 ──┤                      │
            │              │                      │
            │              └── Plan 2.1 ──────────┘
            │                    │
            │              Plan 2.2
            │
            ├── Plan 4.1 ──┬── Plan 4.2 ──┬── Plan 4.3
            │              │              │
            │              │              └── Plan 4.4
            │              │
            │              └── Plan 6.1 ──┬── Plan 6.2 ──── Plan 6.3 ──── Plan 8.1
            │                             │                      │
            │                             ├── Plan 6.4 ──────────┤
            │                             │                      │
            │                             └── Plan 6.5 ──────────┘
            │                                    │
            │                              Plan 8.2
            │
            ├── Plan 5.1 ───┐
            │               │
            ├── Plan 5.2 ──┬┤── Plan 5.3 ──┐
            │              │               │
            │              └── Plan 5.4 ───┤
            │                              │
            │                        Plan 5.5
            │
            └── Plan 7.1 ──── Plan 7.2 ──── Plan 7.3 ──── Plan 8.3
                                                              │
                                                         Plan 9.3

Plan 3.1 ──── Plan 3.2 ──── Plan 3.3

Plan 8.4 (depends on all Milestone 1-8 plans)
Plan 9.2 (depends on Plan 1.4)
Plan 10.1 (depends on Plan 8.2)
Plan 10.2 (depends on all previous plans)
Plan 10.3 (depends on Plan 10.2)
Plan 10.4 (depends on Plan 10.2)
```

---

## Summary

| Milestone | Plans | Focus |
|-----------|-------|-------|
| 1 | 1.1–1.4 | Project setup, config, schemas, CLI |
| 2 | 2.1–2.3 | LangGraph orchestrator, memory, wiring |
| 3 | 3.1–3.3 | Ambiguity analysis, confidence scoring, clarification loop |
| 4 | 4.1–4.4 | Deterministic loader, policy engine, gates, contracts |
| 5 | 5.1–5.5 | File search, AST parsing, symbol index, dep graph, indexer |
| 6 | 6.1–6.5 | Base agent, planner, dispatcher, engineer, QA, reviewer |
| 7 | 7.1–7.3 | Sandbox manager, OpenHands runtime, agent-sandbox wiring |
| 8 | 8.1–8.4 | Parallel dispatch, review cycle, MCP tools, E2E test |
| 9 | 9.1–9.3 | CLI polish, integration layer, git/PR operations |
| 10 | 10.1–10.4 | Self-healing, full workflow, hardening, documentation |

**Total plans: 35**

Each plan is designed to be implementable in a single focused coding session. Plans within a milestone can often be worked in parallel when their dependencies allow. The dependency graph above shows the exact ordering constraints.
