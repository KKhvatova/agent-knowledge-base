"""Authorization: the SQL filter must make exactly the same decisions as the Cedar
policies, for every user and every asset (DESIGN.md §4)."""

from __future__ import annotations

import itertools
import json

import pytest
from psycopg import sql

from kb.authz.identity import issue_token, principal_for, verify_token
from kb.authz.policy import cedar_allowed, sql_filter
from kb.config import ROOT
from kb.pipeline.sync import sync_source

EMAILS = [e["email"] for e in json.loads((ROOT / "data/sources/hris/employees.json").read_text())]


@pytest.fixture
def synced(conn, sources):
    for cfg in sources.values():
        sync_source(conn, cfg, reconcile=False)
    # Every combination of the attributes the policies look at, not only the fixtures.
    combinations = itertools.product(
        ["public", "staff", "hr", "hr_global", "owner_only"],
        ["IT", "CA", "US", "GLOBAL"],
        ["public", "internal", "confidential", "restricted"],
        ["giulia.rossi@shopfully.example", "nobody@example"],
    )
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO asset_versions (asset_id, version, type, source_id, external_id, "
            "provenance, title, section_path, content, url, language, owner, department, "
            "country, audience, classification, last_synced, source_updated_at) VALUES "
            "(%s, 1, 'knowledge', 'x', %s, '{}', 't', 't', 't', 'u', 'en', %s, 'people', "
            "%s, %s, %s, now(), now())",
            [
                (f"edge:{n}", str(n), owner, country, audience, classification)
                for n, (audience, country, classification, owner) in enumerate(combinations)
            ],
        )
    return conn.execute("SELECT * FROM asset_versions ORDER BY id").fetchall()


@pytest.mark.parametrize("email", EMAILS)
def test_sql_filter_matches_cedar_for_every_asset(conn, synced, email):
    principal = principal_for(email, "hr-assistant")
    where, params = sql_filter(principal)
    by_sql = {
        r["id"]
        for r in conn.execute(
            sql.SQL("SELECT av.id FROM asset_versions av WHERE {w}").format(w=where), params
        ).fetchall()
    }
    by_cedar = {
        a["id"] for a, ok in zip(synced, cedar_allowed(principal, synced), strict=False) if ok
    }
    assert by_sql == by_cedar


def _readable_titles(conn, synced, email):
    principal = principal_for(email, "hr-assistant")
    return {
        a["title"] for a, ok in zip(synced, cedar_allowed(principal, synced), strict=False) if ok
    }


def test_employee_sees_own_country_and_global_only(conn, synced):
    titles = _readable_titles(conn, synced, "liam.chen@flipp.example")
    assert "Vacation and Time Off – Canada" in titles
    assert "Travel and Expenses Policy" in titles
    assert "Ferie e permessi – Italia" not in titles
    assert "Paid Time Off – United States" not in titles


def test_confidential_is_hr_only(conn, synced):
    assert "Procedura disciplinare – Italia" not in _readable_titles(
        conn, synced, "giulia.rossi@shopfully.example"
    )
    assert "Procedura disciplinare – Italia" in _readable_titles(
        conn, synced, "marco.bianchi@shopfully.example"
    )  # global HR


def test_country_hr_does_not_see_other_countries_hr_content(conn, synced):
    # Ethan is HR for Canada only.
    assert "Procedura disciplinare – Italia" not in _readable_titles(
        conn, synced, "ethan.roy@flipp.example"
    )


def test_owner_only_fallback_is_visible_to_owner_only(conn, synced):
    def sees(email):
        principal = principal_for(email, "hr-assistant")
        owner_asset = [
            a
            for a in synced
            if (a["audience"], a["classification"], a["owner"])
            == ("owner_only", "internal", "giulia.rossi@shopfully.example")
        ][:1]
        return cedar_allowed(principal, owner_asset)[0]

    assert sees("giulia.rossi@shopfully.example")
    assert not sees("marco.bianchi@shopfully.example")  # not even global HR


def test_page_shared_only_with_global_hr_is_for_global_hr_only(conn, sources):
    pages = sources["confluence-hr"].path / "pages"
    meta = json.loads((pages / "1006.json").read_text())
    meta["restrictions"]["read"]["groups"] = ["hr-global"]
    (pages / "1006.json").write_text(json.dumps(meta))
    for cfg in sources.values():
        sync_source(conn, cfg, reconcile=False)
    assets = conn.execute("SELECT * FROM asset_versions WHERE external_id = '1006'").fetchall()

    def sees(email):
        return any(cedar_allowed(principal_for(email, "hr-assistant"), assets))

    assert sees("marco.bianchi@shopfully.example")  # HR, global scope
    assert not sees("ethan.roy@flipp.example")  # HR, Canada only


def test_token_round_trip_binds_user_and_agent():
    principal = verify_token(issue_token("liam.chen@flipp.example", "hr-assistant"))
    assert (principal.country, principal.context_repo) == ("CA", "hr-assistant")


def test_unknown_user_cannot_get_a_token():
    with pytest.raises(PermissionError):
        issue_token("mallory@evil.example", "hr-assistant")
