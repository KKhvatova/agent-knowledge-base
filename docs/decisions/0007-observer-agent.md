# 0007. Observer agent: deterministic detection, LLM diagnosis, allowlisted reversible actions

- **Status:** accepted
- **Date:** 2026-11-19
- **Design:** [DESIGN §5](../DESIGN.md#5-self-maintenance)

## Context

A self-maintaining system fails in predictable ways: a release rejected by the gate, an active release that starts failing, a source frozen by a circuit breaker, propagation lag. Alarms detect these, but someone still has to read the diff, understand the cause and act, often hours later. An LLM agent can shorten that, but an agent with write access to a knowledge base is itself a risk.

## Decision

An **observer agent** with a strict split of responsibilities:

- **Detection is deterministic**: rules over release status, scheduled eval runs, sync health and freshness. The LLM never decides *whether* something is wrong.
- **Diagnosis uses an LLM** on structured signals (diff sizes, sections lost, failing checks), not on raw source content, which limits prompt-injection exposure. It proposes one action.
- **Actions are allowlisted and reversible**: `rollback`, `quarantine`, `open_ticket_only`. Any other proposal is rejected and replaced by a deterministic fallback. A regression on the active release always means rollback.
- **Guardrails**: shadow mode, rate limit per target, post-action verification (evals re-run; escalate if still failing), separate identity and tools, an incident report for every case.

## Alternatives considered

- **Alarms only.** Safe; detection without diagnosis, slow recovery.
- **Fully autonomous agent with general write access.** Fastest in theory; unbounded blast radius.
- **LLM-driven detection.** Flexible; non-deterministic, hard to test, and easy to manipulate.

## Consequences

- Common failures recover in minutes, with a report a human can audit.
- The observer starts in shadow mode, and each action type is enabled only once proven.
- Without an LLM the observer still works, with template diagnosis.
- **In the POC:** `src/kb/observer/` (Ollama, Anthropic API or template), `make demo-incident`, `make demo-rollback`; `test_observer.py` (invalid LLM proposal rejected, rate limit, shadow mode).
