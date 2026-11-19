"""`kb` command-line interface."""

from __future__ import annotations

import typer

from kb.authz.identity import issue_token
from kb.config import load_sources
from kb.pipeline.sync import sync_source
from kb.store import releases
from kb.store.db import connect, init_schema, reset_schema
from kb.store.releases import ContextRepo

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


repo_app = typer.Typer(help="Inspect and roll back Context Repo releases.", no_args_is_help=True)
app.add_typer(repo_app, name="repo")


@app.command()
def release(repo: str = "hr-assistant") -> None:
    """Build a candidate release of a Context Repo and promote it.

    Evaluation gate: added in the next step; until then candidates are promoted directly.
    """
    with connect() as conn:
        candidate = releases.build_candidate(conn, ContextRepo.load(repo), created_by="cli")
        if candidate is None:
            typer.echo("Nothing changed since the active release.")
            return
        releases.promote(conn, candidate, {"summary": "no eval gate yet"}, actor="cli")
        conn.commit()
        typer.echo(f"{repo}: release r{candidate['number']} promoted.")


@repo_app.command("log")
def repo_log(repo: str = "hr-assistant") -> None:
    """List releases of a Context Repo."""
    with connect() as conn:
        for r in releases.log(conn, repo):
            marker = "*" if r["active"] else " "
            typer.echo(
                f"{marker} r{r['number']:<3} {r['status']:12} {r['assets']:3} assets  "
                f"{r['created_at']:%Y-%m-%d %H:%M}  by {r['created_by']}  evals: {r['evals']}"
            )


@repo_app.command("diff")
def repo_diff(a: int, b: int, repo: str = "hr-assistant") -> None:
    """Show assets added, removed and changed between two releases."""
    with connect() as conn:
        d = releases.diff(conn, repo, a, b)
    for kind, items in d.items():
        typer.echo(f"{kind} ({len(items)}):")
        for item in items:
            typer.echo(f"  {item}")


@repo_app.command("rollback")
def repo_rollback(
    to: int = typer.Option(None, help="Release number; default: the previous one."),
    repo: str = "hr-assistant",
    reason: str = "manual rollback",
) -> None:
    """Move the active pointer back to an earlier release."""
    with connect() as conn:
        target = releases.rollback(conn, repo, to, actor="cli", reason=reason)
        conn.commit()
    typer.echo(f"{repo}: active release is now r{target['number']}.")


@app.command()
def token(email: str, agent: str = "hr-assistant") -> None:
    """Issue a demo token for a user, bound to an agent (Context Repo)."""
    typer.echo(issue_token(email, agent))


@app.command()
def serve() -> None:
    """Run the MCP server (HTTP on 127.0.0.1:8765, or stdio with KB_MCP_TRANSPORT=stdio)."""
    from kb.mcp_server.server import main as serve_main

    serve_main()


if __name__ == "__main__":
    app()
