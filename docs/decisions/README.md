# Architecture Decision Records

One file per decision: context, decision, alternatives considered, consequences.

| ADR | Decision | Status |
|---|---|---|
| 0001 | Layered context-engineering model (Raw → Curated Assets → Context Repo) | planned |
| 0002 | Automatic updates for source-faithful content, eval-gated promotion for LLM-derived assets, circuit breakers | planned |
| 0003 | Change propagation: events + incremental polling + full reconciliation, tombstones | planned |
| 0004 | Authorization: on-behalf-of identity, Cedar policies, enforcement at retrieval | planned |
| 0005 | MCP as the agent access layer | planned |
| 0006 | Aurora PostgreSQL + pgvector as the asset store | planned |
| 0007 | Observer agent: deterministic detection, LLM diagnosis, allowlisted reversible actions | planned |
| 0008 | POC runs locally without a paid LLM API (Claude Code as agent, Ollama for diagnosis) | planned |
