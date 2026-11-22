# 0006. Aurora PostgreSQL + pgvector as the asset store

- **Status:** accepted
- **Date:** 2026-11-19
- **Design:** [DESIGN §6, §7](../DESIGN.md#6-technology-choices)

## Context

The store holds assets, their versions and provenance, entities, Context Repo releases, permission attributes and the vectors used for search. Retrieval must filter by permissions, validity and release **in the same query** as vector and keyword search, and "which version is active" must never disagree with "what is indexed".

## Decision

Use **Aurora PostgreSQL with pgvector** (HNSW indexes) and Postgres full-text search, fused with Reciprocal Rank Fusion. One transactional store is the system of record and the search index.

## Alternatives considered

- **OpenSearch Serverless.** Excellent hybrid search, and its newer generation scales to zero, so cost is no longer the objection. Excluded because versions, releases, entities and permissions would live in a second store, and keeping two stores consistent is exactly the class of problem this system exists to avoid. It is the **first choice for scaling out** search.
- **S3 Vectors.** Very cheap at large scale; no keyword search, limited filterable metadata per vector, no joins. Candidate for a cold tier.
- **Kendra.** Costly, opaque ranking.
- **Neptune or another graph database.** No multi-hop need today (DESIGN §3).

## Consequences

- Releases, permission filters and search are consistent by construction (transactions).
- Search quality is good, not best-in-class; a reranker is the first improvement (the POC shows the ranking weakness it fixes).
- Scale ceiling around tens of millions of chunks per region with partitioning. Beyond that, move search to OpenSearch and keep Aurora as the system of record.
- **In the POC:** PostgreSQL 16 + pgvector 0.8 in Docker (`src/kb/store/schema.sql`), hybrid search in `src/kb/retrieval.py`.
