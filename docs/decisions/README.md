# Architecture Decision Records

One file per decision: context, decision, alternatives considered, consequences, and where the POC implements it.

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-layered-context-model.md) | Layered context model: Raw snapshot → Curated assets → Context Repo | accepted |
| [0002](0002-update-policy-and-release-gate.md) | Automatic updates for source-faithful content, eval-gated releases, owner approval for derived confidential content, circuit breakers | accepted |
| [0003](0003-change-propagation.md) | Change propagation: events + incremental polling + full reconciliation, tombstones | accepted |
| [0004](0004-authorization.md) | Authorization: on-behalf-of identity, Cedar policies, enforcement at retrieval time | accepted |
| [0005](0005-mcp-access-layer.md) | MCP as the agent access layer, returning evidence rather than answers | accepted |
| [0006](0006-asset-store.md) | Aurora PostgreSQL + pgvector as the asset store | accepted |
| [0007](0007-observer-agent.md) | Observer agent: deterministic detection, LLM diagnosis, allowlisted reversible actions | accepted |
| [0008](0008-poc-without-paid-llm.md) | The POC runs locally, without a cloud account or a paid LLM API | accepted |
