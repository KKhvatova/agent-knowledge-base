"""Fact extraction for conflict detection (DESIGN.md §1.2, §5.3).

POC simplification: one deterministic extractor for one fact type, "vacation days per
year, per country", which is enough to show the mechanism on a real conflict in the
fixtures (US handbook: 20 days; HRIS policy table: 15 days). In production, candidate
pairs come from the entity layer and are compared by an LLM.
"""

from __future__ import annotations

import json
import re

_LEAVE_WORDS = re.compile(r"vacation|pto|ferie|time off", re.IGNORECASE)
_DAYS = re.compile(
    r"(\d+)\s*\**\s*(?:days|giorni lavorativi|giorni)\s*\**\s*"
    r"(?:of\s+(?:paid\s+)?(?:vacation|pto)|di ferie)",
    re.IGNORECASE,
)


def extract_facts(title: str, text: str, country: str, mime_type: str, raw: bytes) -> dict:
    facts: dict[str, str] = {}
    if mime_type == "application/json":
        row = json.loads(raw).get("row", {})
        if row.get("leave_type") == "vacation":
            facts[f"vacation_days_per_year:{row['country']}"] = str(row["days_per_year"])
        return facts
    if country != "GLOBAL" and _LEAVE_WORDS.search(title + " " + text):
        match = _DAYS.search(text)
        if match:
            facts[f"vacation_days_per_year:{country}"] = match.group(1)
    return facts
