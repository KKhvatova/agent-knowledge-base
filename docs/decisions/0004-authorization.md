# 0004. Authorization: on-behalf-of identity, Cedar policies, enforcement at retrieval time

- **Status:** accepted
- **Date:** 2026-11-19
- **Design:** [DESIGN §6.3, §6.4](../DESIGN.md#6-agent-access)

## Context

An agent must never see more than the user it works for. Source permission models differ (Confluence restrictions, Drive sharing, HRIS visibility rules), are often inconsistent, and change. The index always holds a slightly old copy of them. And the agent itself is a target: prompt injection can try to make it fetch what the user may not see.

## Decision

- **On-behalf-of identity.** Every call carries a token that binds the **user** and the **agent workflow** (Context Repo). The agent has no standing access of its own.
- **Normalised access attributes on every asset** (audience, country, classification, owner), mapped from source permissions. When a mapping is uncertain, the asset becomes `owner_only`: **deny by default**, with a note explaining why.
- **Cedar policies** decide access, using user attributes from the IdP and asset attributes. Enforcement happens **at retrieval time**, in two steps: an equivalent filter applied *before* ranking (so restricted content never influences results), then an explicit Cedar check on every returned asset. A disagreement between the two is logged as an incident.
- **Restricted content gets a live check**: the MCP server also asks the source whether the user can still access the record right now, which bounds the window of a permission change not yet synced.

## Alternatives considered

- **Agent service account with broad access.** Simple; the agent exceeds the user's permissions, and prompt injection inherits that.
- **Filter after ranking only.** Leaks through ranking and through result counts; can return empty pages.
- **Separate index per audience.** Simple to reason about; explodes with countries × roles × departments and duplicates content.
- **OPA / Rego.** More general; no managed AWS service, harder to analyse. **Postgres row-level security alone**: tied to one store, does not cover live tools.

## Consequences

- Authorization quality depends on identity data quality (DESIGN §13, risk 1).
- Some content is under-shared until its mapping is fixed; the share of `owner_only` content is a monitored metric.
- The pre-filter must stay equivalent to the policies. In production it is generated from them (Cedar partial evaluation); in the POC it is written by hand and checked by a test matrix.
- **In the POC:** `policies/kb.cedar`, `src/kb/authz/` (`cedarpy` + SQL filter), JWT issued by `kb token`; `test_authz.py` (Cedar vs SQL agreement for every user and every asset).
