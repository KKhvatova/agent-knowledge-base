"""`kb` command-line interface."""

from __future__ import annotations

import typer

from kb.authz.identity import issue_token
from kb.config import ensure_working_copy, load_sources
from kb.pipeline.sync import sync_source
from kb.store import releases
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
    ensure_working_copy(fresh=True)
    typer.echo("Knowledge store reset; sources working copy recreated from data/sources/.")


@app.command()
def sync(
    source: str = typer.Option(None, help="Sync only this source id."),
    reconcile: bool = typer.Option(False, help="Also run full reconciliation (deletions)."),
) -> None:
    """Sync sources: events, then incremental polling, then (optionally) reconciliation."""
    ensure_working_copy()
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
def release(
    repo: str = "hr-assistant",
    force: bool = typer.Option(False, help="Emergency: promote even if evals fail (audited)."),
) -> None:
    """Build a candidate release, run the eval suite, promote only if it passes."""
    from kb.evals.gate import release_with_gate

    with connect() as conn:
        outcome = release_with_gate(conn, repo, actor="cli", force=force)
    if outcome.status == "unchanged":
        typer.echo("Nothing changed since the active release.")
        return
    number = outcome.release["number"]
    typer.echo(f"{repo}: candidate r{number} -> {outcome.status.upper()}")
    typer.echo(f"  evals: {outcome.report.summary}")
    for failure in outcome.report.failures:
        typer.echo(f"  - {failure}")
    if outcome.status == "rejected":
        typer.echo("  The active release is unchanged; agents keep seeing the last good one.")


@app.command("eval")
def eval_cmd(repo: str = "hr-assistant") -> None:
    """Run the eval suite against the active release."""
    from kb.evals.runner import run_suite

    with connect() as conn:
        active = releases.active_release(conn, repo)
        if active is None:
            typer.echo("No active release.")
            raise typer.Exit(1)
        report = run_suite(conn, repo, active)
    typer.echo(f"r{active['number']}: {report.summary}")
    for failure in report.failures:
        typer.echo(f"  - {failure}")


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
def observe(
    repo: str = "hr-assistant",
    shadow: bool = typer.Option(False, help="Only propose actions; change nothing."),
) -> None:
    """Run the observer once: detect, diagnose, act (allowlist only), verify, report."""
    from kb.observer.agent import observe as run_observer

    with connect() as conn:
        incidents = run_observer(conn, repo, shadow=shadow)
    if not incidents:
        typer.echo("Observer: nothing abnormal.")
    for incident in incidents:
        typer.echo(
            f"Observer: {incident.kind} on {incident.target} -> {incident.status} "
            f"(diagnosis: {incident.provider})"
        )
        for action in incident.actions:
            typer.echo(f"  - {action}")
        if incident.verification:
            typer.echo(f"  verified: {incident.verification}")
        typer.echo(f"  report: {incident.path}")


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
