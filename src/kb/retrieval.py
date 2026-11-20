"""Retrieval: hybrid search over the active release, filtered by policy (DESIGN.md §6).

What an agent sees is the intersection of:
- the Context Repo's active release (scope + pinned versions),
- the asset's current status (deleted or quarantined assets are excluded immediately),
- business validity (what is in force on `as_of`, today by default),
- the user's permissions (SQL filter before ranking + Cedar check after).

The response carries the evidence an agent needs to judge each result: citation,
version, validity, freshness, future versions and warnings (DESIGN.md §6.2).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import psycopg
from psycopg import sql

from kb.authz.identity import Principal
from kb.authz.policy import cedar_allowed, sql_filter
from kb.config import SOURCES_DIR, load_sources
from kb.pipeline.embed import embed
from kb.store.db import audit
from kb.store.releases import active_release

RRF_K = 60  # standard constant for reciprocal rank fusion


def _visible_where(principal: Principal, release_id: int, as_of: date) -> tuple[sql.Composed, dict]:
    policy, params = sql_filter(principal)
    where = sql.SQL(
        "ra.release_id = %(release_id)s"
        " AND av.status NOT IN ('tombstoned', 'quarantined')"
        " AND (av.valid_from IS NULL OR av.valid_from <= %(as_of)s)"
        " AND (av.valid_to IS NULL OR av.valid_to >= %(as_of)s)"
        " AND {policy}"
    ).format(policy=policy)
    return where, {**params, "release_id": release_id, "as_of": as_of}


def _ranked(
    conn: psycopg.Connection,
    where: sql.Composed,
    params: dict,
    order: sql.SQL,
    extra: sql.SQL,
    limit: int,
) -> list[dict]:
    query = sql.SQL(
        "SELECT av.* FROM asset_versions av "
        "JOIN release_assets ra ON ra.asset_version_id = av.id "
        "WHERE {where} {extra} ORDER BY {order} LIMIT %(limit)s"
    ).format(where=where, extra=extra, order=order)
    return conn.execute(query, {**params, "limit": limit}).fetchall()


def search(
    conn: psycopg.Connection,
    principal: Principal,
    query: str,
    k: int = 6,
    as_of: date | None = None,
    candidates: int = 20,
    release: dict | None = None,
) -> dict:
    """Search the active release, or `release` if given (evals run on candidates)."""
    as_of = as_of or date.today()
    release = release or active_release(conn, principal.context_repo)
    if release is None:
        return {"release": None, "results": [], "warnings": ["No active release."]}

    where, params = _visible_where(principal, release["id"], as_of)
    vector = embed([query])[0]

    by_vector = _ranked(
        conn,
        where,
        {**params, "q": vector},
        order=sql.SQL("av.embedding <=> %(q)s"),
        extra=sql.SQL(""),
        limit=candidates,
    )
    by_keyword = _ranked(
        conn,
        where,
        {**params, "text": query},
        order=sql.SQL("ts_rank(av.tsv, websearch_to_tsquery('simple', %(text)s)) DESC"),
        extra=sql.SQL("AND av.tsv @@ websearch_to_tsquery('simple', %(text)s)"),
        limit=candidates,
    )

    # Reciprocal rank fusion: robust to the different scales of the two scores.
    scores: dict[int, float] = {}
    rows: dict[int, dict] = {}
    for ranking in (by_vector, by_keyword):
        for rank, row in enumerate(ranking):
            scores[row["id"]] = scores.get(row["id"], 0.0) + 1.0 / (RRF_K + rank + 1)
            rows[row["id"]] = row
    top = [rows[i] for i in sorted(scores, key=scores.get, reverse=True)[:k]]

    # Step 2: explicit Cedar check. A disagreement means the SQL translation has a bug.
    allowed = cedar_allowed(principal, top)
    mismatches = [a["asset_id"] for a, ok in zip(top, allowed, strict=True) if not ok]
    if mismatches:
        audit(conn, "mcp", "authz_mismatch", user=principal.email, assets=mismatches)
    top = [a for a, ok in zip(top, allowed, strict=True) if ok]

    results = [_to_result(conn, principal, release, a, as_of) for a in top]
    audit(
        conn,
        "mcp",
        "search",
        user=principal.email,
        agent=principal.context_repo,
        release=release["number"],
        query=query,
        assets=[f"{a['asset_id']}@v{a['version']}" for a in top],
    )
    conn.commit()
    return {
        "release": f"{principal.context_repo}@r{release['number']}",
        "user": _user(principal),
        "as_of": as_of.isoformat(),
        "results": results,
        "warnings": _freshness_warnings(conn, {a["source_id"] for a in top}),
    }


def _to_result(
    conn: psycopg.Connection, principal: Principal, release: dict, asset: dict, as_of: date
) -> dict:
    return {
        "asset_id": asset["asset_id"],
        "version": asset["version"],
        "type": asset["type"],
        # Retrieved content is data, never instructions (DESIGN.md §6.5).
        "content": {"trust": "untrusted_source_data", "text": asset["content"]},
        "citation": {
            "title": asset["title"],
            "section": asset["section_path"],
            "url": asset["url"],
            "source": asset["source_id"],
        },
        "language": asset["language"],
        "valid_from": asset["valid_from"].isoformat() if asset["valid_from"] else None,
        "valid_to": asset["valid_to"].isoformat() if asset["valid_to"] else None,
        "last_synced": asset["last_synced"].isoformat(),
        "future_version": _future_version(conn, principal, release, asset, as_of),
        "conflicts": _conflicts(conn, principal, release, asset, as_of),
    }


def _conflicts(
    conn: psycopg.Connection, principal: Principal, release: dict, asset: dict, as_of: date
) -> list[dict]:
    """Other visible assets stating a different value for the same fact (DESIGN.md §1.2).

    No authority rule is configured in the POC, so every conflict is shown to the agent,
    with the most recently changed source marked as a hint, not a verdict.
    """
    where, params = _visible_where(principal, release["id"], as_of)
    rows = conn.execute(
        sql.SQL(
            "SELECT mine.fact_key, f.value, av.title, av.section_path, av.source_id, "
            "av.source_updated_at, av.asset_id FROM asset_facts mine "
            "JOIN asset_facts f ON f.fact_key = mine.fact_key "
            "JOIN asset_versions av ON av.id = f.asset_version_id "
            "JOIN release_assets ra ON ra.asset_version_id = av.id "
            "WHERE mine.asset_version_id = %(me)s AND {where}"
        ).format(where=where),
        {**params, "me": asset["id"]},
    ).fetchall()
    conflicts = []
    for key in sorted({r["fact_key"] for r in rows}):
        group = [r for r in rows if r["fact_key"] == key]
        if len({r["value"] for r in group}) < 2:
            continue
        newest = max(r["source_updated_at"] for r in group)
        conflicts.append(
            {
                "fact": key,
                "statements": [
                    {
                        "value": r["value"],
                        "source": r["source_id"],
                        "title": r["title"],
                        "section": r["section_path"],
                        "source_updated_at": r["source_updated_at"].isoformat(),
                        "most_recent": r["source_updated_at"] == newest,
                    }
                    for r in sorted(group, key=lambda r: r["source_updated_at"], reverse=True)
                ],
                "resolution": "No authority rule: show both values; the most recent is marked.",
            }
        )
    return conflicts


def _future_version(
    conn: psycopg.Connection, principal: Principal, release: dict, asset: dict, as_of: date
) -> dict | None:
    """Is there a visible document that replaces this one later? (DESIGN.md §5.4)"""
    policy, params = sql_filter(principal)
    row = conn.execute(
        sql.SQL(
            "SELECT av.title, av.valid_from FROM asset_versions av "
            "JOIN release_assets ra ON ra.asset_version_id = av.id "
            "WHERE ra.release_id = %(release_id)s AND av.replaces = %(ext)s "
            "AND av.valid_from > %(as_of)s AND av.status NOT IN ('tombstoned', 'quarantined') "
            "AND {policy} ORDER BY av.valid_from LIMIT 1"
        ).format(policy=policy),
        {**params, "release_id": release["id"], "ext": asset["external_id"], "as_of": as_of},
    ).fetchone()
    if row is None:
        return None
    return {"title": row["title"], "valid_from": row["valid_from"].isoformat()}


def _freshness_warnings(conn: psycopg.Connection, source_ids: set[str]) -> list[str]:
    sources = load_sources()
    warnings = []
    for sid in sorted(source_ids):
        state = conn.execute(
            "SELECT last_sync_at, frozen, frozen_reason FROM sync_state WHERE source_id = %s",
            (sid,),
        ).fetchone()
        if state is None:
            continue
        if state["frozen"]:
            warnings.append(
                f"Source {sid} is frozen ({state['frozen_reason']}); "
                "its content may be out of date."
            )
        target = timedelta(minutes=sources[sid].freshness_target_minutes)
        if state["last_sync_at"] and datetime.now(UTC) - state["last_sync_at"] > target:
            synced = state["last_sync_at"].strftime("%Y-%m-%d %H:%M")
            warnings.append(
                f"Source {sid} was last synced at {synced} UTC, beyond its freshness target."
            )
    return warnings


def _user(principal: Principal) -> dict:
    """Who the agent is acting for. Without it, an agent cannot tell whether a country's
    policy applies to the user or is simply the only one it found."""
    return {
        "name": principal.name,
        "country": principal.country,
        "role": principal.role,
        "note": "Results include only what this user may see; policies for other "
        "countries or roles may exist but are not shown.",
    }


def get_leave_balance(conn: psycopg.Connection, principal: Principal) -> dict:
    """Live tool: personal data is read from the HRIS at request time, never indexed.

    POC simplification: the "HRIS API" is the JSON export on disk. The tool takes no
    employee parameter, so it can only ever return the caller's own data.
    """
    path = SOURCES_DIR / "hris" / "leave_balances.json"
    try:
        rows = json.loads(path.read_text())
    except OSError:
        return {"error": "The HRIS is not available right now. Please try again later."}
    mine = [r for r in rows if r["employee_id"] == principal.employee_id]
    audit(conn, "mcp", "live_read", user=principal.email, tool="get_leave_balance")
    conn.commit()
    return {
        "user": _user(principal),
        "source": "HRIS (live read)",
        "read_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "balances": [{**r, "remaining": r["entitled"] - r["taken"]} for r in mine],
    }


def get_asset(
    conn: psycopg.Connection, principal: Principal, asset_id: str, version: int | None = None
) -> dict:
    """One asset: the version pinned by the active release, or a specific version
    (to verify a past citation). Same status and permission checks as search."""
    release = active_release(conn, principal.context_repo)
    if release is None:
        return {"error": "No active release."}
    policy, params = sql_filter(principal)
    if version is None:
        scope = sql.SQL(
            "av.id IN (SELECT asset_version_id FROM release_assets "
            "WHERE release_id = %(release_id)s) AND av.asset_id = %(asset_id)s"
        )
    else:
        # An older version, to verify a past citation: only versions that a promoted
        # release of this Context Repo has served. Versions the gate rejected, or that
        # no release has published yet, are never served.
        scope = sql.SQL(
            "av.asset_id = %(asset_id)s AND av.version = %(version)s AND "
            "av.id IN (SELECT ra.asset_version_id FROM release_assets ra "
            "JOIN releases r ON r.id = ra.release_id "
            "WHERE r.context_repo = %(repo)s AND r.status = 'promoted')"
        )
    row = conn.execute(
        sql.SQL(
            "SELECT av.* FROM asset_versions av WHERE {scope} "
            "AND av.status NOT IN ('tombstoned', 'quarantined') AND {policy}"
        ).format(scope=scope, policy=policy),
        {
            **params,
            "release_id": release["id"],
            "repo": principal.context_repo,
            "asset_id": asset_id,
            "version": version,
        },
    ).fetchone()
    # Not found and not allowed look the same, so the caller learns nothing either way.
    if row is None or not cedar_allowed(principal, [row])[0]:
        return {"error": f"Asset {asset_id!r} not found."}
    audit(conn, "mcp", "get_asset", user=principal.email, asset=f"{asset_id}@v{row['version']}")
    conn.commit()
    return _to_result(conn, principal, release, row, date.today())


def context_info(conn: psycopg.Connection, principal: Principal) -> dict:
    """Active release and freshness per source, so the agent can warn about stale data."""
    release = active_release(conn, principal.context_repo)
    states = conn.execute("SELECT * FROM sync_state ORDER BY source_id").fetchall()
    return {
        "agent": principal.context_repo,
        "user": _user(principal),
        # The agent's instructions are part of the Context Repo, versioned with each release.
        "instructions": release["instructions"] if release else None,
        "release": None
        if release is None
        else {
            "number": release["number"],
            "promoted_at": release["created_at"].isoformat(timespec="seconds"),
        },
        "sources": [
            {
                "source": s["source_id"],
                "last_sync_at": s["last_sync_at"].isoformat(timespec="seconds")
                if s["last_sync_at"]
                else None,
                "frozen": s["frozen"],
            }
            for s in states
        ],
        "warnings": _freshness_warnings(conn, {s["source_id"] for s in states}),
    }
