"""Observer agent: detect -> collect context -> diagnose -> act -> verify (DESIGN.md §5).

- Detection is deterministic (release rejected by the gate, scheduled eval regression on
  the active release, frozen source).
- Diagnosis may use an LLM, which can only *propose* an action from the allowlist; the
  proposal is validated, and replaced by the deterministic one if invalid.
- Actions are executed by code, are reversible, rate-limited, and verified.
- The observer has its own identity ("observer") and never sees the content of
  confidential assets, only statistics about them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from kb.config import ROOT, load_sources
from kb.evals.runner import last_good_release, run_suite
from kb.observer.llm import SYSTEM, LLMProvider, TemplateProvider, choose_provider
from kb.store import releases
from kb.store.db import audit

ACTOR = "observer"
ALLOWED_ACTIONS = {"rollback", "quarantine", "open_ticket_only"}
# Which actions make sense for which incident: an allowlisted action can still be wrong.
ACTIONS_BY_KIND = {
    "release_rejected": {"quarantine", "open_ticket_only"},
    "active_regression": {"rollback", "open_ticket_only"},
    "source_frozen": {"open_ticket_only"},
}
RATE_LIMIT_MINUTES = 60
INCIDENTS_DIR = ROOT / "incidents"


@dataclass
class Incident:
    kind: str
    target: str
    signal_ref: str
    detected: str
    context: dict = field(default_factory=dict)
    diagnosis: dict = field(default_factory=dict)
    actions: list[str] = field(default_factory=list)
    verification: str = ""
    status: str = "open"
    provider: str = "template"
    path: Path | None = None


# --------------------------------------------------------------------------------------
# Detection (deterministic)
# --------------------------------------------------------------------------------------


def _already_handled(conn: psycopg.Connection, kind: str, target: str, ref: str) -> bool:
    """A signal is handled once acted on; a shadow-mode proposal does not count."""
    return (
        conn.execute(
            "SELECT 1 FROM incidents WHERE kind = %s AND target = %s AND signal_ref = %s "
            "AND status <> 'proposed'",
            (kind, target, ref),
        ).fetchone()
        is not None
    )


def detect(conn: psycopg.Connection, repo: str) -> list[Incident]:
    incidents: list[Incident] = []

    latest = conn.execute(
        "SELECT * FROM releases WHERE context_repo = %s ORDER BY number DESC LIMIT 1", (repo,)
    ).fetchone()
    if latest and latest["status"] == "rejected":
        ref = f"r{latest['number']}"
        if not _already_handled(conn, "release_rejected", repo, ref):
            incidents.append(
                Incident(
                    "release_rejected",
                    repo,
                    ref,
                    f"Candidate release {ref} was rejected by the eval gate: "
                    f"{latest['eval_report']['summary']}",
                )
            )

    active = releases.active_release(conn, repo)
    if active:
        ref = f"r{active['number']}"
        report = run_suite(conn, repo, active)  # scheduled evaluation of what users see now
        if not report.passed and not _already_handled(conn, "active_regression", repo, ref):
            incidents.append(
                Incident(
                    "active_regression",
                    repo,
                    ref,
                    f"Scheduled evals on the ACTIVE release {ref} fail: {report.summary}",
                    context={"eval_failures": report.failures},
                )
            )

    for state in conn.execute("SELECT * FROM sync_state WHERE frozen").fetchall():
        ref = state["frozen_reason"]
        if not _already_handled(conn, "source_frozen", state["source_id"], ref):
            incidents.append(
                Incident(
                    "source_frozen",
                    state["source_id"],
                    ref,
                    f"Source {state['source_id']} is frozen by a circuit breaker: {ref}",
                )
            )
    return incidents


# --------------------------------------------------------------------------------------
# Context collection (respecting the observer's own access limits)
# --------------------------------------------------------------------------------------


def _changed_records(conn: psycopg.Connection, good: dict, bad: dict) -> list[dict]:
    """Records whose pinned versions differ between two releases, with size statistics."""
    rows = conn.execute(
        "SELECT r.number, av.id, av.source_id, av.external_id, av.title, av.section_path, "
        "av.classification, av.owner, length(av.content) AS size FROM releases r "
        "JOIN release_assets ra ON ra.release_id = r.id "
        "JOIN asset_versions av ON av.id = ra.asset_version_id "
        "WHERE r.id IN (%s, %s)",
        (good["id"], bad["id"]),
    ).fetchall()
    by_record: dict[tuple, dict] = {}
    for r in rows:
        rec = by_record.setdefault(
            (r["source_id"], r["external_id"]),
            {
                "record": f"{r['source_id']}:{r['external_id']}",
                "title": r["title"],
                "owner": r["owner"],
                "classification": r["classification"],
                "good": {"ids": set(), "sections": [], "size": 0},
                "bad": {"ids": set(), "sections": [], "size": 0},
            },
        )
        side = rec["good"] if r["number"] == good["number"] else rec["bad"]
        side["ids"].add(r["id"])
        side["sections"].append(r["section_path"].split(" › ")[-1])
        side["size"] += r["size"]
    changed = []
    for rec in by_record.values():
        if rec["good"]["ids"] == rec["bad"]["ids"]:
            continue
        lost = sorted(set(rec["good"]["sections"]) - set(rec["bad"]["sections"]))
        changed.append(
            {
                "record": rec["record"],
                "title": rec["title"],
                "owner": rec["owner"],
                "classification": rec["classification"],
                "size_before": rec["good"]["size"],
                "size_after": rec["bad"]["size"],
                # Section names are metadata; content is never shown to the observer.
                "sections_lost": lost,
                "bad_asset_version_ids": sorted(rec["bad"]["ids"] - rec["good"]["ids"]),
            }
        )
    return changed


def _collect(conn: psycopg.Connection, incident: Incident) -> None:
    if incident.kind == "source_frozen":
        return
    repo = incident.target
    bad = conn.execute(
        "SELECT * FROM releases WHERE context_repo = %s AND number = %s",
        (repo, int(incident.signal_ref[1:])),
    ).fetchone()
    good = last_good_release(conn, repo, bad["number"])
    incident.context.update(
        {
            "bad_release": bad["number"],
            "last_good_release": good["number"] if good else None,
            "eval_summary": (bad["eval_report"] or {}).get("summary"),
            "eval_failures": incident.context.get("eval_failures")
            or (bad["eval_report"] or {}).get("failures", []),
            "changed_records": _changed_records(conn, good, bad) if good else [],
        }
    )


# --------------------------------------------------------------------------------------
# Diagnosis: deterministic proposal + optional LLM, validated against the allowlist
# --------------------------------------------------------------------------------------


def _suspects(incident: Incident) -> list[dict]:
    """Changed records that shrank sharply or lost sections: the usual parse failure."""
    changed = incident.context.get("changed_records", [])
    shrunk = [
        c for c in changed if c["sections_lost"] or c["size_after"] < 0.5 * max(c["size_before"], 1)
    ]
    return shrunk or changed


def _deterministic(incident: Incident) -> dict:
    if incident.kind == "source_frozen":
        return {
            "probable_cause": "Mass change at the source, connector bug or revoked "
            "permissions; it cannot be told apart without the owner.",
            "action": "open_ticket_only",
            "targets": [incident.target],
            "explanation": "The circuit breaker already protects agents; a human must "
            "confirm whether the change is real.",
        }
    suspects = _suspects(incident)
    names = ", ".join(
        f"{s['title']} (lost: {', '.join(s['sections_lost']) or 'none'}; "
        f"{s['size_before']}→{s['size_after']} chars)"
        for s in suspects
    )
    action = "rollback" if incident.kind == "active_regression" else "quarantine"
    return {
        "probable_cause": f"New versions of {names or 'some assets'} are much smaller than "
        "before; probable parsing or source error.",
        "action": action,
        "targets": [s["record"] for s in suspects],
        "explanation": "Restore the last release that passed evals and keep the suspicious "
        "versions out of future releases until the owner confirms them."
        if action == "rollback"
        else "The gate already kept them out of the active release; quarantine them so the "
        "next candidate keeps the previous good versions.",
    }


def _diagnose(incident: Incident, provider: LLMProvider) -> None:
    fallback = _deterministic(incident)
    incident.provider = provider.name
    if isinstance(provider, TemplateProvider):
        incident.diagnosis = fallback
        return
    candidates = sorted(
        {c["record"] for c in incident.context.get("changed_records", [])} | {incident.target}
    )
    payload = {
        "detected": incident.detected,
        "kind": incident.kind,
        "context": incident.context,
        "allowed_actions": sorted(ACTIONS_BY_KIND[incident.kind]),
        "candidate_targets": candidates,
    }
    try:
        proposal = provider.complete_json(SYSTEM, json.dumps(payload, default=str))
        valid = (
            proposal.get("action") in ACTIONS_BY_KIND[incident.kind]
            and set(proposal.get("targets", [])) <= set(candidates)
            and proposal.get("probable_cause")
        )
    except Exception as error:  # noqa: BLE001 - any LLM failure falls back to the template
        proposal, valid = {"error": str(error)}, False
    if not valid:
        fallback["explanation"] += f" (LLM proposal rejected or unavailable: {proposal})"
        incident.diagnosis = fallback
        return
    # The model may explain the cause, but the action on an active regression is always
    # a rollback first: users must get the last good release back.
    if incident.kind == "active_regression":
        proposal["action"] = "rollback"
    incident.diagnosis = proposal


# --------------------------------------------------------------------------------------
# Action: allowlisted, reversible, rate-limited, verified
# --------------------------------------------------------------------------------------


def _rate_limited(conn: psycopg.Connection, target: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM audit_log WHERE actor = %s AND details->>'target' = %s "
            "AND action IN ('rollback', 'quarantine') "
            "AND at > now() - make_interval(mins => %s)",
            (ACTOR, target, RATE_LIMIT_MINUTES),
        ).fetchone()
        is not None
    )


def _quarantine(conn: psycopg.Connection, incident: Incident, records: list[str]) -> None:
    ids = [
        i
        for c in incident.context.get("changed_records", [])
        if c["record"] in records
        for i in c["bad_asset_version_ids"]
    ]
    if not ids:
        return
    conn.execute("UPDATE asset_versions SET status = 'quarantined' WHERE id = ANY(%s)", (ids,))
    audit(
        conn,
        ACTOR,
        "quarantine",
        target=incident.target,
        asset_version_ids=ids,
        incident=incident.signal_ref,
    )
    incident.actions.append(
        f"Quarantined {len(ids)} asset version(s) of {', '.join(records)} "
        "(kept for inspection, excluded from retrieval and releases)."
    )


def _act(conn: psycopg.Connection, incident: Incident, shadow: bool) -> None:
    diagnosis = incident.diagnosis
    action = diagnosis["action"]
    owners = sorted(
        {
            c["owner"]
            for c in incident.context.get("changed_records", [])
            if c["record"] in diagnosis.get("targets", [])
        }
    )
    if incident.kind == "source_frozen":
        owners = sorted(set(load_sources()[incident.target].owners.values()))

    if shadow:
        incident.status = "proposed"
        incident.actions.append(
            f"SHADOW MODE: would execute '{action}' on "
            f"{diagnosis.get('targets')}. Nothing was changed."
        )
        return
    if action != "open_ticket_only" and _rate_limited(conn, incident.target):
        incident.status = "escalated"
        incident.actions.append(
            "Rate limit: an automatic action already ran on this target "
            f"in the last {RATE_LIMIT_MINUTES} min. Escalated, no action."
        )
        return

    if action == "rollback" and incident.context.get("last_good_release") is None:
        incident.status = "escalated"
        incident.actions.append(
            "No earlier release passed its evals: nothing safe to roll back to. "
            "Escalated to humans, no action."
        )
        return

    if action == "rollback":
        target = releases.rollback(
            conn,
            incident.target,
            incident.context["last_good_release"],
            actor=ACTOR,
            reason=f"observer: {incident.detected}",
        )
        audit(conn, ACTOR, "rollback", target=incident.target, to=target["number"])
        incident.actions.append(
            f"Rolled back {incident.target} to r{target['number']} "
            "(last release that passed evals)."
        )
        _quarantine(conn, incident, diagnosis.get("targets", []))
    elif action == "quarantine":
        _quarantine(conn, incident, diagnosis.get("targets", []))

    ticket = (
        f"Ticket opened for {', '.join(owners) or 'the platform team'}: "
        f"{diagnosis['probable_cause']}"
    )
    incident.actions.append(ticket)
    audit(
        conn,
        ACTOR,
        "open_ticket",
        target=incident.target,
        owners=owners,
        incident=incident.signal_ref,
    )
    incident.status = "mitigated" if action != "open_ticket_only" else "escalated"


def _verify(conn: psycopg.Connection, incident: Incident) -> None:
    if incident.kind == "source_frozen" or incident.status == "proposed":
        return
    active = releases.active_release(conn, incident.target)
    report = run_suite(conn, incident.target, active)
    incident.verification = f"Active release r{active['number']}: {report.summary}"
    if not report.passed:
        incident.status = "escalated"
        incident.verification += " -> metrics did NOT recover; escalated to humans."


# --------------------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------------------


def _write_report(conn: psycopg.Connection, incident: Incident) -> None:
    number = conn.execute("SELECT coalesce(max(id), 0) + 1 AS n FROM incidents").fetchone()["n"]
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    d = incident.diagnosis
    lines = [
        f"# INC-{number:04d}  {incident.target}  {now}  status: {incident.status}",
        "",
        f"**Detected** {incident.detected}",
        "",
        f"**Diagnosis** ({incident.provider}) {d.get('probable_cause', '')}",
        "",
        f"{d.get('explanation', '')}",
        "",
        "**Actions**",
        *[f"- {a}" for a in incident.actions],
    ]
    if incident.verification:
        lines += ["", f"**Verified** {incident.verification}"]
    failures = incident.context.get("eval_failures") or []
    if failures:
        lines += ["", "**Eval failures**", *[f"- {f}" for f in failures]]
    report = "\n".join(lines) + "\n"
    conn.execute(
        # A real run replaces an earlier shadow-mode proposal for the same signal.
        "INSERT INTO incidents (kind, target, signal_ref, status, report) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (kind, target, signal_ref) "
        "DO UPDATE SET status = EXCLUDED.status, report = EXCLUDED.report, opened_at = now()",
        (incident.kind, incident.target, incident.signal_ref, incident.status, report),
    )
    INCIDENTS_DIR.mkdir(exist_ok=True)
    incident.path = INCIDENTS_DIR / f"INC-{number:04d}.md"
    incident.path.write_text(report)


def observe(
    conn: psycopg.Connection,
    repo: str = "hr-assistant",
    shadow: bool = False,
    provider: LLMProvider | None = None,
) -> list[Incident]:
    provider = provider or choose_provider()
    incidents = detect(conn, repo)
    for incident in incidents:
        _collect(conn, incident)
        _diagnose(incident, provider)
        _act(conn, incident, shadow)
        _verify(conn, incident)
        _write_report(conn, incident)
        conn.commit()
    return incidents
