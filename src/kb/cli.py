"""`kb` command-line interface."""

from __future__ import annotations

import typer

from kb.config import load_sources
from kb.pipeline.sync import sync_source
from kb.store.db import connect, init_schema, reset_schema

app = typer.Typer(help="Agent Knowledge Base POC", no_args_is_help=True)


@app.callback()
def main() -> None:
    """Agent Knowledge Base POC."""


@app.command()
def version() -> None:
    """Print the POC version."""
    typer.echo("agent-knowledge-base 0.1.0")


@app.command()
def init() -> None:
    """Create the database schema (idempotent)."""
    with connect() as conn:
        init_schema(conn)
    typer.echo("Schema ready.")


@app.command()
def reset(yes: bool = typer.Option(False, "--yes", help="Confirm deleting all data.")) -> None:
    """Drop and recreate all tables."""
    if not yes:
        typer.confirm("This deletes everything in the knowledge store. Continue?", abort=True)
    with connect() as conn:
        reset_schema(conn)
    typer.echo("Knowledge store reset.")


@app.command()
def sync(
    source: str = typer.Option(None, help="Sync only this source id."),
    reconcile: bool = typer.Option(False, help="Also run full reconciliation (deletions)."),
) -> None:
    """Sync sources: events, then incremental polling, then (optionally) reconciliation."""
    sources = load_sources()
    selected = [sources[source]] if source else list(sources.values())
    with connect() as conn:
        init_schema(conn)
        for cfg in selected:
            s = sync_source(conn, cfg, reconcile=reconcile)
            typer.echo(
                f"{s.source_id:16} created={s.created} updated={s.updated} "
                f"permission_only={s.permission_only} unchanged={s.unchanged} "
                f"deleted={s.deleted}{'  FROZEN' if s.frozen else ''}"
            )
            for message in s.messages:
                typer.echo(f"  - {message}")


@app.command()
def status() -> None:
    """Show records and assets per source, and the sync state."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT s.source_id, s.frozen, s.last_sync_at, "
            "(SELECT count(*) FROM records r WHERE r.source_id = s.source_id "
            " AND r.status = 'active') AS records, "
            "(SELECT count(*) FROM asset_versions a WHERE a.source_id = s.source_id "
            " AND a.status = 'active') AS assets "
            "FROM sync_state s ORDER BY s.source_id"
        ).fetchall()
    for r in rows:
        typer.echo(
            f"{r['source_id']:16} records={r['records']:3} active_assets={r['assets']:3} "
            f"frozen={r['frozen']} last_sync={r['last_sync_at']:%Y-%m-%d %H:%M:%S}"
        )


if __name__ == "__main__":
    app()
