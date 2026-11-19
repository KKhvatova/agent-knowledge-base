"""Shared fixtures. Integration tests use a separate `kb_test` database and copies of
the source fixtures, so they never touch the development data."""

from __future__ import annotations

import dataclasses
import shutil
from pathlib import Path

import psycopg
import pytest

from kb.config import load_sources
from kb.store import db

ROOT = Path(__file__).resolve().parents[1]
ADMIN_URL = "postgresql://kb:kb@localhost:5433/kb"
TEST_URL = "postgresql://kb:kb@localhost:5433/kb_test"


@pytest.fixture(scope="session")
def _test_database() -> str:
    try:
        with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
            exists = admin.execute("SELECT 1 FROM pg_database WHERE datname = 'kb_test'").fetchone()
            if not exists:
                admin.execute("CREATE DATABASE kb_test")
    except psycopg.OperationalError:
        pytest.skip("Postgres is not running (docker compose up -d postgres)")
    return TEST_URL


@pytest.fixture
def conn(_test_database: str):
    connection = db.connect(_test_database)
    db.reset_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Source configs pointing at a temporary copy of data/sources/."""
    copy = tmp_path / "sources"
    shutil.copytree(ROOT / "data" / "sources", copy)
    monkeypatch.setattr("kb.pipeline.sync.EVENTS_FILE", copy / "events.jsonl")
    return {
        sid: dataclasses.replace(cfg, path=copy / cfg.path.name)
        for sid, cfg in load_sources().items()
    }
