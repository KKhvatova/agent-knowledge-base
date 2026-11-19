"""Who is asking: users and tokens (DESIGN.md §6.3).

POC simplification: tokens are JWTs signed locally with a shared secret, and user
attributes come from the HRIS export. In production the token would come from the
corporate IdP through an on-behalf-of exchange (AgentCore Identity), and attributes
from the IdP.

The token binds a user to an agent (Context Repo): the agent can only act for that
user, and only within its own Context Repo.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

import jwt

from kb.config import ROOT

SECRET = os.environ.get("KB_JWT_SECRET", "dev-only-secret-change-me-in-production-0123456789")
TOKEN_TTL_SECONDS = 8 * 3600  # long enough for a demo; minutes in production


@dataclass(frozen=True)
class Principal:
    employee_id: str
    email: str
    name: str
    country: str
    role: str  # employee | hr
    hr_scope: str  # country | global
    context_repo: str  # the agent this token was issued for

    def cedar_entity(self) -> dict:
        return {
            "uid": {"__entity": {"type": "Employee", "id": self.employee_id}},
            "attrs": {
                "email": self.email,
                "country": self.country,
                "role": self.role,
                "hr_scope": self.hr_scope,
            },
            "parents": [],
        }


def _employees() -> dict[str, dict]:
    rows = json.loads((ROOT / "data" / "sources" / "hris" / "employees.json").read_text())
    return {r["email"]: r for r in rows}


def principal_for(email: str, context_repo: str) -> Principal:
    row = _employees().get(email)
    if row is None:
        raise PermissionError(f"Unknown user {email!r}")
    return Principal(
        employee_id=row["id"],
        email=row["email"],
        name=row["name"],
        country=row["country"],
        role=row["role"],
        hr_scope=row.get("hr_scope", "country"),
        context_repo=context_repo,
    )


def issue_token(email: str, context_repo: str) -> str:
    principal_for(email, context_repo)  # fail early on unknown users
    now = int(time.time())
    claims = {"sub": email, "agent": context_repo, "iat": now, "exp": now + TOKEN_TTL_SECONDS}
    return jwt.encode(claims, SECRET, algorithm="HS256")


def verify_token(token: str) -> Principal:
    claims = jwt.decode(token, SECRET, algorithms=["HS256"])
    return principal_for(claims["sub"], claims["agent"])
