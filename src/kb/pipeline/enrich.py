"""T3 Enrich metadata: translate source permissions into one attribute model.

Every source expresses permissions differently (Confluence group names, Drive group
e-mails, HRIS country visibility). This step maps them into two attributes used by the
Cedar policies: `audience` (who may read) and `country`.

The mapping is lossy by nature (DESIGN.md §1.2). When it cannot translate a permission
with certainty, it falls back to `owner_only`: coverage is lost, security never is.
"""

from __future__ import annotations

from dataclasses import dataclass

from kb.config import SourceConfig
from kb.connectors.base import SourceRecord


@dataclass(frozen=True)
class AccessAttrs:
    audience: str  # public | staff | hr | owner_only
    country: str  # IT | CA | US | GLOBAL
    classification: str
    owner: str
    note: str | None = None  # why the mapping fell back to owner_only


def map_permissions(record: SourceRecord, source: SourceConfig) -> AccessAttrs:
    owner = source.owner_for(record.metadata)
    classification = record.metadata.get("classification", "internal")
    mapping = source.permission_mapping

    def fallback(note: str) -> AccessAttrs:
        return AccessAttrs("owner_only", "GLOBAL", classification, owner, note)

    if mapping["kind"] == "country":
        country = record.acl_raw.get("visible_to_country")
        if country not in {"IT", "CA", "US"}:
            return fallback(f"unknown country visibility: {country!r}")
        return AccessAttrs("staff", country, classification, owner)

    if mapping["kind"] == "groups":
        principals = record.acl_raw.get("read_groups", [])
    elif mapping["kind"] == "group_emails":
        principals = [p["emailAddress"] for p in record.acl_raw.get("permissions", [])]
    else:
        return fallback(f"unknown mapping kind {mapping['kind']!r}")

    known = mapping["groups"]
    unknown = [p for p in principals if p not in known]
    if not principals:
        return fallback("no read permissions found")
    if unknown:
        return fallback(f"unmapped principals: {', '.join(unknown)}")

    entries = [known[p] for p in principals]
    staff = {e["country"] for e in entries if e["audience"] == "staff"}
    # Global HR can read everything by policy, so "hr-global" never narrows a document.
    hr = {e["country"] for e in entries if e["audience"] == "hr"} - {"GLOBAL"}

    if staff:
        if len(staff) > 1:
            # E.g. a page shared with staff-it AND staff-ca: our attribute model has one
            # country per asset, so this cannot be expressed. Deny by default.
            return fallback(f"shared with several countries: {sorted(staff)}")
        return AccessAttrs("staff", staff.pop(), classification, owner)
    if len(hr) > 1:
        return fallback(f"shared with HR of several countries: {sorted(hr)}")
    return AccessAttrs("hr", hr.pop() if hr else "GLOBAL", classification, owner)
