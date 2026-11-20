# 0008. The POC runs locally, without a cloud account or a paid LLM API

- **Status:** accepted
- **Date:** 2026-11-19
- **Design:** [DESIGN §12](../DESIGN.md#12-poc-vs-production)

## Context

The POC must be easy for an evaluator to run, and must not depend on credentials that the author or the evaluator may not have. The production design uses AWS managed services and Claude on Bedrock. The POC exists to show the behaviour of the design, not its infrastructure.

## Decision

- **Everything runs locally**: PostgreSQL + pgvector in Docker, a small multilingual embedding model on CPU, Cedar through `cedarpy`, a FastMCP server.
- **The agent is an existing MCP client** (Claude Code or Claude Desktop), connected with a user token. The POC does not ship its own chat agent.
- **LLM steps inside the pipeline are made reproducible**: procedures were written by Claude Code during development and stored as fixtures with the hash of the source version they came from; facts are extracted by a deterministic rule.
- **The observer's LLM is optional**: a local model through Ollama, the Anthropic API if a key is set, or a template fallback.

## Alternatives considered

- **Deploy on AWS.** Closer to production; needs an account, costs money, and is harder for an evaluator to reproduce.
- **Call an LLM API in the pipeline.** More realistic derivation; makes the demo depend on a key and makes results non-deterministic.
- **Mock every AWS service locally** (LocalStack). Adds setup weight without showing more of the design.

## Consequences

- `make setup` is the only step needed; tests and demos are deterministic.
- Derivation quality and scale are not demonstrated (DESIGN §12.3).
- Every substitution is listed in the README and in DESIGN §12.2.
