"""Self-maintenance: additions, modifications, permission changes and deletions
propagate without manual intervention, and anomalies trip the circuit breaker
(DESIGN.md §2, §5)."""

from __future__ import annotations

import json
from pathlib import Path

from kb.pipeline.sync import sync_source


def _sync_all(conn, sources, reconcile=False):
    return {sid: sync_source(conn, cfg, reconcile=reconcile) for sid, cfg in sources.items()}


def _active(conn, external_id):
    return conn.execute(
        "SELECT * FROM asset_versions WHERE external_id = %s AND status = 'active' ORDER BY id",
        (external_id,),
    ).fetchall()


def _edit_page(
    sources, page_id, *, html=None, groups=None, props=None, when="2030-01-01T00:00:00Z"
):
    pages: Path = sources["confluence-hr"].path / "pages"
    meta = json.loads((pages / f"{page_id}.json").read_text())
    if html is not None:
        (pages / f"{page_id}.html").write_text(html)
    if groups is not None:
        meta["restrictions"]["read"]["groups"] = groups
    if props is not None:
        meta["metadata"].setdefault("properties", {}).update(props)
    meta["version"] = {"number": meta["version"]["number"] + 1, "when": when}
    (pages / f"{page_id}.json").write_text(json.dumps(meta))


def test_initial_sync_then_nothing_to_do(conn, sources):
    first = _sync_all(conn, sources)
    assert first["confluence-hr"].created == 16
    second = _sync_all(conn, sources)
    assert all(s.created == s.updated == 0 for s in second.values())


def test_modification_creates_new_versions_and_supersedes_old(conn, sources):
    _sync_all(conn, sources)
    _edit_page(
        sources,
        "2001",
        html="<h1>Vacation</h1><h2>Entitlement</h2>"
        "<p>Full-time employees receive <strong>18 days</strong> of paid vacation.</p>",
    )
    stats = _sync_all(conn, sources)

    assert stats["confluence-hr"].updated == 1
    active = _active(conn, "2001")
    assert [a["section_path"] for a in active] == ["Vacation and Time Off – Canada › Entitlement"]
    assert "18 days" in active[0]["content"] and active[0]["version"] == 2
    superseded = conn.execute(
        "SELECT count(*) AS n FROM asset_versions WHERE external_id = '2001' "
        "AND status = 'superseded'"
    ).fetchone()["n"]
    assert superseded == 3  # old versions are kept for audit and rollback, never overwritten


def test_permission_only_change_takes_fast_path(conn, sources):
    _sync_all(conn, sources)
    ids_before = [a["id"] for a in _active(conn, "1001")]
    _edit_page(sources, "1001", groups=["hr-it", "hr-global"])  # restrict to HR only

    stats = _sync_all(conn, sources)
    assert stats["confluence-hr"].permission_only == 1
    active = _active(conn, "1001")
    assert [a["id"] for a in active] == ids_before  # same rows: no re-parse, no re-embed
    assert {a["audience"] for a in active} == {"hr"}


def test_classification_change_applies_immediately_like_a_permission_change(conn, sources):
    _sync_all(conn, sources)
    ids_before = [a["id"] for a in _active(conn, "1001")]
    _edit_page(sources, "1001", props={"classification": "confidential"})  # text unchanged

    stats = _sync_all(conn, sources)
    assert stats["confluence-hr"].permission_only == 1
    active = _active(conn, "1001")
    assert [a["id"] for a in active] == ids_before
    assert {a["classification"] for a in active} == {"confidential"}


def test_metadata_change_creates_new_versions(conn, sources):
    _sync_all(conn, sources)
    _edit_page(sources, "1004", props={"valid_to": "2026-06-30"})  # text unchanged

    stats = _sync_all(conn, sources)
    assert stats["confluence-hr"].updated == 1
    assert {str(a["valid_to"]) for a in _active(conn, "1004")} == {"2026-06-30"}


def test_permission_change_to_another_country_moves_its_facts(conn, sources):
    _sync_all(conn, sources)
    _edit_page(sources, "2001", groups=["staff-us", "hr-us"])  # CA page now for the US

    assert _sync_all(conn, sources)["confluence-hr"].permission_only == 1
    keys = {
        r["fact_key"]
        for r in conn.execute(
            "SELECT f.fact_key FROM asset_facts f JOIN asset_versions av "
            "ON av.id = f.asset_version_id WHERE av.external_id = '2001'"
        ).fetchall()
    }
    assert keys == {"vacation_days_per_year:US"}


def test_unmapped_permission_falls_back_to_owner_only(conn, sources):
    _sync_all(conn, sources)
    _edit_page(sources, "2004", groups=["staff-ca", "contractors-toronto"])
    _sync_all(conn, sources)
    active = _active(conn, "2004")
    assert {a["audience"] for a in active} == {"owner_only"}
    assert "contractors-toronto" in active[0]["mapping_note"]


def test_deletion_is_found_by_reconciliation_only(conn, sources):
    _sync_all(conn, sources)
    pages = sources["confluence-hr"].path / "pages"
    (pages / "3004.json").unlink()
    (pages / "3004.html").unlink()

    polled = _sync_all(conn, sources)  # polling cannot see a deletion
    assert polled["confluence-hr"].deleted == 0
    assert _active(conn, "3004")

    reconciled = _sync_all(conn, sources, reconcile=True)
    assert reconciled["confluence-hr"].deleted == 1
    assert _active(conn, "3004") == []
    record = conn.execute("SELECT status FROM records WHERE external_id = '3004'").fetchone()
    assert record["status"] == "tombstoned"


def test_deletion_event_is_applied_without_reconciliation(conn, sources):
    _sync_all(conn, sources)
    pages = sources["confluence-hr"].path / "pages"
    (pages / "9002.json").unlink()
    (pages / "9002.html").unlink()
    events = sources["confluence-hr"].path.parent / "events.jsonl"
    events.write_text(
        json.dumps({"source_id": "confluence-hr", "external_id": "9002", "type": "delete"}) + "\n"
    )

    stats = _sync_all(conn, sources)
    assert stats["confluence-hr"].deleted == 1
    assert _active(conn, "9002") == []


def test_mass_deletion_trips_circuit_breaker(conn, sources):
    _sync_all(conn, sources)
    pages = sources["confluence-hr"].path / "pages"
    for meta in sorted(pages.glob("*.json"))[:10]:  # 10 of 16 pages vanish
        meta.unlink()

    stats = _sync_all(conn, sources, reconcile=True)
    assert stats["confluence-hr"].frozen
    assert stats["confluence-hr"].deleted == 0  # nothing applied
    assert (
        len(
            conn.execute(
                "SELECT * FROM records WHERE source_id = 'confluence-hr' AND status = 'active'"
            ).fetchall()
        )
        == 16
    )
    # A frozen source stays skipped: unfreezing is a human decision (manual in the POC).
    assert _sync_all(conn, sources)["confluence-hr"].frozen
