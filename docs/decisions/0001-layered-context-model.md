# 0001. Layered context model: Raw snapshot → Curated assets → Context Repo

- **Status:** accepted
- **Date:** 2026-11-19
- **Design:** [DESIGN §1, §3](../DESIGN.md#1-architecture)

## Context

Agents need company knowledge that is accurate, current and authorized. The usual setup is a single index: documents are chunked, embedded and searched. It mixes three different things in one place: what the source said (evidence), what we made of it (interpretation), and what a given agent should see (selection). When an answer is wrong, a single index cannot say which of the three failed, and cannot undo just that part.

## Decision

Separate five layers, each with one job:

1. **Sources**: owned by departments, never modified by us.
2. **Raw snapshot**: immutable, versioned copy of each source record with its raw permissions. It is the evidence and the replay point.
3. **Curated assets**: typed units (Knowledge, Procedure, Tool, Example, Memory) derived from the snapshot, each with owner, access attributes, validity, version and **provenance** (the raw versions it came from).
4. **Context Repo**: per agent workflow, a manifest that selects assets by scope, lists tools and instructions, and is released as an immutable set of pinned asset versions. Releases can be diffed and rolled back.
5. **Access layer**: the MCP server, which serves a Context Repo release on behalf of a user.

## Alternatives considered

- **Single index, re-built on change.** Simplest, and enough for a demo. No history, no provenance, no per-agent rollback, no way to tell a parsing error from a source error.
- **Managed knowledge base product** (Bedrock Knowledge Bases, Amazon Quick). Covers ingestion and retrieval well; versioning, derived assets, per-workflow releases and eval-gated promotion are outside its model (DESIGN §6).
- **Knowledge graph as the core.** Powerful for multi-hop questions, expensive to maintain, and not what the first agents need (DESIGN §3).

## Consequences

- Every answer can be traced back: asset version → raw version → source record.
- Rollback is cheap at every level: a release, an asset, or a whole source (DESIGN §5).
- Re-processing is possible without touching sources: change a parser or an embedding model, rebuild assets from the snapshot.
- More storage and more moving parts than a single index. Acceptable because each layer is simple on its own.
- **In the POC:** `raw_versions`, `asset_versions` (with `provenance`), `releases` + `release_assets`, `context_repos/hr-assistant/manifest.yaml`.
