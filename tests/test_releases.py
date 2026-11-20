"""Context Repo releases and what agents see through them (DESIGN.md §5.5, §7.1–7.3)."""

from __future__ import annotations

import json
from datetime import date

import pytest

from kb.authz.identity import principal_for
from kb.pipeline.sync import sync_source
from kb.retrieval import get_asset, search
from kb.store import releases
from kb.store.releases import ContextRepo

LIAM = "liam.chen@flipp.example"
GIULIA = "giulia.rossi@shopfully.example"


@pytest.fixture
def repo():
    return ContextRepo.load("hr-assistant")


def _sync(conn, sources, reconcile=False):
    for cfg in sources.values():
        sync_source(conn, cfg, reconcile=reconcile)


def _release(conn, repo):
    candidate = releases.build_candidate(conn, repo, "test")
    if candidate:
        releases.promote(conn, candidate, {"summary": "test"}, "test")
    conn.commit()
    return candidate


def _edit(sources, page_id, html=None, groups=None):
    pages = sources["confluence-hr"].path / "pages"
    meta = json.loads((pages / f"{page_id}.json").read_text())
    if html:
        (pages / f"{page_id}.html").write_text(html)
    if groups:
        meta["restrictions"]["read"]["groups"] = groups
    meta["version"] = {"number": meta["version"]["number"] + 1, "when": "2030-01-01T00:00:00Z"}
    (pages / f"{page_id}.json").write_text(json.dumps(meta))


def _texts(conn, email, query):
    result = search(conn, principal_for(email, "hr-assistant"), query, k=10)
    return " ".join(r["content"]["text"] for r in result["results"])


@pytest.fixture
def released(conn, sources, repo):
    _sync(conn, sources)
    _release(conn, repo)
    return conn


def test_no_new_release_when_nothing_changed(released, repo):
    assert releases.build_candidate(released, repo, "test") is None


def test_modification_is_visible_only_after_release(released, sources, repo):
    _edit(sources, "2001", html="<h2>Entitlement</h2><p>Employees receive 18 days of vacation.</p>")
    _sync(released, sources)
    assert "15 days" in _texts(released, LIAM, "vacation entitlement")  # still r1

    _release(released, repo)
    text = _texts(released, LIAM, "vacation entitlement")
    assert "18 days" in text and "15 days" not in text


def test_rollback_restores_previous_content(released, sources, repo):
    _edit(sources, "2001", html="<h2>Entitlement</h2><p>Employees receive 18 days of vacation.</p>")
    _sync(released, sources)
    _release(released, repo)

    target = releases.rollback(released, "hr-assistant", None, "test", reason="test")
    released.commit()
    assert target["number"] == 1
    assert "15 days" in _texts(released, LIAM, "vacation entitlement")


def test_diff_lists_changed_assets(released, sources, repo):
    _edit(sources, "2001", html="<h2>Entitlement</h2><p>Employees receive 18 days of vacation.</p>")
    _sync(released, sources)
    _release(released, repo)
    d = releases.diff(released, "hr-assistant", 1, 2)
    assert any("Entitlement (v1 → v2)" in c for c in d["changed"])
    assert any("Carry-over" in r for r in d["removed"])


def test_deletion_applies_immediately_even_if_pinned_version_is_older(released, sources):
    # Regression: v1 is pinned by r1; v2 exists but is not released; then the page is deleted.
    _edit(sources, "2001", html="<h2>Entitlement</h2><p>Employees receive 18 days of vacation.</p>")
    _sync(released, sources)
    pages = sources["confluence-hr"].path / "pages"
    (pages / "2001.json").unlink()
    (pages / "2001.html").unlink()
    _sync(released, sources, reconcile=True)
    text = _texts(released, LIAM, "vacation entitlement days")
    assert "15 days" not in text and "18 days" not in text


def test_permission_restriction_applies_immediately_to_pinned_versions(released, sources):
    # Regression: a restriction must also reach older versions still pinned by the release.
    _edit(
        sources,
        "2001",
        html="<h2>Entitlement</h2><p>Employees receive 18 days of vacation.</p>",
        groups=["hr-ca", "hr-global"],
    )
    _sync(released, sources)
    assert "15 days" not in _texts(released, LIAM, "vacation entitlement")


def test_future_policy_is_announced_and_served_on_its_date(released):
    giulia = principal_for(GIULIA, "hr-assistant")
    today = search(released, giulia, "smart working giorni da remoto", k=3)
    top = today["results"][0]
    assert "2 giorni" in top["content"]["text"]
    assert top["future_version"]["valid_from"] == "2027-01-01"

    january = search(
        released, giulia, "smart working giorni da remoto", k=3, as_of=date(2027, 1, 15)
    )
    assert "3 giorni" in january["results"][0]["content"]["text"]


def test_out_of_scope_departments_are_never_returned(released, repo, conn):
    # Pretend an engineering doc exists: it is not in the HR assistant's scope.
    conn.execute("UPDATE asset_versions SET department = 'engineering' WHERE external_id = '9002'")
    _release(conn, repo)
    assert "colleagues, customers and partners" not in _texts(conn, LIAM, "code of conduct")


def test_get_asset_never_serves_a_version_that_was_not_released(released, sources):
    _edit(sources, "2001", html="<h2>Entitlement</h2><p>99 days of paid vacation.</p>")
    _sync(released, sources)  # new version synced, but no release promoted it
    liam = principal_for(LIAM, "hr-assistant")
    asset_id = "confluence-hr:2001#entitlement"

    assert "15 days" in get_asset(released, liam, asset_id, version=1)["content"]["text"]
    assert "error" in get_asset(released, liam, asset_id, version=2)
