"""Release gate and observer agent, including its guardrails (DESIGN.md §7.2, §8)."""

from __future__ import annotations

import json

import pytest

from kb.authz.identity import principal_for
from kb.evals.gate import release_with_gate
from kb.observer import agent
from kb.observer.llm import TemplateProvider
from kb.pipeline.sync import sync_source
from kb.retrieval import search
from kb.store import releases

LIAM = "liam.chen@flipp.example"


@pytest.fixture(autouse=True)
def _incidents_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "INCIDENTS_DIR", tmp_path / "incidents")


def _sync(conn, sources):
    for cfg in sources.values():
        sync_source(conn, cfg, reconcile=True)


def _corrupt_ca_page(sources):
    pages = sources["confluence-hr"].path / "pages"
    (pages / "2001.html").write_text("<h2>How to request vacation</h2><p>Use Workday.</p>")
    meta = json.loads((pages / "2001.json").read_text())
    meta["version"] = {"number": 99, "when": "2030-01-01T00:00:00Z"}
    (pages / "2001.json").write_text(json.dumps(meta))


@pytest.fixture
def healthy(conn, sources):
    _sync(conn, sources)
    assert release_with_gate(conn, "hr-assistant", "test").status == "promoted"
    return conn


def _liam_sees_15_days(conn):
    result = search(conn, principal_for(LIAM, "hr-assistant"), "vacation days per year", k=5)
    return any("15 days" in r["content"]["text"] for r in result["results"])


def test_gate_rejects_corrupted_content_and_users_keep_good_release(healthy, sources):
    _corrupt_ca_page(sources)
    _sync(healthy, sources)
    outcome = release_with_gate(healthy, "hr-assistant", "test")
    assert outcome.status == "rejected"
    assert releases.active_release(healthy, "hr-assistant")["number"] == 1
    assert _liam_sees_15_days(healthy)


def test_observer_quarantines_after_rejection_and_next_release_keeps_good_version(healthy, sources):
    _corrupt_ca_page(sources)
    _sync(healthy, sources)
    release_with_gate(healthy, "hr-assistant", "test")

    [incident] = agent.observe(healthy, provider=TemplateProvider())
    assert incident.kind == "release_rejected" and incident.status == "mitigated"
    assert incident.diagnosis["targets"] == ["confluence-hr:2001"]
    assert incident.path.exists()

    # The quarantined version is carried out; the good one is kept: nothing to release.
    assert release_with_gate(healthy, "hr-assistant", "test").status == "unchanged"
    assert agent.observe(healthy, provider=TemplateProvider()) == []  # no duplicate incident


def test_observer_rolls_back_a_forced_bad_release(healthy, sources):
    _corrupt_ca_page(sources)
    _sync(healthy, sources)
    assert release_with_gate(healthy, "hr-assistant", "test", force=True).status == "forced"
    assert not _liam_sees_15_days(healthy)

    [incident] = agent.observe(healthy, provider=TemplateProvider())
    assert incident.kind == "active_regression"
    assert "PASS" in incident.verification
    assert releases.active_release(healthy, "hr-assistant")["number"] == 1
    assert _liam_sees_15_days(healthy)


def test_shadow_mode_changes_nothing(healthy, sources):
    _corrupt_ca_page(sources)
    _sync(healthy, sources)
    release_with_gate(healthy, "hr-assistant", "test", force=True)

    [incident] = agent.observe(healthy, shadow=True, provider=TemplateProvider())
    assert incident.status == "proposed"
    assert releases.active_release(healthy, "hr-assistant")["number"] == 2


def test_rate_limit_escalates_instead_of_acting_twice(healthy, sources, conn):
    from kb.store.db import audit

    audit(conn, agent.ACTOR, "rollback", target="hr-assistant", to=1)  # recent action
    _corrupt_ca_page(sources)
    _sync(healthy, sources)
    release_with_gate(healthy, "hr-assistant", "test", force=True)

    [incident] = agent.observe(healthy, provider=TemplateProvider())
    assert incident.status == "escalated"
    assert releases.active_release(healthy, "hr-assistant")["number"] == 2  # not touched


class _RogueLLM:
    """An LLM that proposes something outside the allowlist."""

    name = "rogue"

    def complete_json(self, system, user):
        return {"probable_cause": "x", "action": "delete_source", "targets": ["confluence-hr"]}


def test_invalid_llm_proposal_is_rejected_and_replaced(healthy, sources):
    _corrupt_ca_page(sources)
    _sync(healthy, sources)
    release_with_gate(healthy, "hr-assistant", "test")

    [incident] = agent.observe(healthy, provider=_RogueLLM())
    assert incident.diagnosis["action"] == "quarantine"  # deterministic fallback
    assert "LLM proposal rejected" in incident.diagnosis["explanation"]


def test_circuit_breaker_is_escalated_to_owners(conn, sources):
    _sync(conn, sources)
    release_with_gate(conn, "hr-assistant", "test")
    for meta in sorted((sources["confluence-hr"].path / "pages").glob("*.json"))[:10]:
        meta.unlink()
    _sync(conn, sources)

    incidents = agent.observe(conn, provider=TemplateProvider())
    frozen = [i for i in incidents if i.kind == "source_frozen"]
    assert frozen and frozen[0].status == "escalated"
    assert any("hr-ca-team@flipp.example" in a for a in frozen[0].actions)
