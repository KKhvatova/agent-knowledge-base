# Agent Knowledge Base

A knowledge base system that gives AI agents access to company knowledge that is **accurate, current and authorized** at the moment they act.

Built as the take-home assignment for the AI Engineer role at ShopFully & Flipp.

## Deliverables

| # | Deliverable | Where |
|---|---|---|
| 1 | Production-grade system design (AWS) | [`docs/DESIGN.md`](docs/DESIGN.md) |
| 2 | Proof of Concept: HR assistant for a multi-country company | this repository |
| — | How AI was used across the SDLC | [`docs/AI-SDLC.md`](docs/AI-SDLC.md) |
| — | Architecture Decision Records | [`docs/decisions/`](docs/decisions/) |

## The idea in one paragraph

Department sources are synced into an immutable, versioned **raw snapshot**. From it the system derives typed **curated assets** (knowledge, procedures, tools, examples), each carrying owner, access policy, version and provenance. Each agent workflow consumes a versioned **Context Repo**: a manifest of the assets, tools and instructions it needs, which can be diffed and rolled back. Agents reach it through an **MCP server** that acts on behalf of the end user and enforces **Cedar** policies at retrieval time. An **observer agent** watches sync health, freshness and eval results, and takes only reversible actions (rollback, quarantine, ticket) when something goes wrong.

## What the POC shows

The scenario is an **HR assistant** for a company with employees in Italy (ShopFully), Canada and the United States (Flipp). The same agent answers different people differently, and correctly:

| Who asks (token) | Question | What the agent gets |
|---|---|---|
| Liam, staff, Canada | "how many vacation days do I get?" | Canadian policy (15 days), the derived procedure "How to request vacation", and his own live balance (4 days left) |
| Giulia, staff, Italy | "quanti giorni di ferie ho?" | Italian policy (26 working days), in Italian |
| Liam | "procedura disciplinare" | Nothing about it: the page is confidential, HR-only |
| Marco, HR, global scope | "procedura disciplinare" | The confidential Italian disciplinary procedure |
| Emma, staff, US | "how many PTO days" | The handbook says 20, the HRIS table says 15: both are returned with a **conflict** marker that names the most recent source |

Beyond retrieval, the POC runs the full lifecycle of the design:

- **Change detection**: events (webhook-like), incremental polling, and a full reconciliation that catches deletions nobody announced.
- **Permission changes and deletions** reach agents immediately, across every version of a record, without waiting for a release.
- **Content changes** reach agents only through a **release gated by evals**: golden questions, ACL leak probes, behaviour checks, and a content-collapse check that blocks a document shrinking to less than half its size.
- **Context Repo versioning**: every release pins asset versions; `kb repo diff` and `kb repo rollback` work like git.
- **LLM-derived procedures** with provenance: when the source page changes, the procedure is marked stale and dropped from the next release until it is regenerated.
- **Observer agent**: it detects a rejected release or a regression, diagnoses it (with an LLM if available), and acts within an allowlist (rollback, quarantine, ticket). It verifies the outcome and writes an incident report.

## Quickstart

**Requirements:** macOS or Linux, [Docker](https://www.docker.com/), [uv](https://docs.astral.sh/uv/) (Python 3.12 is installed by uv). No API key is needed.

```bash
git clone https://github.com/KKhvatova/agent-knowledge-base.git
cd agent-knowledge-base
make setup      # deps, Postgres+pgvector in Docker, sync all sources, first gated release
```

The first run downloads the multilingual embedding model once. Expected output ends with:

```
hr-assistant: candidate r1 -> PROMOTED
  evals: PASS hit@5=1.00 fact_recall=1.00 acl_leaks=0 behaviours=3/3
```

Then:

```bash
uv run kb status                 # per source: records, active assets, last sync, frozen
uv run kb eval                   # run the eval suite on the active release
uv run kb repo log               # release history of the hr-assistant Context Repo
make test                        # 61 tests (uses a separate kb_test database)
make help                        # every target
```

### Connect a real agent

The MCP server acts **on behalf of a user**: every call carries a signed token that binds the user and the Context Repo. Tokens for the synthetic employees:

| Email | Who |
|---|---|
| `liam.chen@flipp.example` | staff, Canada |
| `emma.johnson@flipp.example` | staff, United States |
| `giulia.rossi@shopfully.example` | staff, Italy |
| `marco.bianchi@shopfully.example` | HR, global scope |

**Claude Code** (HTTP transport):

```bash
make serve                                            # terminal 1: http://127.0.0.1:8765/mcp
claude mcp add --transport http kb-hr http://127.0.0.1:8765/mcp \
  --header "Authorization: Bearer $(uv run kb token liam.chen@flipp.example)"
claude                                                # then ask: "How many vacation days do I have left?"
```

To switch user, remove the server (`claude mcp remove kb-hr`) and add it again with another token. Tokens expire after 8 hours: after that, add the server again with a new token.

**Claude Desktop** (stdio transport), in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "kb-hr": {
      "command": "/absolute/path/to/uv",
      "args": ["--directory", "/absolute/path/to/agent-knowledge-base", "run", "kb", "serve"],
      "env": { "KB_MCP_TRANSPORT": "stdio", "KB_TOKEN": "<output of: uv run kb token liam.chen@flipp.example>" }
    }
  }
}
```

Use the absolute path printed by `which uv`: Claude Desktop does not start servers with your shell's `PATH`. Restart Claude Desktop after editing the file, and again with a new token after 8 hours.

Tools exposed: `search_knowledge`, `get_asset`, `get_leave_balance` (live HRIS read, own data only), `context_info`. Retrieved text is returned as `untrusted_source_data`, with citation, validity dates, freshness and conflicts.

### Demos

Each demo starts from a clean state and changes only the sources working copy (`.data/sources`), never the committed fixtures.

```bash
make demo-update     # HR-CA raises vacation 15 -> 18 days: sync, gated release r2, diff r1..r2
make demo-incident   # a corrupted page arrives: the gate rejects r2, the observer quarantines
                     # the bad version, opens a ticket and verifies; the next release passes
make demo-rollback   # a bad release is forced past the gate: the observer detects the
                     # regression, rolls back to r1 and verifies
```

Incident reports are written to `incidents/INC-NNNN.md`. Single changes can also be applied by hand, then picked up with `make sync` (permission and deletion changes need no release):

```bash
uv run python scripts/demo.py delete-page     # a page deleted at the source, with no event
uv run python scripts/demo.py restrict-page   # a page restricted to HR
```

### Optional: an LLM for the observer

The observer works without an LLM: it falls back to a deterministic, template-based diagnosis. With an LLM it writes the diagnosis and proposes an action, which is still validated against the allowlist.

```bash
# Local, free: Ollama, native install
ollama pull qwen2.5:7b
KB_LLM=ollama make demo-incident

# ... or Ollama in Docker
docker compose --profile llm up -d
docker compose exec ollama ollama pull qwen2.5:7b
KB_LLM=ollama make demo-incident

# Anthropic API
ANTHROPIC_API_KEY=... KB_LLM=anthropic make demo-incident
```

`KB_LLM=auto` (default) uses Anthropic if a key is set, then Ollama if it is running, then the template. Set `KB_LLM=template` for fully reproducible demo output.

## Repository map

```
config/sources.yaml            sources: paths, owners, freshness targets, circuit breakers, permission mapping
context_repos/hr-assistant/    the Context Repo: manifest, system prompt, eval suite
policies/kb.cedar              authorization policies (Cedar)
data/sources/                  synthetic source fixtures (Confluence export, Drive PDFs, HRIS tables)
data/derived/procedures.yaml   LLM-derived procedures, with the hash of the source they came from
scripts/                       fixture generation, procedure derivation, demo changes
src/kb/connectors/             one connector per source type, behind a common contract
src/kb/pipeline/               sync (detect, snapshot, parse, enrich, embed, facts), derive
src/kb/store/                  Postgres schema, releases (build, gate, promote, diff, rollback)
src/kb/authz/                  identity tokens, Cedar check, equivalent SQL pre-filter
src/kb/retrieval.py            hybrid search (vector + full-text, RRF), conflicts, validity
src/kb/mcp_server/             MCP server
src/kb/evals/                  eval runner and release gate
src/kb/observer/               observer agent and LLM providers
tests/                         61 tests, including an authorization matrix (Cedar vs SQL agreement)
```

## POC vs production

Every component maps to a production component in [`docs/DESIGN.md`](docs/DESIGN.md). The POC keeps the **contracts and the behaviour** and replaces the infrastructure.

| Concern | POC | Production (AWS) |
|---|---|---|
| Sources | Files exported from Confluence, Drive and HRIS | Connectors calling the source APIs |
| Change events | `events.jsonl` | EventBridge + SQS FIFO |
| Orchestration | `kb sync`, `kb release` CLI | Step Functions, Lambda / ECS tasks |
| Raw snapshot | `raw_versions` table | S3 with versioning and Object Lock |
| Curated assets, vectors, releases | PostgreSQL 16 + pgvector in Docker | Aurora PostgreSQL + pgvector |
| Embeddings | `paraphrase-multilingual-MiniLM-L12-v2` on CPU (fastembed) | Bedrock multilingual embeddings |
| Derivation (procedures, facts) | Fixtures written by Claude Code; regex fact extractor | Claude on Bedrock with structured output |
| Identity | HS256 JWT issued by `kb token` | AgentCore Identity, corporate IdP, on-behalf-of |
| Authorization | `cedarpy` + hand-written SQL filter | Verified Permissions / AgentCore Policy (Cedar) |
| MCP server | FastMCP on localhost | AgentCore Runtime + Gateway |
| Observer LLM | Ollama, Anthropic API or template | Claude on Bedrock |
| Monitoring, tickets | Incident files and tables | CloudWatch / OpenTelemetry, ticketing system |

## Simplifications, stated explicitly

- **Synthetic data.** All policies, employees and balances are invented. Every document carries the notice *"SYNTHETIC DATA for a proof of concept. Not a real ShopFully or Flipp policy."* and uses `.example` domains. Countries and brands match the real company, the content does not.
- **Public website not ingested.** The design treats the public website as one more source. The POC leaves it out: its content adds nothing to the HR scenario, and an automated snapshot would have meant working around the site's bot protection.
- **File-based connectors.** They implement the same contract (`changes`, `fetch`, `list_ids`) that an API connector would. The permission metadata has the same shape as the real one (Confluence restrictions, Drive group emails, HRIS country visibility).
- **Procedures are fixtures.** In production an LLM derives them in the pipeline. Here Claude Code wrote them during development, from the current pages. They are stored with the SHA-256 of the source version, so provenance-based invalidation is real. Regeneration (`scripts/derive_procedures.py`) is manual.
- **Fact extraction is a regex.** It covers one fact type (vacation days per year per country), which is enough to show conflict detection end to end. Production uses an LLM extractor with the same `asset_facts` output.
- **SQL filter written by hand.** The pre-ranking filter mirrors `policies/kb.cedar`. A test matrix checks that the two always agree, and an explicit Cedar check runs after retrieval. In production the filter would be generated from the policies (Cedar partial evaluation).
- **One Context Repo, one tenant, one region.** The design has regional data planes (EU, Canada). The POC runs one.
- **The JWT secret has a development default.** Set `KB_JWT_SECRET` outside a demo.
- **No owner UI.** Owner approval of derived confidential content is the `approved_by` column. Tickets are incident reports, not issues in a tracker.

## Known results and limits

- **Eval baseline:** hit@5 = 1.00, fact recall = 1.00, ACL leaks = 0, behaviours 3/3 on 16 golden questions, 6 ACL probes and 3 behaviour checks. The derived procedures moved fact recall from 0.94 to 1.00.
- **Small golden set.** It is written by the same person who wrote the data, so it measures regressions, not real-world quality.
- **Ranking is not always ideal.** With a very small `k`, generic sections ("Carry-over", "How to request") can outrank the entitlement section. Hybrid search with RRF and no reranker is the reason. Production adds a reranker.
- **Instructions are guidance in generic clients.** In Claude Desktop or Claude Code the Context Repo instructions reach the agent as MCP server instructions, not as its system prompt, so the client's model may still add general knowledge (clearly labelled, in our tests). An agent built for this Context Repo uses them as its system prompt.
- **Golden questions go stale.** After the legitimate 15→18 edit in `make demo-update`, the golden fact "15 days" no longer holds. The runner cannot tell a legitimate edit from corruption, so it **flags** the failure for the owner to re-confirm instead of excusing it.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `KB_DATABASE_URL` | `postgresql://kb:kb@localhost:5433/kb` | Knowledge store |
| `KB_SOURCES_DIR` | `.data/sources` | Sources working copy (recreated by `make reset`) |
| `KB_EMBEDDING_MODEL` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | Local embedding model |
| `KB_JWT_SECRET` | development value | Token signing key |
| `KB_TOKEN` | — | Token for the stdio transport |
| `KB_MCP_TRANSPORT`, `KB_MCP_PORT` | `http`, `8765` | MCP server transport |
| `KB_LLM` | `auto` | Observer LLM: `auto`, `anthropic`, `ollama`, `template` |
| `KB_OLLAMA_URL`, `KB_OLLAMA_MODEL` | `http://localhost:11434`, `qwen2.5:7b` | Ollama |
| `KB_ANTHROPIC_MODEL` | `claude-sonnet-5-5` | Anthropic model |

## Development

```bash
uv run pre-commit install --hook-type pre-commit --hook-type commit-msg
```

Commits follow [Conventional Commits](https://www.conventionalcommits.org/), checked by Commitizen. Ruff runs on every commit.
