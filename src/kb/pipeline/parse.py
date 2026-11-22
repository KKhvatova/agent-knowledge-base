"""T2 Parse and normalize: any source format -> a list of sections (DESIGN.md §2).

Sections follow the document's own structure (headings), not fixed sizes, so each one
is a meaningful, citable unit (DESIGN.md §3).
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup
from markdownify import markdownify
from pypdf import PdfReader


@dataclass(frozen=True)
class Section:
    heading: str  # "" for the document's introduction
    text: str  # Markdown, so lists and tables keep their structure


def parse(content: bytes, mime_type: str, title: str) -> list[Section]:
    if mime_type == "text/html":
        return _parse_html(content.decode("utf-8"))
    if mime_type == "application/pdf":
        return _parse_pdf(content, title)
    if mime_type == "application/json":
        return [Section(heading="", text=json.loads(content)["text"])]
    raise ValueError(f"No parser for {mime_type}")


def _parse_html(html: str) -> list[Section]:
    soup = BeautifulSoup(html, "html.parser")
    for el in soup.select("h1, .disclaimer"):
        el.decompose()  # title is stored separately; the disclaimer is not content

    sections: list[Section] = []
    heading, buffer = "", []
    for node in soup.contents:
        if getattr(node, "name", None) == "h2":
            _flush(sections, heading, buffer)
            heading, buffer = node.get_text(strip=True), []
        else:
            buffer.append(str(node))
    _flush(sections, heading, buffer)
    return sections


def _flush(sections: list[Section], heading: str, buffer: list[str]) -> None:
    text = markdownify("".join(buffer), heading_style="ATX").strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if text:
        sections.append(Section(heading=heading, text=text))


def _parse_pdf(content: bytes, title: str) -> list[Section]:
    """Split a PDF on heading-like lines (short, no final punctuation)."""
    reader = PdfReader(io.BytesIO(content))
    lines = [ln.strip() for page in reader.pages for ln in page.extract_text().splitlines()]
    lines = [ln for ln in lines if ln and ln != title and not ln.startswith("SYNTHETIC DATA")]

    sections: list[Section] = []
    heading, buffer = "", []
    for line in lines:
        if len(line) < 40 and not line.endswith((".", ",", ";", ":")):
            if buffer:
                sections.append(Section(heading=heading, text=" ".join(buffer)))
            heading, buffer = line, []
        else:
            buffer.append(line)
    if buffer:
        sections.append(Section(heading=heading, text=" ".join(buffer)))
    return sections
