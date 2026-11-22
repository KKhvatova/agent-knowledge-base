"""T5 Derive: publish LLM-derived Procedure assets with provenance (DESIGN.md §3).

POC simplification: procedures are fixtures in data/derived/procedures.yaml, written by
an LLM (Claude Code) during development, each with the hash of the source version it
was derived from. A procedure is published only while its sources still have exactly
that content. When a source changes, sync marks the published procedure `stale`
(invalidation through provenance) and this step reports it as needing regeneration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import psycopg
import yaml

from kb.config import ROOT
from kb.pipeline.embed import embed

PROCEDURES_FILE = ROOT / "data" / "derived" / "procedures.yaml"


@dataclass
class DeriveStats:
    published: int = 0
    unchanged: int = 0
    needs_regeneration: list[str] = field(default_factory=list)
    source_missing: list[str] = field(default_factory=list)


def derive(conn: psycopg.Connection, path: Path = PROCEDURES_FILE) -> DeriveStats:
    stats = DeriveStats()
    for proc in yaml.safe_load(path.read_text()) or []:
        current = []
        for src in proc["derived_from"]:
            source_id, external_id = src["record"].split(":", 1)
            row = conn.execute(
                "SELECT r.status, rv.id, rv.body_hash FROM records r "
                "JOIN raw_versions rv ON rv.id = r.current_raw_id "
                "WHERE r.source_id = %s AND r.external_id = %s",
                (source_id, external_id),
            ).fetchone()
            current.append((src, row))

        if any(row is None or row["status"] != "active" for _, row in current):
            stats.source_missing.append(proc["id"])
            continue
        if any(row["body_hash"] != src["body_sha256"] for src, row in current):
            stats.needs_regeneration.append(proc["id"])
            continue

        provenance = sorted(row["id"] for _, row in current)
        existing = conn.execute(
            "SELECT 1 FROM asset_versions WHERE asset_id = %s AND status = 'active' "
            "AND provenance = %s::bigint[]",
            (proc["id"], provenance),
        ).fetchone()
        if existing:
            stats.unchanged += 1
            continue
        _publish(conn, proc, provenance, current[0][0]["record"])
        stats.published += 1
    conn.commit()
    return stats


def _publish(conn: psycopg.Connection, proc: dict, provenance: list[int], first: str) -> None:
    """Insert a new Procedure version. It inherits the access attributes of its source:
    a derived asset can never be more visible than what it was derived from."""
    source_id, external_id = first.split(":", 1)
    src = conn.execute(
        "SELECT * FROM asset_versions WHERE source_id = %s AND external_id = %s "
        "AND type = 'knowledge' AND status = 'active' ORDER BY id LIMIT 1",
        (source_id, external_id),
    ).fetchone()
    conn.execute(
        "UPDATE asset_versions SET status = 'superseded' WHERE asset_id = %s "
        "AND status IN ('active', 'stale')",
        (proc["id"],),
    )
    version = conn.execute(
        "SELECT coalesce(max(version), 0) + 1 AS v FROM asset_versions WHERE asset_id = %s",
        (proc["id"],),
    ).fetchone()["v"]
    vector = embed([f"{proc['title']}\n{proc['steps']}"])[0]
    conn.execute(
        "INSERT INTO asset_versions (asset_id, version, type, source_id, external_id, "
        "provenance, title, section_path, content, url, language, owner, department, country, "
        "audience, classification, mapping_note, valid_from, valid_to, source_updated_at, "
        "last_synced, embedding) VALUES (%s,%s,'procedure',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
        "%s,%s,%s,%s,%s,%s,now(),%s)",
        (
            proc["id"],
            version,
            src["source_id"],
            src["external_id"],
            provenance,
            proc["title"],
            f"{proc['title']} (procedure derived from {src['title']})",
            proc["steps"],
            src["url"],
            proc["language"],
            src["owner"],
            src["department"],
            src["country"],
            src["audience"],
            src["classification"],
            src["mapping_note"],
            src["valid_from"],
            src["valid_to"],
            src["source_updated_at"],
            vector,
        ),
    )
