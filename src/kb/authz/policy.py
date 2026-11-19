"""Authorization, enforced in two steps (DESIGN.md §6.4).

1. Before ranking: the policies are translated into a SQL filter, applied INSIDE the
   search query, so unauthorized assets are never scored.
2. After retrieval: every returned asset is checked again with Cedar. If the two steps
   ever disagree, the asset is dropped and the mismatch is reported, because it means
   the SQL translation has a bug.

`tests/test_authz.py` checks that the SQL filter and Cedar agree for every user and
every asset in the store.
"""

from __future__ import annotations

from functools import lru_cache

import cedarpy
from psycopg import sql

from kb.authz.identity import Principal
from kb.config import ROOT

POLICY_FILE = ROOT / "policies" / "kb.cedar"


@lru_cache
def _policies() -> str:
    return POLICY_FILE.read_text()


def sql_filter(principal: Principal, alias: str = "av") -> tuple[sql.Composed, dict]:
    """The SQL translation of policies/kb.cedar for one principal.

    Keep in sync with the Cedar file; the matrix test fails if they drift apart.
    """
    a = sql.Identifier(alias)
    query = sql.SQL(
        "("
        "  {a}.audience = 'public'"
        "  OR ({a}.audience = 'staff' AND {a}.country IN ('GLOBAL', %(p_country)s))"
        "  OR (%(p_role)s = 'hr' AND {a}.audience IN ('staff', 'hr')"
        "      AND (%(p_scope)s = 'global' OR {a}.country IN ('GLOBAL', %(p_country)s)))"
        "  OR ({a}.audience = 'owner_only' AND {a}.owner = %(p_email)s)"
        ")"
        " AND NOT ({a}.classification = 'confidential' AND %(p_role)s <> 'hr')"
        " AND {a}.classification <> 'restricted'"
    ).format(a=a)
    params = {
        "p_country": principal.country,
        "p_role": principal.role,
        "p_scope": principal.hr_scope,
        "p_email": principal.email,
    }
    return query, params


def _asset_entity(asset: dict) -> dict:
    return {
        "uid": {"__entity": {"type": "Asset", "id": str(asset["id"])}},
        "attrs": {
            "audience": asset["audience"],
            "country": asset["country"],
            "classification": asset["classification"],
            "owner": asset["owner"],
        },
        "parents": [],
    }


def cedar_allowed(principal: Principal, assets: list[dict]) -> list[bool]:
    """Explicit Cedar decision for each asset (step 2)."""
    if not assets:
        return []
    entities = [principal.cedar_entity()] + [_asset_entity(a) for a in assets]
    requests = [
        {
            "principal": f'Employee::"{principal.employee_id}"',
            "action": 'Action::"read"',
            "resource": f'Asset::"{a["id"]}"',
            "context": {},
        }
        for a in assets
    ]
    results = cedarpy.is_authorized_batch(requests, _policies(), entities)
    return [r.allowed for r in results]
