# AI Orchestrator

An autonomous AI software engineering platform that operates as a complete AI development team — from requirement analysis through code delivery.

## Overview

AI Orchestrator is a platform that receives software engineering requirements, plans work, writes code, runs tests, reviews output, and delivers pull requests — autonomously. It is built on Python, OpenHands, LangGraph, and MCP-compatible tooling, with deterministic policies enforcing reliability at every stage.

The system is **technology-agnostic**: it works with any programming language, framework, repository layout, or operating system. Reliability comes not from hardcoded stack knowledge but from deterministic scaffolding — contracts, policies, stack definitions, and acceptance criteria loaded at runtime.

## Vision and Long-Term Goal

The platform evolves into a fully independent AI engineering organization capable of:

- Receiving requirements from any source (CLI, Jira, Slack, GitHub Issues)
- Running structured clarification loops to resolve ambiguity
- Decomposing work into task graphs with dependency ordering
- Dispatching tasks to specialized agents (backend, frontend, QA, security, docs)
- Executing code changes inside sandboxed containers via OpenHands
- Running tests, debugging failures, and iterating autonomously
- Reviewing code against policies and acceptance criteria
- Committing code, creating pull requests, and reporting results

## Architectural Philosophy

The system models an **AI engineering organization** with clear hierarchy and separation of concerns:

```
Orchestrator (LangGraph state machine)
│
├── Planner Agent (PM)
│   ├── Requirement analysis
│   ├── Ambiguity detection & clarification loop
│   ├── Task decomposition
│   └── Acceptance criteria generation
│
├── Dispatcher
│   ├── Task routing & scheduling
│   ├── Dependency resolution
│   └── Parallel execution management
│
└── Project Agents
    ├── Backend Engineer Agent
    ├── Frontend Engineer Agent
    ├── Infrastructure Engineer Agent
    ├── QA Engineer Agent
    ├── Security Reviewer Agent
    └── Documentation Agent
        │
        └── Subagents (per-task)
            ├── Code Writer
            ├── Test Runner
            ├── Debugger
            ├── Code Reviewer
            └── Dependency Analyzer
```

**Key principles:**

1. **Agents are general-purpose.** No agent contains hardcoded stack logic. All stack-specific behavior is loaded from deterministic configuration files.
2. **Reliability is structural.** Deterministic policies, contracts, and gates enforce correctness — not LLM hope.
3. **Execution is sandboxed.** All code execution happens in ephemeral Docker containers via OpenHands.
4. **State is explicit.** The LangGraph state machine owns all workflow state. No implicit state lives in agent memory.

## System Architecture

### High-Level Flow

```
Input (prompt / webhook / ticket)
    │
    ▼
┌─────────────────────────────────┐
│  Integration Wrapper Layer      │  (future: Jira, Slack, GitHub adapters)
│  Currently: CLI prompt input    │
└──────────────┬──────────────────┘
               │
               ▼
┌─────────────────────────────────┐
│  Orchestrator (LangGraph)       │
│  ┌───────────────────────────┐  │
│  │ State Machine             │  │
│  │ ┌─────────┐ ┌──────────┐ │  │
│  │ │ Planner │→│Dispatcher│ │  │
│  │ └─────────┘ └──────────┘ │  │
│  │       ↕           │      │  │
│  │ Clarification     ▼      │  │
│  │    Loop      ┌────────┐  │  │
│  │              │ Agents │  │  │
│  │              └────────┘  │  │
│  └───────────────────────────┘  │
│                                 │
│  ┌───────────────────────────┐  │
│  │ Execution Memory          │  │
│  │ Task Graph                │  │
│  │ Policy Engine             │  │
│  └───────────────────────────┘  │
└──────────────┬──────────────────┘
               │
               ▼
┌─────────────────────────────────┐
│  Sandbox Layer (OpenHands)      │
│  Docker containers              │
│  File I/O, shell, test runners  │
└──────────────┬──────────────────┘
               │
               ▼
┌─────────────────────────────────┐
│  Repository Intelligence        │
│  tree-sitter, ripgrep, LSP      │
│  File search, symbols, deps     │
└─────────────────────────────────┘
```

### Component Interaction

1. **Orchestrator** receives a requirement and delegates to the **Planner**.
2. **Planner** analyzes the requirement, detects ambiguity, and runs a **clarification loop** until confidence exceeds threshold.
3. **Planner** decomposes the clarified requirement into a **task graph** with dependencies, acceptance criteria, and agent assignments.
4. **Dispatcher** reads the task graph, resolves dependencies, and routes tasks to **Project Agents**.
5. **Project Agents** execute tasks inside **sandboxed containers** using OpenHands, guided by **deterministic policies** and **project contracts**.
6. **Repo Intelligence** provides agents with structural understanding of the codebase — file maps, symbol tables, dependency graphs.
7. **Policy Engine** validates agent output against coding standards, security rules, and acceptance criteria.
8. Results flow back through the Orchestrator, which decides next steps (retry, escalate, approve, merge).

## Agent Model

### Agent Types

| Agent | Responsibility |
|-------|---------------|
| Planner (PM) | Requirement analysis, clarification, task decomposition, acceptance criteria |
| Dispatcher | Task scheduling, dependency resolution, parallel execution |
| Backend Engineer | Server-side code, APIs, databases, business logic |
| Frontend Engineer | UI components, client-side logic, styling |
| Infrastructure Engineer | CI/CD, Docker, deployment configs, cloud resources |
| QA Engineer | Test writing, test execution, coverage analysis |
| Security Reviewer | Vulnerability scanning, OWASP checks, dependency audits |
| Documentation Agent | README updates, API docs, inline documentation |

### Agent Lifecycle

1. Agent receives a task from the Dispatcher
2. Agent loads relevant **project contract** and **deterministic policies**
3. Agent queries **Repo Intelligence** for codebase context
4. Agent executes work inside a **sandbox** via OpenHands
5. Agent runs validation against **acceptance criteria**
6. Agent reports results back to the Dispatcher

### Subagent Spawning

Project agents may spawn short-lived subagents for specific subtasks:

- **Test Runner**: execute test suites, parse results
- **Debugger**: analyze failures, propose fixes
- **Code Reviewer**: check output against policies
- **Dependency Analyzer**: audit imports, detect conflicts

Subagents inherit the parent agent's sandbox and context.

## Deterministic Scaffolding Strategy

Reliability is enforced through deterministic components, not LLM reasoning.

### Stack Definitions

Stack definitions describe technology-specific conventions:

```yaml
# deterministics/stacks/react-nextjs.yaml
name: react-nextjs
language: typescript
framework: nextjs
package_manager: npm
test_runner: jest
build_command: "npm run build"
dev_command: "npm run dev"
source_dirs: ["src/", "app/", "pages/"]
test_dirs: ["__tests__/", "*.test.ts", "*.test.tsx"]
config_files: ["next.config.js", "tsconfig.json", "package.json"]
conventions:
  component_style: functional
  state_management: server-components-first
  styling: tailwindcss
```

Agents read these files dynamically. There is **no** `if language == "react"` anywhere in the codebase.

### Policies

Policies define enforceable rules:

```yaml
# deterministics/policies/coding_standards.yaml
rules:
  - id: no-console-log
    severity: warning
    description: "Remove console.log statements before commit"
  - id: test-coverage-minimum
    severity: error
    threshold: 80
    description: "Test coverage must exceed 80%"
  - id: no-hardcoded-secrets
    severity: critical
    description: "No API keys, passwords, or tokens in source code"
```

### Gates

Gates are checkpoints that block progression:

```yaml
# deterministics/gates/pre-commit.yaml
checks:
  - name: tests_pass
    required: true
    command: "{{stack.test_runner}}"
  - name: lint_clean
    required: true
    command: "{{stack.lint_command}}"
  - name: security_scan
    required: false
    command: "{{stack.security_scanner}}"
```

## Project Contract Model

Each project managed by the system has a **project contract** — a YAML file that defines the project's structure, constraints, and expectations.

```yaml
# contracts/project_contract.yaml
project:
  name: "example-app"
  repository: "https://github.com/org/example-app"
  stack: "react-nextjs"                    # references deterministics/stacks/
  policies:
    - "coding_standards"                   # references deterministics/policies/
    - "security_baseline"
  gates:
    - "pre-commit"                         # references deterministics/gates/
  acceptance_criteria:
    - "All existing tests must continue to pass"
    - "New code must have test coverage above 80%"
    - "No new security vulnerabilities introduced"
  constraints:
    max_file_changes: 20
    restricted_paths: ["config/production/", ".env"]
    required_reviewers: ["security"]
```

The contract is the **single source of truth** for how agents interact with a project.

## Repository Intelligence System

The Repo Intelligence layer gives agents structural understanding of any codebase.

### Capabilities

| Capability | Technology | Description |
|-----------|-----------|-------------|
| File search | ripgrep | Fast full-text search across the repo |
| Code navigation | tree-sitter | AST-based symbol extraction and navigation |
| Symbol resolution | tree-sitter grammars | Find definitions, references, call sites |
| Dependency graph | Custom + tree-sitter | Map imports and module dependencies |
| Repository map | Custom indexer | Generate structural overview of the codebase |
| Change impact | Git + dep graph | Predict which files/tests are affected by changes |

### Architecture

```
repo_intelligence/
├── indexer.py              # Orchestrates indexing pipeline
├── file_search.py          # ripgrep wrapper for fast search
├── ast_parser.py           # tree-sitter parsing and symbol extraction
├── symbol_index.py         # Symbol table: definitions, references, types
├── dependency_graph.py     # Import/module dependency mapping
├── repo_map.py             # High-level repo structure summary
├── change_impact.py        # Predict affected files from a changeset
└── grammars/               # tree-sitter grammar files per language
```

The indexer runs once when a project is loaded and incrementally updates as agents make changes.

## Container Sandbox Model

All code execution occurs in sandboxed environments via OpenHands.

### Design

- Each project gets an **ephemeral Docker container**
- Containers are provisioned from **stack-specific base images** (e.g., `node:20`, `python:3.12`)
- The project repository is mounted into the container
- Agents execute commands through the OpenHands runtime API
- Containers are destroyed after task completion (or persisted for debugging)

### Capabilities

- Install dependencies (`npm install`, `pip install`, etc.)
- Run tests (`pytest`, `jest`, `go test`, etc.)
- Execute builds (`npm run build`, `cargo build`, etc.)
- Run linters and formatters
- Execute arbitrary shell commands safely

### Isolation

- No network access to production systems
- No access to host filesystem outside the mounted repo
- Resource limits (CPU, memory, disk) enforced per container
- Execution timeouts prevent runaway processes

## Integration Wrapper Architecture

The integration layer is designed but **not implemented** in early milestones. The architecture ensures future integration is straightforward.

### Design

```
integrations/
├── base.py                 # Abstract integration interface
├── inbound/                # Event receivers
│   ├── cli.py              # CLI prompt input (implemented first)
│   ├── github_webhook.py   # GitHub webhook receiver (future)
│   ├── jira_webhook.py     # Jira webhook receiver (future)
│   └── slack_events.py     # Slack event receiver (future)
├── outbound/               # Notification senders
│   ├── console.py          # Console output (implemented first)
│   ├── github_api.py       # GitHub PR/comment creation (future)
│   ├── jira_api.py         # Jira status updates (future)
│   └── slack_api.py        # Slack message posting (future)
└── auth/                   # Authentication management
    └── credential_store.py # Secure credential storage
```

### Interface Contract

All inbound integrations implement:

```python
class InboundIntegration(ABC):
    async def receive(self) -> Requirement: ...
    async def send_clarification(self, question: str) -> str: ...
    async def report_status(self, status: Status) -> None: ...
```

All outbound integrations implement:

```python
class OutboundIntegration(ABC):
    async def notify(self, event: Event) -> None: ...
    async def create_pull_request(self, pr: PullRequest) -> str: ...
    async def update_status(self, task_id: str, status: Status) -> None: ...
```

The CLI integration is the only one implemented initially. The interface contract ensures all future integrations are drop-in replacements.

## Technology Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Language | Python 3.12+ | Primary implementation language |
| Orchestration | LangGraph | State machine, workflow orchestration, agent coordination |
| Agent Runtime | OpenHands | Sandboxed code execution, tool use, agent runtime |
| LLM Provider | Anthropic Claude / configurable | Agent reasoning engine |
| Code Parsing | tree-sitter | AST parsing, symbol extraction |
| File Search | ripgrep | Fast full-text code search |
| Containerization | Docker | Sandboxed execution environments |
| Configuration | YAML | Contracts, policies, stack definitions |
| Tool Protocol | MCP | Standardized tool interfaces for agents |
| Testing | pytest | Platform test suite |
| Schema Validation | Pydantic | Data validation and serialization |

### Additional Libraries (as needed)

| Library | Purpose | Justification |
|---------|---------|---------------|
| pydantic | Schema validation | Industry standard for Python data models |
| pyyaml | YAML parsing | Required for deterministic config files |
| docker (Python SDK) | Container management | Programmatic Docker control |
| aiohttp / httpx | HTTP client | Async HTTP for integrations and API calls |
| rich | CLI output | Structured terminal output and progress display |

## Security Considerations

- **Sandbox isolation**: All agent-executed code runs in Docker containers with no host access
- **Credential management**: API keys and tokens stored in a dedicated credential store, never in contracts or policies
- **Policy enforcement**: Security policies are checked as mandatory gates before any code is committed
- **Input validation**: All external input (requirements, webhook payloads) validated through Pydantic schemas
- **Audit trail**: Every agent action, decision, and state transition is logged
- **Restricted paths**: Project contracts can mark paths as off-limits to agents
- **Network isolation**: Sandbox containers have restricted network access

## Scalability Strategy

- **Horizontal agent scaling**: Multiple agents can execute in parallel across different containers
- **Task-level parallelism**: Independent tasks in the task graph execute concurrently
- **Incremental indexing**: Repo intelligence updates incrementally, not from scratch
- **Stateless agents**: Agents read state from the orchestrator; they hold no persistent state themselves
- **Queue-based dispatch**: The dispatcher can be backed by a task queue for high-throughput scenarios
- **Pluggable LLM backends**: Different agents can use different models based on task complexity

## Future Evolution

1. **Multi-project orchestration**: Manage changes across multiple related repositories
2. **Learning from feedback**: Incorporate PR review feedback to improve future output
3. **Custom agent creation**: Users define domain-specific agents via configuration
4. **Cost optimization**: Route simple tasks to cheaper/faster models
5. **Streaming progress**: Real-time visibility into agent work via WebSocket
6. **Self-improvement**: The system analyzes its own failure patterns and adjusts policies

## Repository Structure

```
ai-orchestrator/
├── README.md
├── PLAN.md
├── pyproject.toml
├── requirements.txt
│
├── orchestrator/
│   ├── __init__.py
│   ├── graph.py                    # LangGraph state machine definition
│   ├── state.py                    # Orchestrator state schema
│   ├── nodes.py                    # LangGraph node implementations
│   └── config.py                   # Orchestrator configuration
│
├── agents/
│   ├── __init__.py
│   ├── base.py                     # Base agent class
│   ├── planner.py                  # Planner / PM agent
│   ├── dispatcher.py               # Task dispatcher
│   ├── engineer.py                 # General engineering agent
│   ├── reviewer.py                 # Code review agent
│   ├── qa.py                       # QA / test agent
│   └── prompts/
│       ├── planner_system.md
│       ├── engineer_system.md
│       ├── reviewer_system.md
│       └── qa_system.md
│
├── clarification/
│   ├── __init__.py
│   ├── analyzer.py                 # Requirement ambiguity analysis
│   ├── confidence.py               # Confidence scoring
│   ├── loop.py                     # Clarification loop state machine
│   └── schemas.py                  # Clarification data models
│
├── contracts/
│   ├── __init__.py
│   ├── loader.py                   # Contract loading and validation
│   ├── validator.py                # Contract compliance checker
│   └── schemas.py                  # Contract Pydantic models
│
├── deterministics/
│   ├── __init__.py
│   ├── loader.py                   # Dynamic loader for all deterministic files
│   ├── policy_engine.py            # Policy evaluation engine
│   ├── gate_runner.py              # Gate check execution
│   ├── stacks/                     # Stack definition YAML files
│   │   ├── react-nextjs.yaml
│   │   ├── python-fastapi.yaml
│   │   ├── node-nestjs.yaml
│   │   └── _template.yaml
│   ├── policies/                   # Policy YAML files
│   │   ├── coding_standards.yaml
│   │   ├── security_baseline.yaml
│   │   └── _template.yaml
│   └── gates/                      # Gate YAML files
│       ├── pre_commit.yaml
│       ├── pre_merge.yaml
│       └── _template.yaml
│
├── repo_intelligence/
│   ├── __init__.py
│   ├── indexer.py                  # Indexing pipeline orchestrator
│   ├── file_search.py              # ripgrep integration
│   ├── ast_parser.py               # tree-sitter parsing
│   ├── symbol_index.py             # Symbol table management
│   ├── dependency_graph.py         # Import/dependency mapping
│   ├── repo_map.py                 # Structural repo overview
│   └── change_impact.py            # Change impact prediction
│
├── sandbox/
│   ├── __init__.py
│   ├── manager.py                  # Sandbox lifecycle management
│   ├── runtime.py                  # OpenHands runtime wrapper
│   └── config.py                   # Sandbox configuration
│
├── integrations/
│   ├── __init__.py
│   ├── base.py                     # Abstract integration interfaces
│   ├── inbound/
│   │   ├── __init__.py
│   │   └── cli.py                  # CLI prompt input
│   └── outbound/
│       ├── __init__.py
│       └── console.py              # Console output
│
├── memory/
│   ├── __init__.py
│   ├── execution_log.py            # Execution history and audit trail
│   ├── task_graph.py               # Task graph state management
│   └── store.py                    # Persistent memory store
│
├── tools/
│   ├── __init__.py
│   ├── mcp_registry.py             # MCP tool registry
│   ├── file_ops.py                 # File operation tools
│   ├── shell.py                    # Shell execution tools
│   └── git_ops.py                  # Git operation tools
│
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── test_orchestrator.py
    ├── test_planner.py
    ├── test_dispatcher.py
    ├── test_clarification.py
    ├── test_contracts.py
    ├── test_deterministics.py
    ├── test_repo_intelligence.py
    ├── test_sandbox.py
    └── fixtures/
        ├── sample_contract.yaml
        ├── sample_stack.yaml
        └── sample_repo/
```
