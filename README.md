# Agent Knowledge Base

A knowledge base system that gives AI agents access to company knowledge that is **accurate, current and authorized** at the moment they act.

Built as the take-home assignment for the AI Engineer role at ShopFully & Flipp.

> **Status:** work in progress.

## Deliverables

| # | Deliverable | Where |
|---|---|---|
| 1 | Production-grade system design (AWS) | [`docs/DESIGN.md`](docs/DESIGN.md) (PDF export: `make pdf`) |
| 2 | Proof of Concept: HR assistant for a multi-country company | this repository |
| — | How AI was used across the SDLC | [`docs/AI-SDLC.md`](docs/AI-SDLC.md) |
| — | Architecture Decision Records | [`docs/decisions/`](docs/decisions/) |

## The idea in one paragraph

Department sources are synced into an immutable, versioned **raw snapshot**. From it the system derives typed **curated assets** (knowledge, procedures, tools, examples), each carrying owner, access policy, version and provenance. Each agent workflow consumes a versioned **Context Repo**: a manifest of the assets, tools and instructions it needs, which can be diffed and rolled back. Agents reach it through an **MCP server** that acts on behalf of the end user and enforces **Cedar** policies at retrieval time. An **observer agent** watches sync health, freshness and eval results, and takes only reversible actions (rollback, quarantine, pause) when something goes wrong.

## Quickstart

_Coming soon: `docker compose up`, `make sync`, connecting Claude Code / Claude Desktop to the MCP server._

## POC scope and assumptions

_Coming soon. Every simplification relative to the production design will be listed here explicitly._
