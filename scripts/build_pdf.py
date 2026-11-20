"""Build docs/build/DESIGN.pdf from docs/DESIGN.md.

Markdown is converted to HTML with a print stylesheet; Mermaid diagrams are rendered by
Mermaid.js inside headless Google Chrome, which then prints the page to PDF. Nothing
else needs to be installed besides Chrome (macOS path below, or set CHROME).

Usage: uv run python scripts/build_pdf.py   (or: make pdf)
"""

from __future__ import annotations

import html
import os
import re
import subprocess
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "DESIGN.md"
BUILD = ROOT / "docs" / "build"
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
MERMAID = "https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"

CSS = """
@page { size: A4; margin: 18mm 16mm 20mm 16mm; }
body { font-family: -apple-system, "Helvetica Neue", Arial, sans-serif; font-size: 10pt;
       line-height: 1.45; color: #1a1a1a; }
h1 { font-size: 20pt; margin: 0 0 6pt; }
h2 { font-size: 14pt; margin: 18pt 0 6pt; border-bottom: 1px solid #ccc; padding-bottom: 3pt;
     break-after: avoid; }
h2 { break-before: page; }
h2:first-of-type { break-before: auto; }
h3 { font-size: 11.5pt; margin: 12pt 0 4pt; break-after: avoid; }
p, li { orphans: 3; widows: 3; }
blockquote { margin: 6pt 0; padding: 4pt 10pt; border-left: 3px solid #999; color: #444; }
table { border-collapse: collapse; width: 100%; margin: 6pt 0 10pt; font-size: 8.5pt; }
th, td { border: 1px solid #bbb; padding: 3pt 5pt; vertical-align: top; text-align: left; }
th { background: #f0f0f0; }
tr { break-inside: avoid; }
code { font-family: Menlo, monospace; font-size: 8.5pt; background: #f4f4f4; padding: 0 2px; }
pre { background: #f6f6f6; padding: 6pt 8pt; font-size: 8pt; white-space: pre-wrap;
      break-inside: avoid; border: 1px solid #e2e2e2; }
pre code { background: none; padding: 0; }
pre.mermaid { background: none; border: none; text-align: center; padding: 0; margin: 0; }
pre.mermaid svg { max-width: 100%; height: auto; }
/* Large diagrams get a page of their own, sized to fill it. */
@page wide { size: A4 landscape; margin: 10mm; }
.diagram-wide { page: wide; break-before: page; break-after: page; }
.diagram-wide svg { max-height: 185mm; }
.diagram-full { break-before: page; break-after: page; }
.diagram-full svg { max-height: 255mm; }
a { color: #1a4fa0; text-decoration: none; }
"""


def _mermaid_blocks(text: str) -> tuple[str, list[str]]:
    """Take ```mermaid blocks out before Markdown conversion, leaving placeholders."""
    blocks: list[str] = []

    def keep(match: re.Match) -> str:
        blocks.append(match.group(1))
        return f"\n\nMERMAID-BLOCK-{len(blocks) - 1}\n\n"

    return re.sub(r"```mermaid\n(.*?)```", keep, text, flags=re.S), blocks


def build_html() -> str:
    text, blocks = _mermaid_blocks(SOURCE.read_text())
    body = markdown.markdown(text, extensions=["tables", "fenced_code", "toc", "sane_lists"])
    for i, block in enumerate(blocks):
        layout = ""
        if block.startswith("sequenceDiagram"):
            layout = "diagram-wide"  # long timelines read best on a landscape page
        elif block.startswith("flowchart LR") and block.count("\n") > 40:
            # The system diagram is very wide: top-to-bottom fits a portrait page.
            block = block.replace("flowchart LR", "flowchart TB", 1)
            layout = "diagram-full"
        body = body.replace(
            f"<p>MERMAID-BLOCK-{i}</p>",
            f'<div class="{layout}"><pre class="mermaid">{html.escape(block)}</pre></div>',
        )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Agent Knowledge Base: System Design</title>
<style>{CSS}</style>
<script src="{MERMAID}"></script>
<script>mermaid.initialize({{startOnLoad: true, theme: "neutral", securityLevel: "strict",
  flowchart: {{useMaxWidth: true}}, sequence: {{useMaxWidth: true}}}});</script>
</head><body>{body}</body></html>"""


def main() -> None:
    if not Path(CHROME).exists():
        sys.exit(f"Google Chrome not found at {CHROME}; set CHROME=/path/to/chrome")
    BUILD.mkdir(parents=True, exist_ok=True)
    page = BUILD / "DESIGN.html"
    pdf = BUILD / "DESIGN.pdf"
    page.write_text(build_html())
    subprocess.run(
        [
            CHROME,
            "--headless=new",
            "--disable-gpu",
            "--no-pdf-header-footer",
            "--virtual-time-budget=20000",
            f"--print-to-pdf={pdf}",
            page.as_uri(),
        ],
        check=True,
        capture_output=True,
    )
    print(f"Wrote {pdf.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
