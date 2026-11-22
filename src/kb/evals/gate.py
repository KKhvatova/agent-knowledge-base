"""Release gate: build a candidate, evaluate it, promote only if it passes (DESIGN.md §5)."""

from __future__ import annotations

from dataclasses import dataclass

import psycopg

from kb.evals.runner import EvalReport, run_suite
from kb.store import releases
from kb.store.db import audit
from kb.store.releases import ContextRepo


@dataclass
class GateOutcome:
    status: str  # unchanged | promoted | rejected | forced
    release: dict | None
    report: EvalReport | None


def release_with_gate(
    conn: psycopg.Connection, repo_name: str, actor: str, force: bool = False
) -> GateOutcome:
    candidate = releases.build_candidate(conn, ContextRepo.load(repo_name), created_by=actor)
    if candidate is None:
        conn.commit()
        return GateOutcome("unchanged", None, None)

    report = run_suite(conn, repo_name, candidate)
    if report.passed:
        releases.promote(conn, candidate, report.as_dict(), actor)
        status = "promoted"
    elif force:
        # Emergency bypass: allowed, but recorded. The observer's scheduled evals will
        # catch a regression in the active release and can roll it back (DESIGN.md §5).
        releases.promote(conn, candidate, {**report.as_dict(), "forced": True}, actor)
        audit(
            conn,
            actor,
            "release_forced",
            context_repo=repo_name,
            number=candidate["number"],
            summary=report.summary,
        )
        status = "forced"
    else:
        releases.reject(conn, candidate, report.as_dict(), actor)
        status = "rejected"
    conn.commit()
    return GateOutcome(status, candidate, report)
