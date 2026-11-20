# 0002. Automatic updates for source-faithful content, eval-gated releases, owner approval for derived confidential content, circuit breakers

- **Status:** accepted
- **Date:** 2026-11-19
- **Design:** [DESIGN §1.2, §7](../DESIGN.md#7-self-maintenance)

## Context

The brief asks for a knowledge base that updates automatically. Taken literally, every change at the source would reach agents unchecked: a broken export, a parsing failure on a new layout, or an LLM mistake in a derived procedure would be served as truth within minutes. On the other side, manual review of every change does not scale and makes the system stale.

Not all changes carry the same risk. A deletion or a permission restriction must apply at once: delaying it is a leak. An addition or an edit can be wrong, and is safe to delay by minutes.

## Decision

- **Deletions and permission changes apply immediately**, to every version of the record, including versions pinned by the active release. They bypass releases.
- **Additions and modifications reach agents only through a release** of the Context Repo, promoted automatically if its eval suite passes (golden questions, ACL probes, behaviour checks). If the suite fails, the previous release stays active.
- **LLM-derived assets** (procedures, FAQs) carry provenance. When a source changes, assets derived from it become `stale` and leave the next release until they are regenerated.
- **Derived assets from confidential or restricted sources need owner approval** before any release can include them. Source-faithful content follows the source's own review process.
- **Circuit breakers** freeze a source at its last good state when a sync looks abnormal (for example, a mass deletion above a per-source ratio). An owner confirms, or the platform fixes the connector.

## Alternatives considered

- **Fully automatic, no gate.** Fastest; every upstream error reaches agents.
- **Human review of every change.** Safest on paper; does not scale, and reviewers approve by habit.
- **Gate everything, including deletions and permission changes.** Consistent, but turns a permission restriction into a window during which restricted content is still served.

## Consequences

- Minutes of extra lag for edits; zero extra lag for removals of access.
- The eval suite becomes part of the product, and must be maintained. Golden questions go stale when policies change legitimately: failures on records changed in the release are flagged for the owner to re-confirm, not excused (DESIGN §11.1).
- Owners have a small, bounded approval duty, limited to derived content on sensitive sources.
- **In the POC:** `kb release` (gate in `src/kb/evals/gate.py`); `test_releases.py` (immediate deletion and restriction on pinned versions); `test_derive.py` (stale on change, owner approval); `test_sync.py` (circuit breaker); `make demo-update`.
