# AI-SDLC: how AI was used to build this

This document records how AI was used in each phase and who decided what.

## Tools

| Tool | Used for |
|---|---|
| **Claude Code** (CLI, Claude Opus) | Design interview, research, writing the design document, implementation, tests, verification, commits |
| **Claude Code as an MCP client** | The agent of the POC: it connects to the knowledge base MCP server with a user token |
| **Claude Code as a curator** | Wrote the four LLM-derived procedures that the POC publishes (with provenance) |
| **Ollama** (`qwen2.5:7b`, local) / Anthropic API, optional | Diagnosis step of the observer agent |
| **Pre-commit hooks** (ruff, Commitizen) | Automatic checks on every AI-written commit |

## Who decided what

The AI did most of the typing. Every decision that shapes the system went through me, and several key ones came from me.

| Area | Came from me |
|---|---|
| Overall approach | Start from context-engineering practice: versioned context per agent workflow, not a single index |
| Self-maintenance | **Data versioning with rollback** as a hard requirement; an **observer agent** that intervenes when something goes wrong |
| Accuracy | When sources conflict, show both and **name the most recent one** (I combined two of the proposed options) |
| Stack | Python, Claude, **AWS** for production, freedom to differ in the POC |
| POC scope | HR scenario, synthetic data (the public website was planned, then left out), MCP as the interface, local execution |
| Engineering practice | Conventional Commits checked by **Commitizen** (light version: check only, no version bump) |

## 1. Design

**Grilling before writing.** I did not ask the AI for a design. I asked it to interview me. Using a "grill me" skill, Claude Code walked a design tree one round at a time: scope, data, users, sources, freshness, permissions, versioning, evaluation, POC constraints. About 50 questions in total. The recorded answers became the backbone of the document and of the [ADRs](decisions/).

**Section by section.** The document was written one section at a time. I reviewed each section before the next one, and asked for changes when the meaning was not clear. Diagrams are Mermaid, checked by rendering them on GitHub.

## 2. Implementation

**Vertical slices, each one runnable.** The POC was built in steps that each ended in a working, tested state, and a commit:

1. Project skeleton and tooling (uv, ruff, pytest, pre-commit, Docker Compose).
2. Synthetic sources generator: one script is the single origin of all fixtures.
3. Connector contract and three file-based connectors.
4. Knowledge store and sync pipeline (events, polling, reconciliation, permission fast path, tombstones, circuit breaker).
5. Context Repo releases, Cedar authorization, hybrid retrieval, MCP server.
6. Conflict detection, eval suite as release gate, observer agent, demos.
7. LLM-derived procedures with provenance.

**AI inside the product, not only to write it.** The four procedures that the HR assistant serves were written by Claude Code from the current source pages, as the pipeline's derivation step would do on Bedrock. They are stored with the SHA-256 of the source version they were derived from. When a source page changes, the procedure becomes stale and leaves the next release, and a test fails if someone edits a source fixture without regenerating them. This made the provenance mechanism real instead of described. It also had a measurable effect: fact recall went from 0.94 to 1.00, because the sick-leave procedure answers a question that the source page alone answered poorly.

**Working without a paid API.** The POC must run for anyone who clones it, with no paid API key.

**Commit hygiene.** Pre-commit runs ruff and ruff-format on every commit and Commitizen on every message. These hooks rejected several AI-written commits (long lines, formatting), which were fixed before committing again.

## 3. Testing

The AI wrote tests together with each slice, not afterwards: 61 tests run against a real Postgres. The most important ones:

- **Authorization matrix.** For every test user and every asset, the hand-written SQL filter and the Cedar policies must give the same answer. This is the test that makes a hand-written filter acceptable.
- **Security regression tests.** While building releases, the AI found that deletions and permission restrictions were applied only to the *active* version of a record. Older versions, still pinned by the active release, kept being served. Both bugs were fixed (the change now applies to every version) and each got a regression test.
- **Fixture consistency.** A test fails if a source page changes without the derived procedures being regenerated.
- **Evals as tests of the content.** The eval suite (16 golden questions, 6 ACL leak probes, 3 behaviour checks) is the release gate. The first baseline had fact recall 0.94 because of one missed question. I kept it visible instead of tuning the question away; it was fixed later by the derived procedure.

## 4. Review and verification

**Every claim in the documents checked against the code.** Before each documentation commit, the AI checked what the documents said against the repository: test names cited as evidence, defaults of environment variables, command output.

**The MCP server tested with a real client.** Before writing the README, the server was queried through an MCP client with tokens for four users, to confirm that answers differ correctly by country and role, that conflicts and live data are returned, and that confidential content stays hidden.
