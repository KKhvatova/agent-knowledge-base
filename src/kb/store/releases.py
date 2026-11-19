"""Context Repos and releases (DESIGN.md §5.5, §7.2, §7.3).

A release pins exact asset versions, plus the manifest and the agent's instructions.
Agents only ever see the active release, so:
- additions and modifications reach agents only when a new release is promoted;
- deletions and permission changes apply immediately (they act on asset status and
  attributes, which retrieval checks on every query);
- rollback is moving the active pointer back.
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

import psycopg
import yaml
from psycopg.types.json import Jsonb

from kb.config import ROOT
from kb.store.db import audit

CONTEXT_REPOS = ROOT / "context_repos"


@dataclass(frozen=True)
class ContextRepo:
    name: str
    manifest: dict
    instructions: str

    @classmethod
    def load(cls, name: str, root: Path = CONTEXT_REPOS) -> ContextRepo:
        folder = root / name
        manifest = yaml.safe_load((folder / "manifest.yaml").read_text())
        instructions = (folder / manifest["instructions"]).read_text()
        return cls(name=name, manifest=manifest, instructions=instructions)


def register(conn: psycopg.Connection, repo: ContextRepo) -> None:
    conn.execute(
        "INSERT INTO context_repos (name, manifest) VALUES (%s, %s) "
        "ON CONFLICT (name) DO UPDATE SET manifest = EXCLUDED.manifest",
        (repo.name, Jsonb(repo.manifest)),
    )


def _in_scope_asset_ids(conn: psycopg.Connection, repo: ContextRepo) -> list[int]:
    """Current versions of every asset inside the repo's scope.

    If a record's newest versions were quarantined by the observer, the versions pinned
    by the active release are carried over, so the record does not disappear.
    """
    scope = repo.manifest["scope"]
    rows = conn.execute(
        "SELECT id, source_id FROM asset_versions av "
        "WHERE av.type = ANY(%(types)s) AND av.department = ANY(%(departments)s) AND ("
        "  av.status = 'active'"
        "  OR (av.id IN (SELECT ra.asset_version_id FROM release_assets ra "
        "                JOIN context_repos c ON c.active_release = ra.release_id "
        "                WHERE c.name = %(repo)s)"
        "      AND av.status = 'superseded'"
        "      AND EXISTS (SELECT 1 FROM asset_versions q WHERE q.status = 'quarantined' "
        "                  AND q.source_id = av.source_id AND q.external_id = av.external_id)"
        "      AND NOT EXISTS (SELECT 1 FROM asset_versions a WHERE a.status = 'active' "
        "                  AND a.source_id = av.source_id AND a.external_id = av.external_id)))",
        {"types": scope["asset_types"], "departments": scope["departments"], "repo": repo.name},
    ).fetchall()
    return sorted(
        r["id"] for r in rows if any(fnmatch(r["source_id"], p) for p in scope["sources"])
    )


def active_release(conn: psycopg.Connection, repo_name: str) -> dict | None:
    return conn.execute(
        "SELECT r.* FROM releases r JOIN context_repos c ON c.active_release = r.id "
        "WHERE c.name = %s",
        (repo_name,),
    ).fetchone()


def _pinned(conn: psycopg.Connection, release_id: int) -> list[int]:
    rows = conn.execute(
        "SELECT asset_version_id FROM release_assets WHERE release_id = %s ORDER BY 1",
        (release_id,),
    ).fetchall()
    return [r["asset_version_id"] for r in rows]


def build_candidate(conn: psycopg.Connection, repo: ContextRepo, created_by: str) -> dict | None:
    """Create a candidate release, or return None if nothing changed since the active one."""
    register(conn, repo)
    asset_ids = _in_scope_asset_ids(conn, repo)
    current = active_release(conn, repo.name)
    if (
        current
        and _pinned(conn, current["id"]) == asset_ids
        and current["manifest"] == repo.manifest
        and current["instructions"] == repo.instructions
    ):
        return None

    number = conn.execute(
        "SELECT coalesce(max(number), 0) + 1 AS n FROM releases WHERE context_repo = %s",
        (repo.name,),
    ).fetchone()["n"]
    release = conn.execute(
        "INSERT INTO releases (context_repo, number, status, created_by, manifest, instructions) "
        "VALUES (%s, %s, 'candidate', %s, %s, %s) RETURNING *",
        (repo.name, number, created_by, Jsonb(repo.manifest), repo.instructions),
    ).fetchone()
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO release_assets (release_id, asset_version_id) VALUES (%s, %s)",
            [(release["id"], aid) for aid in asset_ids],
        )
    audit(
        conn,
        created_by,
        "release_candidate",
        context_repo=repo.name,
        number=number,
        assets=len(asset_ids),
    )
    return release


def promote(conn: psycopg.Connection, release: dict, eval_report: dict, actor: str) -> None:
    conn.execute(
        "UPDATE releases SET status = 'promoted', eval_report = %s WHERE id = %s",
        (Jsonb(eval_report), release["id"]),
    )
    conn.execute(
        "UPDATE context_repos SET active_release = %s WHERE name = %s",
        (release["id"], release["context_repo"]),
    )
    audit(
        conn,
        actor,
        "release_promoted",
        context_repo=release["context_repo"],
        number=release["number"],
    )


def reject(conn: psycopg.Connection, release: dict, eval_report: dict, actor: str) -> None:
    conn.execute(
        "UPDATE releases SET status = 'rejected', eval_report = %s WHERE id = %s",
        (Jsonb(eval_report), release["id"]),
    )
    audit(
        conn,
        actor,
        "release_rejected",
        context_repo=release["context_repo"],
        number=release["number"],
    )


def rollback(
    conn: psycopg.Connection, repo_name: str, to_number: int | None, actor: str, reason: str
) -> dict:
    """Move the active pointer back: to `to_number`, or to the previous promoted release."""
    current = active_release(conn, repo_name)
    if current is None:
        raise ValueError(f"{repo_name} has no active release")
    if to_number is None:
        target = conn.execute(
            "SELECT * FROM releases WHERE context_repo = %s AND status = 'promoted' "
            "AND number < %s ORDER BY number DESC LIMIT 1",
            (repo_name, current["number"]),
        ).fetchone()
    else:
        target = conn.execute(
            "SELECT * FROM releases WHERE context_repo = %s AND number = %s "
            "AND status IN ('promoted', 'rolled_back')",
            (repo_name, to_number),
        ).fetchone()
    if target is None:
        raise ValueError("No earlier release to roll back to")

    conn.execute("UPDATE releases SET status = 'rolled_back' WHERE id = %s", (current["id"],))
    conn.execute("UPDATE releases SET status = 'promoted' WHERE id = %s", (target["id"],))
    conn.execute(
        "UPDATE context_repos SET active_release = %s WHERE name = %s", (target["id"], repo_name)
    )
    audit(
        conn,
        actor,
        "rollback",
        context_repo=repo_name,
        from_number=current["number"],
        to_number=target["number"],
        reason=reason,
    )
    return target


def log(conn: psycopg.Connection, repo_name: str) -> list[dict]:
    return conn.execute(
        "SELECT r.number, r.status, r.created_at, r.created_by, "
        "(SELECT count(*) FROM release_assets ra WHERE ra.release_id = r.id) AS assets, "
        "(c.active_release = r.id) AS active, r.eval_report->>'summary' AS evals "
        "FROM releases r JOIN context_repos c ON c.name = r.context_repo "
        "WHERE r.context_repo = %s ORDER BY r.number",
        (repo_name,),
    ).fetchall()


def diff(conn: psycopg.Connection, repo_name: str, a: int, b: int) -> dict[str, list[str]]:
    """Assets added, removed and changed (new version) between releases a and b."""

    def versions(number: int) -> dict[str, tuple[int, str]]:
        rows = conn.execute(
            "SELECT av.asset_id, av.version, av.section_path FROM releases r "
            "JOIN release_assets ra ON ra.release_id = r.id "
            "JOIN asset_versions av ON av.id = ra.asset_version_id "
            "WHERE r.context_repo = %s AND r.number = %s",
            (repo_name, number),
        ).fetchall()
        return {r["asset_id"]: (r["version"], r["section_path"]) for r in rows}

    old, new = versions(a), versions(b)
    return {
        "added": sorted(new[k][1] for k in new.keys() - old.keys()),
        "removed": sorted(old[k][1] for k in old.keys() - new.keys()),
        "changed": sorted(
            f"{new[k][1]} (v{old[k][0]} → v{new[k][0]})"
            for k in new.keys() & old.keys()
            if new[k][0] != old[k][0]
        ),
    }
