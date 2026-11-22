"""Connectors for the POC's file-based sources.

POC simplification (stated in the README): the sources are folders under
data/sources/ that imitate the export format of each real system, instead of live
APIs. Each connector still implements the full contract of `base.Connector`, so the
pipeline, the cursors and the reconciliation behave as they would against real APIs.
"""

from __future__ import annotations

import json
from pathlib import Path

from kb.connectors.base import SourceRecord


def _max_cursor(records: list[SourceRecord], cursor: str | None) -> str | None:
    stamps = [r.updated_at for r in records]
    if cursor:
        stamps.append(cursor)
    return max(stamps) if stamps else None


class ConfluenceExportConnector:
    """Confluence-like export: pages/<id>.html (body) + pages/<id>.json (metadata).

    Permissions are Confluence read restrictions: a list of group names.
    """

    def __init__(self, root: Path, source_id: str = "confluence-hr") -> None:
        self.root = root
        self.source_id = source_id

    def _load(self, page_id: str) -> SourceRecord | None:
        meta_path = self.root / "pages" / f"{page_id}.json"
        body_path = self.root / "pages" / f"{page_id}.html"
        if not meta_path.exists() or not body_path.exists():
            return None
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return SourceRecord(
            source_id=self.source_id,
            external_id=meta["id"],
            source_version=str(meta["version"]["number"]),
            updated_at=meta["version"]["when"],
            title=meta["title"],
            url=meta["_links"]["webui"],
            mime_type="text/html",
            content=body_path.read_bytes(),
            acl_raw={"read_groups": meta["restrictions"]["read"]["groups"]},
            metadata={
                "space": meta["space"]["key"],
                "labels": meta["metadata"]["labels"],
                "language": meta["metadata"]["language"],
                **meta["metadata"].get("properties", {}),
            },
        )

    def list_ids(self) -> set[str]:
        return {p.stem for p in (self.root / "pages").glob("*.json")}

    def fetch(self, external_id: str) -> SourceRecord | None:
        return self._load(external_id)

    def changes(self, cursor: str | None) -> tuple[list[SourceRecord], str | None]:
        records = [r for i in sorted(self.list_ids()) if (r := self._load(i))]
        changed = [r for r in records if cursor is None or r.updated_at > cursor]
        return changed, _max_cursor(changed, cursor)


class DriveFolderConnector:
    """Drive-like folder: files/<name> + index.json with Drive-style permissions.

    Permissions are a list of {type, emailAddress, role}: group e-mail addresses,
    a different model from Confluence's group names.
    """

    def __init__(self, root: Path, source_id: str = "drive-benefits") -> None:
        self.root = root
        self.source_id = source_id

    def _index(self) -> dict[str, dict]:
        entries = json.loads((self.root / "index.json").read_text(encoding="utf-8"))
        return {e["id"]: e for e in entries}

    def _to_record(self, entry: dict) -> SourceRecord | None:
        path = self.root / "files" / entry["name"]
        if not path.exists():
            return None
        return SourceRecord(
            source_id=self.source_id,
            external_id=entry["id"],
            source_version=entry["modifiedTime"],
            updated_at=entry["modifiedTime"],
            title=Path(entry["name"]).stem,
            url=entry["webViewLink"],
            mime_type=entry["mimeType"],
            content=path.read_bytes(),
            acl_raw={"permissions": entry["permissions"]},
            metadata={"language": entry.get("properties", {}).get("language", "en")},
        )

    def list_ids(self) -> set[str]:
        return set(self._index())

    def fetch(self, external_id: str) -> SourceRecord | None:
        entry = self._index().get(external_id)
        return self._to_record(entry) if entry else None

    def changes(self, cursor: str | None) -> tuple[list[SourceRecord], str | None]:
        records = [r for e in self._index().values() if (r := self._to_record(e))]
        changed = [r for r in records if cursor is None or r.updated_at > cursor]
        return changed, _max_cursor(changed, cursor)


class HrisPolicyTableConnector:
    """HRIS leave-policy table: one record per (country, leave type).

    This table is non-personal and rarely changes, so it is *indexed* as knowledge
    (DESIGN §1). Personal data such as leave balances is never indexed: it is read
    live by the `get_leave_balance` tool instead.
    """

    def __init__(self, root: Path, source_id: str = "hris-policies") -> None:
        self.root = root
        self.source_id = source_id

    def _rows(self) -> dict[str, dict]:
        rows = json.loads((self.root / "leave_policy_table.json").read_text(encoding="utf-8"))
        return {f"{r['country']}:{r['leave_type']}": r for r in rows}

    def _to_record(self, key: str, row: dict) -> SourceRecord:
        text = (
            f"Leave policy table (HRIS). Country: {row['country']}. "
            f"Leave type: {row['leave_type']}. "
            f"Days per year: {row['days_per_year']}. "
            f"Effective from: {row['effective_from']}."
        )
        return SourceRecord(
            source_id=self.source_id,
            external_id=key,
            source_version=row["updated_at"],
            updated_at=row["updated_at"],
            title=f"HRIS leave policy – {row['country']} – {row['leave_type']}",
            url=f"https://hris.example/policies/{key}",
            mime_type="application/json",
            content=json.dumps({"text": text, "row": row}).encode(),
            # HRIS security: policy rows are visible to everyone employed in that country.
            acl_raw={"visible_to_country": row["country"]},
            metadata={"language": "en", "valid_from": row["effective_from"]},
        )

    def list_ids(self) -> set[str]:
        return set(self._rows())

    def fetch(self, external_id: str) -> SourceRecord | None:
        row = self._rows().get(external_id)
        return self._to_record(external_id, row) if row else None

    def changes(self, cursor: str | None) -> tuple[list[SourceRecord], str | None]:
        records = [self._to_record(k, r) for k, r in self._rows().items()]
        changed = [r for r in records if cursor is None or r.updated_at > cursor]
        return changed, _max_cursor(changed, cursor)
