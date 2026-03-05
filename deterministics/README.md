# Deterministics Packs

This directory contains stack-specific deterministic packs.

Rules:

- Core runtime must remain stack-agnostic.
- Commands, gates, prompts, and repo hints are pack-defined.
- Each pack must validate against `pack.schema.yaml`.
