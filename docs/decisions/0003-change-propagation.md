# 0003. Change propagation: events + incremental polling + full reconciliation, with tombstones

- **Status:** accepted
- **Date:** 2026-11-19
- **Design:** [DESIGN §2](../DESIGN.md#2-data-connection)

## Context

"Current" needs a definition per source (a freshness target) and a mechanism that meets it. Sources differ: some send webhooks, some only expose "modified since", some expose nothing but a list. Every mechanism fails in its own way: webhooks are lost, polling misses deletions, full scans are slow and costly.

## Decision

Every connector implements one contract (`changes(cursor)`, `fetch(id)`, `list_ids()`), and the platform combines three mechanisms:

1. **Events** where the source offers them, for low latency.
2. **Incremental polling** with a cursor, as the safety net for lost events.
3. **Periodic full reconciliation**: compare the source's list of ids with ours. It is the only reliable way to catch deletions that were never announced.

Processing is idempotent and ordered per record: a content hash and a permission hash decide what to do (skip, permission-only fast path, or full re-processing). Deletions become **tombstones**, with a grace period before physical removal, so that a wrong deletion can be undone.

## Alternatives considered

- **Events only.** Lowest latency; silently stale after the first lost webhook.
- **Polling only.** Simple; misses deletions and is slower.
- **Full re-crawl on a schedule.** Always correct eventually; expensive and hours behind.
- **Change data capture at the database level.** Not available for SaaS sources.

## Consequences

- Freshness is bounded per source by its configured target, and measured end to end with canary edits (DESIGN §8).
- Reconciliation is also where circuit breakers act (ADR 0002).
- Connectors stay small: all the hard logic (hashing, ordering, tombstones) lives once in the platform.
- **In the POC:** `src/kb/connectors/` (contract and three file-based connectors), `src/kb/pipeline/sync.py`, `events.jsonl` standing in for EventBridge; `test_sync.py` (deletion found only by reconciliation, permission fast path, skip unchanged).
