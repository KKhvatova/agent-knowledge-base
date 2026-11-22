"""The connector contract (DESIGN.md §2).

Every source, whatever its API or format, is wrapped by a connector that implements
the same three operations and emits the same record envelope. Nothing downstream of
the connectors knows which system a record came from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class SourceRecord:
    """The source record envelope: the only shape the rest of the system sees."""

    source_id: str  # e.g. "confluence-hr"
    external_id: str  # id in the source system
    source_version: str  # version, etag or revision as given by the source
    updated_at: str  # ISO-8601 timestamp of the last change in the source
    title: str
    url: str
    mime_type: str
    content: bytes
    # Permissions exactly as the source expresses them. They are translated later
    # (pipeline step T3), so a fixed mapping can be re-applied without calling the source.
    acl_raw: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)  # source-specific fields


class Connector(Protocol):
    """What every connector must implement."""

    source_id: str

    def changes(self, cursor: str | None) -> tuple[list[SourceRecord], str | None]:
        """Records changed since `cursor`, plus the new cursor (incremental polling)."""
        ...

    def fetch(self, external_id: str) -> SourceRecord | None:
        """One record by id, or None if it no longer exists (used for events)."""
        ...

    def list_ids(self) -> set[str]:
        """Every id currently in the source (used by reconciliation to find deletions)."""
        ...
