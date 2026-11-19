"""Simulate changes made by source owners, on the sources working copy (.data/sources).

Each change is written the way a real source would expose it: the page changes in the
"Confluence export" and a webhook-like event is appended to events.jsonl.

Usage:
  uv run python scripts/demo.py edit-policy       # CA vacation 15 -> 18 days (DESIGN §3.3)
  uv run python scripts/demo.py corrupt-handbook  # CA page loses sections (parse failure)
  uv run python scripts/demo.py delete-page       # US hybrid-work page deleted, no event
  uv run python scripts/demo.py restrict-page     # IT leave page restricted to HR only
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime

from kb.config import EVENTS_FILE, SOURCES_DIR, ensure_working_copy

PAGES = SOURCES_DIR / "confluence-hr" / "pages"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _event(external_id: str, kind: str) -> None:
    with EVENTS_FILE.open("a") as f:
        f.write(
            json.dumps(
                {
                    "source_id": "confluence-hr",
                    "external_id": external_id,
                    "type": kind,
                    "at": _now(),
                }
            )
            + "\n"
        )


def _update_page(page_id: str, html: str | None = None, groups: list[str] | None = None) -> None:
    meta_path = PAGES / f"{page_id}.json"
    meta = json.loads(meta_path.read_text())
    if html is not None:
        (PAGES / f"{page_id}.html").write_text(html)
    if groups is not None:
        meta["restrictions"]["read"]["groups"] = groups
    meta["version"] = {"number": meta["version"]["number"] + 1, "when": _now()}
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    _event(page_id, "upsert")


def edit_policy() -> None:
    html = (
        (PAGES / "2001.html")
        .read_text()
        .replace(
            "<strong>15 days of paid vacation</strong>", "<strong>18 days of paid vacation</strong>"
        )
    )
    _update_page("2001", html=html)
    print("HR-CA owner raised vacation from 15 to 18 days (page 2001) + webhook event.")


def corrupt_handbook() -> None:
    # What a broken export or a parser failure on a new layout looks like: most sections
    # are gone and what is left is truncated.
    html = (
        "<h1>Vacation and Time Off – Canada</h1>\n<h2>How to request vacation</h2>\n"
        "<p>Submit the request in Workday.</p>\n"
    )
    _update_page("2001", html=html)
    print(
        "Page 2001 (Vacation – Canada) arrived corrupted: 'Entitlement' and 'Carry-over' "
        "sections lost + webhook event."
    )


def delete_page() -> None:
    (PAGES / "3004.json").unlink()
    (PAGES / "3004.html").unlink()
    print(
        "Page 3004 (Hybrid Work – US) deleted in the source. No event was sent: only "
        "reconciliation will notice."
    )


def restrict_page() -> None:
    _update_page("1001", groups=["hr-it", "hr-global"])
    print("Page 1001 (Ferie – Italia) restricted to HR only + webhook event.")


COMMANDS = {
    "edit-policy": edit_policy,
    "corrupt-handbook": corrupt_handbook,
    "delete-page": delete_page,
    "restrict-page": restrict_page,
}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        sys.exit(__doc__)
    ensure_working_copy()
    COMMANDS[sys.argv[1]]()
