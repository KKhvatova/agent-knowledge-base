"""Database access helpers."""

from __future__ import annotations

from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from kb.config import DATABASE_URL

SCHEMA = Path(__file__).with_name("schema.sql")


def connect(url: str = DATABASE_URL) -> psycopg.Connection:
    conn = psycopg.connect(url, row_factory=dict_row, autocommit=False)
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    register_vector(conn)
    return conn


def init_schema(conn: psycopg.Connection) -> None:
    conn.execute(SCHEMA.read_text())
    conn.commit()


def reset_schema(conn: psycopg.Connection) -> None:
    """Drop everything. Used by tests and `kb reset`."""
    conn.execute(
        "DROP TABLE IF EXISTS incidents, audit_log, sync_state, release_assets, releases, "
        "context_repos, asset_facts, asset_versions, records, raw_versions CASCADE"
    )
    conn.commit()
    init_schema(conn)


def audit(conn: psycopg.Connection, actor: str, action: str, **details) -> None:
    conn.execute(
        "INSERT INTO audit_log (actor, action, details) VALUES (%s, %s, %s)",
        (actor, action, Jsonb(details)),
    )
