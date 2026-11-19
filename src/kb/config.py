"""Settings and source configuration."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from kb.connectors.base import Connector
from kb.connectors.file_sources import (
    ConfluenceExportConnector,
    DriveFolderConnector,
    HrisPolicyTableConnector,
)

ROOT = Path(__file__).resolve().parents[2]

DATABASE_URL = os.environ.get("KB_DATABASE_URL", "postgresql://kb:kb@localhost:5433/kb")
EMBEDDING_MODEL = os.environ.get(
    "KB_EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)
FIXTURES_DIR = ROOT / "data" / "sources"
# The system reads a working copy of the sources, so demos can edit and delete
# documents without touching the committed fixtures. `kb reset` recreates it.
SOURCES_DIR = Path(os.environ.get("KB_SOURCES_DIR", ROOT / ".data" / "sources"))
EVENTS_FILE = SOURCES_DIR / "events.jsonl"


def ensure_working_copy(fresh: bool = False) -> Path:
    if fresh and SOURCES_DIR.exists():
        shutil.rmtree(SOURCES_DIR)
    if not SOURCES_DIR.exists():
        shutil.copytree(FIXTURES_DIR, SOURCES_DIR)
    return SOURCES_DIR


_CONNECTORS = {
    "confluence_export": ConfluenceExportConnector,
    "drive_folder": DriveFolderConnector,
    "hris_policy_table": HrisPolicyTableConnector,
}


@dataclass(frozen=True)
class SourceConfig:
    source_id: str
    connector: str
    path: Path
    department: str
    owners: dict[str, str]
    freshness_target_minutes: int
    max_delete_ratio: float
    permission_mapping: dict

    def owner_for(self, metadata: dict) -> str:
        """Owner by Confluence space if configured, otherwise the source default."""
        return self.owners.get(metadata.get("space", ""), self.owners.get("default", "unknown"))

    def build_connector(self) -> Connector:
        return _CONNECTORS[self.connector](self.path, source_id=self.source_id)


@lru_cache
def load_sources(path: Path = ROOT / "config" / "sources.yaml") -> dict[str, SourceConfig]:
    raw = yaml.safe_load(path.read_text())["sources"]
    return {
        sid: SourceConfig(
            source_id=sid,
            connector=cfg["connector"],
            path=SOURCES_DIR / cfg["path"],
            department=cfg["department"],
            owners=cfg["owners"],
            freshness_target_minutes=cfg["freshness_target_minutes"],
            max_delete_ratio=cfg["circuit_breaker"]["max_delete_ratio"],
            permission_mapping=cfg["permission_mapping"],
        )
        for sid, cfg in raw.items()
    }
