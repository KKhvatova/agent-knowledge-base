"""Connector contract tests: run against copies of the fixtures, never the originals."""

import json
import shutil
from pathlib import Path

import pytest

from kb.connectors.file_sources import (
    ConfluenceExportConnector,
    DriveFolderConnector,
    HrisPolicyTableConnector,
)

SOURCES = Path(__file__).resolve().parents[1] / "data" / "sources"


@pytest.fixture
def sources(tmp_path: Path) -> Path:
    shutil.copytree(SOURCES, tmp_path / "sources")
    return tmp_path / "sources"


def test_confluence_full_sync_returns_every_page(sources: Path) -> None:
    conn = ConfluenceExportConnector(sources / "confluence-hr")
    records, cursor = conn.changes(None)
    assert len(records) == len(conn.list_ids()) == 16
    assert cursor == max(r.updated_at for r in records)


def test_confluence_incremental_sync_returns_only_newer_pages(sources: Path) -> None:
    conn = ConfluenceExportConnector(sources / "confluence-hr")
    _, cursor = conn.changes(None)
    assert conn.changes(cursor)[0] == []

    meta_path = sources / "confluence-hr" / "pages" / "2001.json"
    meta = json.loads(meta_path.read_text())
    meta["version"] = {"number": 7, "when": "2030-01-01T00:00:00Z"}
    meta_path.write_text(json.dumps(meta))

    changed, new_cursor = conn.changes(cursor)
    assert [r.external_id for r in changed] == ["2001"]
    assert new_cursor == "2030-01-01T00:00:00Z"


def test_confluence_keeps_raw_permissions_and_properties(sources: Path) -> None:
    record = ConfluenceExportConnector(sources / "confluence-hr").fetch("1006")
    assert record is not None
    assert record.acl_raw == {"read_groups": ["hr-it", "hr-global"]}
    assert record.metadata["classification"] == "confidential"


def test_deleted_page_disappears_from_list_ids_and_fetch(sources: Path) -> None:
    conn = ConfluenceExportConnector(sources / "confluence-hr")
    (sources / "confluence-hr" / "pages" / "3004.json").unlink()
    assert "3004" not in conn.list_ids()
    assert conn.fetch("3004") is None


def test_drive_uses_group_emails_as_permissions(sources: Path) -> None:
    conn = DriveFolderConnector(sources / "drive-benefits")
    records, _ = conn.changes(None)
    assert len(records) == 3
    it = conn.fetch("drv-it-welfare")
    assert it is not None and it.mime_type == "application/pdf"
    emails = [p["emailAddress"] for p in it.acl_raw["permissions"]]
    assert "staff-it@groups.example" in emails


def test_hris_policy_rows_are_records_but_balances_are_not(sources: Path) -> None:
    conn = HrisPolicyTableConnector(sources / "hris")
    assert conn.list_ids() == {"IT:vacation", "CA:vacation", "US:vacation"}
    us = conn.fetch("US:vacation")
    assert us is not None and b"Days per year: 15" in us.content
