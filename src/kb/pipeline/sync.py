"""Sync a source into the knowledge store (DESIGN.md §2, §5).

One sync run uses up to three change-detection mechanisms, each catching what the
previous one missed:

1. Events      (data/sources/events.jsonl stands in for webhooks / EventBridge)
2. Polling     (connector.changes(cursor))
3. Reconcile   (connector.list_ids() vs the records we hold) -> finds deletions

Every changed record goes through T1 snapshot -> T2 parse -> T3 enrich -> T4 embed.
Three hashes decide how much work a change needs: body (content), access (permissions
and classification) and metadata (title, validity...). Only an access change takes the
fast path; content and metadata changes create new asset versions.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date

import psycopg
from psycopg.types.json import Jsonb

from kb.config import EVENTS_FILE, SourceConfig
from kb.connectors.base import SourceRecord
from kb.pipeline.embed import embed
from kb.pipeline.enrich import AccessAttrs, map_permissions
from kb.pipeline.facts import extract_facts
from kb.pipeline.parse import parse
from kb.store.db import audit

ACTOR = "sync"


@dataclass
class SyncStats:
    source_id: str
    created: int = 0
    updated: int = 0
    permission_only: int = 0
    unchanged: int = 0
    deleted: int = 0
    frozen: bool = False
    messages: list[str] = field(default_factory=list)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _acl_hash(record: SourceRecord) -> str:
    """Everything that decides who may read: source permissions and classification."""
    access = {"acl": record.acl_raw, "classification": record.metadata.get("classification")}
    return _sha(json.dumps(access, sort_keys=True).encode())


def _meta_hash(record: SourceRecord) -> str:
    """Everything else that ends up in the assets: title, url, validity, language..."""
    meta = {k: v for k, v in record.metadata.items() if k != "classification"}
    data = {"title": record.title, "url": record.url, "metadata": meta}
    return _sha(json.dumps(data, sort_keys=True, default=str).encode())


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------


def sync_source(conn: psycopg.Connection, source: SourceConfig, reconcile: bool) -> SyncStats:
    stats = SyncStats(source.source_id)
    connector = source.build_connector()
    state = _sync_state(conn, source.source_id)

    if state["frozen"]:
        stats.frozen = True
        stats.messages.append(f"source frozen: {state['frozen_reason']}")
        return stats

    # 1. Events
    events, new_offset = _read_events(source.source_id, state["events_offset"])
    for event in events:
        record = connector.fetch(event["external_id"]) if event["type"] == "upsert" else None
        if record is None:
            _tombstone(conn, source.source_id, event["external_id"], stats, reason="event")
        else:
            _process(conn, record, source, stats)

    # 2. Incremental polling
    changed, cursor = connector.changes(state["cursor"])
    for record in changed:
        _process(conn, record, source, stats)

    # 3. Reconciliation (deletions)
    if reconcile:
        _reconcile(conn, source, connector.list_ids(), stats)

    conn.execute(
        "UPDATE sync_state SET cursor = %s, events_offset = %s, last_sync_at = now() "
        "WHERE source_id = %s",
        (cursor, new_offset, source.source_id),
    )
    conn.commit()
    return stats


def _sync_state(conn: psycopg.Connection, source_id: str) -> dict:
    conn.execute(
        "INSERT INTO sync_state (source_id) VALUES (%s) ON CONFLICT DO NOTHING", (source_id,)
    )
    return conn.execute("SELECT * FROM sync_state WHERE source_id = %s", (source_id,)).fetchone()


def _read_events(source_id: str, offset: int) -> tuple[list[dict], int]:
    """Read events appended to the shared events file since this source's offset."""
    if not EVENTS_FILE.exists():
        return [], offset
    lines = EVENTS_FILE.read_text().splitlines()
    events = [json.loads(ln) for ln in lines[offset:] if ln.strip()]
    return [e for e in events if e["source_id"] == source_id], len(lines)


# --------------------------------------------------------------------------------------
# T1 snapshot + routing: unchanged / permission-only / full processing
# --------------------------------------------------------------------------------------


def _process(
    conn: psycopg.Connection, record: SourceRecord, source: SourceConfig, stats: SyncStats
) -> None:
    body_hash, acl_hash, meta_hash = _sha(record.content), _acl_hash(record), _meta_hash(record)
    current = conn.execute(
        "SELECT r.status, rv.id, rv.body_hash, rv.acl_hash, rv.meta_hash FROM records r "
        "LEFT JOIN raw_versions rv ON rv.id = r.current_raw_id "
        "WHERE r.source_id = %s AND r.external_id = %s",
        (record.source_id, record.external_id),
    ).fetchone()

    is_active = current is not None and current["status"] == "active"
    hashes = (body_hash, acl_hash, meta_hash)
    if is_active and (current["body_hash"], current["acl_hash"], current["meta_hash"]) == hashes:
        stats.unchanged += 1  # a "touch" that changed nothing: skip all processing
        return

    raw_id = _snapshot(conn, record, hashes)
    attrs = map_permissions(record, source)

    if is_active and (current["body_hash"], current["meta_hash"]) == (body_hash, meta_hash):
        # Fast path: only access changed (permissions or classification). Update in place,
        # no re-parse, no re-embed, no new release needed (access is checked at query time).
        _update_permissions(conn, record, attrs, raw_id)
        stats.permission_only += 1
        audit(
            conn,
            ACTOR,
            "permission_change",
            source_id=record.source_id,
            external_id=record.external_id,
            audience=attrs.audience,
            country=attrs.country,
        )
        return

    old_raw_ids = _supersede(conn, record)
    _build_knowledge_assets(conn, record, source, attrs, raw_id)
    _update_permissions(conn, record, attrs, raw_id=None)  # also older, still-pinned versions
    _mark_derived_stale(conn, old_raw_ids)
    if current is None:
        stats.created += 1
    else:
        stats.updated += 1
    if attrs.audience == "owner_only":
        stats.messages.append(f"{record.external_id}: owner_only ({attrs.note})")


def _snapshot(conn: psycopg.Connection, record: SourceRecord, hashes: tuple[str, ...]) -> int:
    raw_id = conn.execute(
        "INSERT INTO raw_versions (source_id, external_id, source_version, body_hash, acl_hash, "
        "meta_hash, title, url, mime_type, content, acl_raw, metadata, updated_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (
            record.source_id,
            record.external_id,
            record.source_version,
            *hashes,
            record.title,
            record.url,
            record.mime_type,
            record.content,
            Jsonb(record.acl_raw),
            Jsonb(record.metadata),
            record.updated_at,
        ),
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO records (source_id, external_id, current_raw_id, status) "
        "VALUES (%s, %s, %s, 'active') ON CONFLICT (source_id, external_id) DO UPDATE "
        "SET current_raw_id = EXCLUDED.current_raw_id, status = 'active', tombstoned_at = NULL",
        (record.source_id, record.external_id, raw_id),
    )
    return raw_id


def _update_permissions(
    conn: psycopg.Connection, record: SourceRecord, attrs: AccessAttrs, raw_id: int | None
) -> None:
    """Apply the record's current permissions to ALL its asset versions.

    Permissions belong to the record, not to a version: an older version may still be
    pinned by the active release, and it must not keep permissions that were revoked.
    """
    conn.execute(
        "UPDATE asset_versions SET audience = %s, country = %s, classification = %s, "
        "owner = %s, mapping_note = %s, last_synced = now() "
        "WHERE source_id = %s AND external_id = %s",
        (
            attrs.audience,
            attrs.country,
            attrs.classification,
            attrs.owner,
            attrs.note,
            record.source_id,
            record.external_id,
        ),
    )
    if raw_id is not None:  # permission-only change: record the new raw version as provenance
        conn.execute(
            "UPDATE asset_versions SET provenance = array_append(provenance, %s) "
            "WHERE source_id = %s AND external_id = %s AND status = 'active'",
            (raw_id, record.source_id, record.external_id),
        )
    _refresh_facts(conn, record, attrs.country)


def _refresh_facts(conn: psycopg.Connection, record: SourceRecord, country: str) -> None:
    """Facts are keyed by country (e.g. vacation_days_per_year:CA): when a record moves to
    another country, re-extract them, or they would create false conflicts."""
    versions = conn.execute(
        "SELECT av.id, av.title, av.content, rv.mime_type, rv.content AS raw "
        "FROM asset_versions av JOIN raw_versions rv ON rv.id = av.provenance[1] "
        "WHERE av.source_id = %s AND av.external_id = %s AND av.type = 'knowledge'",
        (record.source_id, record.external_id),
    ).fetchall()
    for v in versions:
        conn.execute("DELETE FROM asset_facts WHERE asset_version_id = %s", (v["id"],))
        facts = extract_facts(v["title"], v["content"], country, v["mime_type"], bytes(v["raw"]))
        for key, value in facts.items():
            conn.execute(
                "INSERT INTO asset_facts (asset_version_id, fact_key, value) VALUES (%s, %s, %s)",
                (v["id"], key, value),
            )


def _supersede(conn: psycopg.Connection, record: SourceRecord) -> list[int]:
    """Mark the record's current knowledge assets as superseded; return their raw ids."""
    rows = conn.execute(
        "UPDATE asset_versions SET status = 'superseded' "
        "WHERE source_id = %s AND external_id = %s AND type = 'knowledge' AND status = 'active' "
        "RETURNING provenance",
        (record.source_id, record.external_id),
    ).fetchall()
    return sorted({rid for row in rows for rid in row["provenance"]})


def _mark_derived_stale(conn: psycopg.Connection, old_raw_ids: list[int]) -> None:
    """Invalidation cascades through provenance (DESIGN.md §3)."""
    if old_raw_ids:
        conn.execute(
            "UPDATE asset_versions SET status = 'stale' "
            "WHERE type <> 'knowledge' AND status = 'active' AND provenance && %s::bigint[]",
            (old_raw_ids,),
        )


# --------------------------------------------------------------------------------------
# T2 parse + T4 chunk/embed -> Knowledge assets
# --------------------------------------------------------------------------------------


def _build_knowledge_assets(
    conn: psycopg.Connection,
    record: SourceRecord,
    source: SourceConfig,
    attrs: AccessAttrs,
    raw_id: int,
) -> None:
    sections = parse(record.content, record.mime_type, record.title)
    paths = [f"{record.title} › {s.heading}" if s.heading else record.title for s in sections]
    # The section path is embedded with the text, so a chunk keeps its context.
    vectors = embed([f"{p}\n{s.text}" for p, s in zip(paths, sections, strict=True)])

    for index, (section, path, vector) in enumerate(zip(sections, paths, vectors, strict=True)):
        slug = section.heading.lower().replace(" ", "-") or f"part-{index}"
        asset_id = f"{record.source_id}:{record.external_id}#{slug}"
        version = conn.execute(
            "SELECT coalesce(max(version), 0) + 1 AS v FROM asset_versions WHERE asset_id = %s",
            (asset_id,),
        ).fetchone()["v"]
        conn.execute(
            "INSERT INTO asset_versions (asset_id, version, type, source_id, external_id, "
            "provenance, title, section_path, content, url, language, owner, department, "
            "country, audience, classification, mapping_note, replaces, valid_from, valid_to, "
            "source_updated_at, last_synced, embedding) VALUES "
            "(%s,%s,'knowledge',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),%s)",
            (
                asset_id,
                version,
                record.source_id,
                record.external_id,
                [raw_id],
                record.title,
                path,
                section.text,
                record.url,
                record.metadata.get("language", "en"),
                attrs.owner,
                source.department,
                attrs.country,
                attrs.audience,
                attrs.classification,
                attrs.note,
                record.metadata.get("replaces"),
                _date(record.metadata.get("valid_from")),
                _date(record.metadata.get("valid_to")),
                record.updated_at,
                vector,
            ),
        )


# --------------------------------------------------------------------------------------
# Deletions
# --------------------------------------------------------------------------------------


def _tombstone(
    conn: psycopg.Connection, source_id: str, external_id: str, stats: SyncStats, reason: str
) -> None:
    """Deletions take effect immediately, before any new release (DESIGN.md §5)."""
    updated = conn.execute(
        "UPDATE records SET status = 'tombstoned', tombstoned_at = now() "
        "WHERE source_id = %s AND external_id = %s AND status = 'active' RETURNING current_raw_id",
        (source_id, external_id),
    ).fetchone()
    if not updated:
        return
    conn.execute(
        # Every version, not only the latest: an older one may still be pinned by a release.
        "UPDATE asset_versions SET status = 'tombstoned' "
        "WHERE source_id = %s AND external_id = %s AND status <> 'quarantined'",
        (source_id, external_id),
    )
    raw_ids = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM raw_versions WHERE source_id = %s AND external_id = %s",
            (source_id, external_id),
        ).fetchall()
    ]
    _mark_derived_stale(conn, raw_ids)
    stats.deleted += 1
    audit(conn, ACTOR, "tombstone", source_id=source_id, external_id=external_id, reason=reason)


def _reconcile(
    conn: psycopg.Connection, source: SourceConfig, ids_in_source: set[str], stats: SyncStats
) -> None:
    active = {
        r["external_id"]
        for r in conn.execute(
            "SELECT external_id FROM records WHERE source_id = %s AND status = 'active'",
            (source.source_id,),
        ).fetchall()
    }
    missing = active - ids_in_source
    if not missing:
        return

    ratio = len(missing) / len(active)
    if ratio > source.max_delete_ratio:
        # Circuit breaker (DESIGN.md §5): this looks like a failure, not a real change.
        reason = (
            f"reconciliation would delete {len(missing)}/{len(active)} records "
            f"({ratio:.0%} > {source.max_delete_ratio:.0%})"
        )
        conn.execute(
            "UPDATE sync_state SET frozen = true, frozen_reason = %s WHERE source_id = %s",
            (reason, source.source_id),
        )
        audit(conn, ACTOR, "circuit_breaker", source_id=source.source_id, reason=reason)
        stats.frozen = True
        stats.messages.append(f"CIRCUIT BREAKER: {reason}. Source frozen.")
        return

    for external_id in sorted(missing):
        _tombstone(conn, source.source_id, external_id, stats, reason="reconciliation")
