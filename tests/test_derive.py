"""LLM-derived Procedure assets: provenance, invalidation and approval (DESIGN.md §3, §5)."""

from __future__ import annotations

import json

import yaml

from kb.pipeline.derive import PROCEDURES_FILE, derive
from kb.pipeline.sync import sync_source
from kb.store import releases
from kb.store.releases import ContextRepo


def _sync(conn, sources):
    for cfg in sources.values():
        sync_source(conn, cfg, reconcile=True)


def _procedure(conn, pid):
    return conn.execute(
        "SELECT * FROM asset_versions WHERE asset_id = %s ORDER BY version DESC LIMIT 1", (pid,)
    ).fetchone()


def _pinned_ids(conn, release):
    rows = conn.execute(
        "SELECT av.asset_id FROM release_assets ra JOIN asset_versions av "
        "ON av.id = ra.asset_version_id WHERE ra.release_id = %s",
        (release["id"],),
    ).fetchall()
    return {r["asset_id"] for r in rows}


def test_procedures_are_published_with_provenance_and_source_permissions(conn, sources):
    _sync(conn, sources)
    stats = derive(conn)
    assert stats.published == 4 and not stats.needs_regeneration

    proc = _procedure(conn, "procedure:request-vacation-ca")
    raw = conn.execute("SELECT current_raw_id FROM records WHERE external_id = '2001'").fetchone()
    assert proc["type"] == "procedure" and proc["provenance"] == [raw["current_raw_id"]]
    assert (proc["audience"], proc["country"]) == ("staff", "CA")  # inherited, never wider

    assert derive(conn).unchanged == 4  # idempotent


def test_source_change_makes_procedure_stale_and_drops_it_from_next_release(conn, sources):
    _sync(conn, sources)
    derive(conn)
    repo = ContextRepo.load("hr-assistant")
    first = releases.build_candidate(conn, repo, "test")
    releases.promote(conn, first, {"summary": "t"}, "test")
    assert "procedure:request-vacation-ca" in _pinned_ids(conn, first)

    pages = sources["confluence-hr"].path / "pages"
    (pages / "2001.html").write_text("<h2>Entitlement</h2><p>18 days of paid vacation.</p>")
    meta = json.loads((pages / "2001.json").read_text())
    meta["version"] = {"number": 9, "when": "2030-01-01T00:00:00Z"}
    (pages / "2001.json").write_text(json.dumps(meta))
    _sync(conn, sources)

    assert _procedure(conn, "procedure:request-vacation-ca")["status"] == "stale"
    assert derive(conn).needs_regeneration == ["procedure:request-vacation-ca"]
    second = releases.build_candidate(conn, repo, "test")
    assert "procedure:request-vacation-ca" not in _pinned_ids(conn, second)


def test_derived_confidential_asset_needs_owner_approval(conn, sources, tmp_path):
    _sync(conn, sources)
    page = (sources["confluence-hr"].path / "pages" / "1006.html").read_bytes()
    import hashlib

    fixture = tmp_path / "procedures.yaml"
    fixture.write_text(
        yaml.safe_dump(
            [
                {
                    "id": "procedure:disciplinary-it",
                    "title": "Disciplinary steps",
                    "language": "it",
                    "steps": "1. Contestazione scritta. 2. 5 giorni per le giustificazioni.",
                    "derived_from": [
                        {
                            "record": "confluence-hr:1006",
                            "body_sha256": hashlib.sha256(page).hexdigest(),
                        }
                    ],
                }
            ]
        )
    )
    derive(conn, fixture)
    repo = ContextRepo.load("hr-assistant")

    candidate = releases.build_candidate(conn, repo, "test")
    assert "procedure:disciplinary-it" not in _pinned_ids(conn, candidate)

    conn.execute(
        "UPDATE asset_versions SET approved_by = 'hr-it-team@shopfully.example' "
        "WHERE asset_id = 'procedure:disciplinary-it'"
    )
    releases.promote(conn, candidate, {"summary": "t"}, "test")
    approved = releases.build_candidate(conn, repo, "test")
    assert "procedure:disciplinary-it" in _pinned_ids(conn, approved)


def test_procedure_fixtures_match_committed_source_pages():
    """Fails if a source fixture is edited without regenerating the procedures
    (uv run python scripts/derive_procedures.py)."""
    import hashlib

    from kb.config import FIXTURES_DIR

    for proc in yaml.safe_load(PROCEDURES_FILE.read_text()):
        for src in proc["derived_from"]:
            page = src["record"].split(":", 1)[1]
            html = (FIXTURES_DIR / "confluence-hr" / "pages" / f"{page}.html").read_bytes()
            assert hashlib.sha256(html).hexdigest() == src["body_sha256"], proc["id"]
