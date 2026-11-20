# 0005. MCP as the agent access layer, returning evidence rather than answers

- **Status:** accepted
- **Date:** 2026-11-19
- **Design:** [DESIGN §6.1, §6.2](../DESIGN.md#6-agent-access)

## Context

Many teams will build agents, with different frameworks and different models. The knowledge layer must be reachable by all of them without an adapter each, and must not decide how they reason.

## Decision

Expose each Context Repo through an **MCP server** with a small tool surface (`search_knowledge`, `get_asset`, live tools such as `get_leave_balance`, `context_info`). The server returns **evidence, not answers**: each result carries the text marked as `untrusted_source_data`, a citation, validity dates, freshness, conflicts with other sources and notices of future versions. The manifest of the Context Repo lists which tools an agent may call.

In production the server runs on AgentCore Runtime behind AgentCore Gateway, which adds tool-level policies.

## Alternatives considered

- **Custom REST API.** Works everywhere, but each agent framework needs its own adapter.
- **Framework-specific integration** (for example action groups of one agent product). Ties the knowledge layer to one way of building agents.
- **An `ask()` tool that returns a finished answer.** Convenient for simple clients; hides the evidence, doubles LLM cost, and puts reasoning in two places. Can be added on top later for very simple clients.

## Consequences

- Any MCP client works out of the box (Claude Code, Claude Desktop, agent SDKs).
- Each client needs its own LLM and prompt; the Context Repo ships recommended instructions with it.
- The response contract is versioned with the Context Repo, and is what the evals check.
- **In the POC:** `src/kb/mcp_server/server.py` (FastMCP, HTTP or stdio), `context_repos/hr-assistant/prompts/system.md`; connection instructions in the README.
