# Deterministics Pack Guide

## Purpose

Deterministic packs isolate stack-specific behavior from core orchestration.

Core runtime loads pack metadata and executes policy without embedding framework logic.

## Generic Pack Contract

Required files:

- `pack.yaml`
- `commands.yaml`
- `gates.yaml`
- `repoHints.yaml`
- `prompts/*.md`
- `schemas/*.json`

Required top-level fields in `pack.yaml`:

- `apiVersion`
- `name`
- `displayName`
- `targets`
- `entrypoints`
- `toolPolicyProfile`
- `artifacts`

## Pack Responsibilities

- Define bootstrap/build/test/lint commands.
- Define pass/fail gates and retry budgets.
- Define repository path hints and technology fingerprints.
- Provide role prompt templates and schema overrides.

## First Concrete Pack

`deterministics/react-nest-mongo/` is the initial implementation for:

- React frontend
- NestJS backend
- MongoDB persistence

All language/framework specifics must remain inside this pack.
