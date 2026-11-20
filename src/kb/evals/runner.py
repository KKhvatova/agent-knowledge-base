"""Evaluation suite runner: the release gate (DESIGN.md §11.1).

All checks here are deterministic (retrieval, facts, permissions, behaviours), so the
gate never depends on a model's judgement. An LLM judge for answer faithfulness
(DESIGN.md §11.1) is not implemented in the POC.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import psycopg
import yaml

from kb.authz.identity import principal_for
from kb.retrieval import search
from kb.store.releases import CONTEXT_REPOS


@dataclass
class EvalReport:
    passed: bool
    metrics: dict
    failures: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        m = self.metrics
        return (
            f"{'PASS' if self.passed else 'FAIL'} hit@{m['k']}={m['hit_rate']:.2f} "
            f"fact_recall={m['fact_recall']:.2f} acl_leaks={m['acl_leaks']} "
            f"behaviours={m['behaviours_passed']}/{m['behaviours_total']}"
            + (f" collapses={m['content_collapses']}" if m.get("content_collapses") else "")
        )

    def as_dict(self) -> dict:
        return {
            "summary": self.summary,
            "passed": self.passed,
            "metrics": self.metrics,
            "failures": self.failures,
        }


def _record_key(result: dict) -> str:
    # asset ids look like "<source>:<external_id>#<section>"
    return result["asset_id"].split("#", 1)[0]


def _records_changed_since_active(
    conn: psycopg.Connection, repo_name: str, release: dict
) -> set[str]:
    """Records whose pinned asset versions differ between the active release and `release`."""
    rows = conn.execute(
        "SELECT av.source_id || ':' || av.external_id AS record, ra.release_id, "
        "array_agg(av.id ORDER BY av.id) AS ids FROM release_assets ra "
        "JOIN asset_versions av ON av.id = ra.asset_version_id "
        "WHERE ra.release_id IN (%s, (SELECT active_release FROM context_repos WHERE name = %s)) "
        "GROUP BY 1, 2",
        (release["id"], repo_name),
    ).fetchall()
    by_release: dict[int, dict[str, list[int]]] = {}
    for r in rows:
        by_release.setdefault(r["release_id"], {})[r["record"]] = r["ids"]
    if len(by_release) < 2:
        return set()
    a, b = by_release.values()
    return {rec for rec in a.keys() | b.keys() if a.get(rec) != b.get(rec)}


def last_good_release(conn: psycopg.Connection, repo_name: str, before: int) -> dict | None:
    """Most recent release before `before` that passed its evals without being forced."""
    return conn.execute(
        "SELECT * FROM releases WHERE context_repo = %s AND number < %s "
        "AND status IN ('promoted', 'rolled_back') AND (eval_report->>'passed')::boolean "
        "AND coalesce((eval_report->>'forced')::boolean, false) = false "
        "ORDER BY number DESC LIMIT 1",
        (repo_name, before),
    ).fetchone()


def _content_collapses(
    conn: psycopg.Connection, repo_name: str, release: dict, min_ratio: float
) -> list[str]:
    """Records whose content shrank below `min_ratio` of the last good release.

    The "content collapse" breaker of DESIGN.md §7.4: a truncated export or a parser
    failing on a new layout loses most of a document, while a real edit rarely does.
    Fact checks alone can miss it when the lost facts are few or not in the golden set.
    """
    good = last_good_release(conn, repo_name, release["number"])
    if good is None:
        return []
    rows = conn.execute(
        "SELECT ra.release_id, av.source_id || ':' || av.external_id AS record, "
        "sum(length(av.content)) AS size FROM release_assets ra "
        "JOIN asset_versions av ON av.id = ra.asset_version_id "
        "WHERE ra.release_id IN (%s, %s) AND av.type = 'knowledge' GROUP BY 1, 2",
        (good["id"], release["id"]),
    ).fetchall()
    before = {r["record"]: r["size"] for r in rows if r["release_id"] == good["id"]}
    after = {r["record"]: r["size"] for r in rows if r["release_id"] == release["id"]}
    return [
        f"CONTENT COLLAPSE: {rec} went from {before[rec]} to {after[rec]} characters "
        f"since r{good['number']}"
        for rec in sorted(before.keys() & after.keys())
        if after[rec] < min_ratio * before[rec]
    ]


def run_suite(conn: psycopg.Connection, repo_name: str, release: dict) -> EvalReport:
    suite = yaml.safe_load((CONTEXT_REPOS / repo_name / "evals" / "suite.yaml").read_text())
    k, gates = suite["k"], suite["gates"]
    failures: list[str] = []
    changed = _records_changed_since_active(conn, repo_name, release)

    def ask(case: dict, top: int) -> dict:
        principal = principal_for(case["user"], repo_name)
        as_of = date.fromisoformat(case["as_of"]) if case.get("as_of") else None
        return search(conn, principal, case["query"], k=top, as_of=as_of, release=release)

    # 1. Golden questions
    hits = facts = 0
    for case in suite["golden"]:
        results = ask(case, k)["results"]
        if any(_record_key(r) == case["expect"] for r in results):
            hits += 1
        else:
            failures.append(f"miss: {case['query']!r} (expected {case['expect']})")
        if any(case["fact"].lower() in r["content"]["text"].lower() for r in results):
            facts += 1
        else:
            note = ""
            if case["expect"] in changed:
                # A legitimate update and a corruption fail this check the same way, so
                # the system cannot excuse it; it flags it for the owner (DESIGN §11.1).
                note = " [expected record changed in this release: owner must re-confirm the fact]"
            failures.append(f"fact not found: {case['fact']!r} for {case['query']!r}{note}")

    # 2. Authorization: questions designed to reach forbidden content
    leaks = 0
    for case in suite["acl"]:
        seen = {_record_key(r) for r in ask(case, 10)["results"]}
        for forbidden in case["must_not_see"]:
            if forbidden in seen:
                leaks += 1
                failures.append(f"ACL LEAK: {case['user']} saw {forbidden}")

    # 3. Behaviours: conflicts, future versions, validity
    behaviours_passed = 0
    for case in suite["behaviours"]:
        results = ask(case, k)["results"]
        ok = True
        if "expect_conflict" in case:
            want = case["expect_conflict"]
            found = [c for r in results for c in r["conflicts"] if c["fact"] == want["fact"]]
            newest = {s["value"] for c in found for s in c["statements"] if s["most_recent"]}
            ok &= bool(found) and newest == {want["most_recent_value"]}
        if "expect_future_version" in case:
            ok &= any(
                (r["future_version"] or {}).get("valid_from") == case["expect_future_version"]
                for r in results
            )
        if "expect_fact" in case:
            ok &= any(case["expect_fact"].lower() in r["content"]["text"].lower() for r in results)
        if ok:
            behaviours_passed += 1
        else:
            failures.append(f"behaviour failed: {case['name']}")

    # 4. Structure: no document may collapse compared with the last good release
    collapses = _content_collapses(conn, repo_name, release, gates["content_min_ratio"])
    failures.extend(collapses)

    total = len(suite["golden"])
    metrics = {
        "k": k,
        "hit_rate": hits / total,
        "fact_recall": facts / total,
        "acl_leaks": leaks,
        "behaviours_passed": behaviours_passed,
        "behaviours_total": len(suite["behaviours"]),
        "golden_total": total,
        "content_collapses": len(collapses),
    }
    passed = (
        metrics["hit_rate"] >= gates["hit_rate_min"]
        and metrics["fact_recall"] >= gates["fact_recall_min"]
        and leaks <= gates["acl_leaks_max"]
        and not collapses
        and (not gates["behaviours_must_pass"] or behaviours_passed == len(suite["behaviours"]))
    )
    return EvalReport(passed=passed, metrics=metrics, failures=failures)
