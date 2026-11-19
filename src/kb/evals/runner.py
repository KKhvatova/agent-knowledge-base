"""Evaluation suite runner: the release gate (DESIGN.md §11.1).

All checks here are deterministic (retrieval, facts, permissions, behaviours), so the
gate never depends on a model's judgement. An optional LLM judge for answer
faithfulness is a separate, non-gating step.
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


def run_suite(conn: psycopg.Connection, repo_name: str, release: dict) -> EvalReport:
    suite = yaml.safe_load((CONTEXT_REPOS / repo_name / "evals" / "suite.yaml").read_text())
    k, gates = suite["k"], suite["gates"]
    failures: list[str] = []

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
            failures.append(f"fact not found: {case['fact']!r} for {case['query']!r}")

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

    total = len(suite["golden"])
    metrics = {
        "k": k,
        "hit_rate": hits / total,
        "fact_recall": facts / total,
        "acl_leaks": leaks,
        "behaviours_passed": behaviours_passed,
        "behaviours_total": len(suite["behaviours"]),
        "golden_total": total,
    }
    passed = (
        metrics["hit_rate"] >= gates["hit_rate_min"]
        and metrics["fact_recall"] >= gates["fact_recall_min"]
        and leaks <= gates["acl_leaks_max"]
        and (not gates["behaviours_must_pass"] or behaviours_passed == len(suite["behaviours"]))
    )
    return EvalReport(passed=passed, metrics=metrics, failures=failures)
