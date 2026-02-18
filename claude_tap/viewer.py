"""HTML viewer generation – embed JSONL data into a self-contained HTML file."""

from __future__ import annotations

import json
from pathlib import Path


def _generate_html_viewer(trace_path: Path, html_path: Path) -> None:
    """Read viewer.html template, embed JSONL data, write self-contained HTML."""
    template = Path(__file__).parent / "viewer.html"
    if not template.exists():
        return

    # Read JSONL records
    records = []
    if trace_path.exists():
        with open(trace_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(line)

    # Build embedded data script — each line is already valid JSON
    jsonl_path_js = json.dumps(str(trace_path.absolute()))
    html_path_js = json.dumps(str(html_path.absolute()))
    data_js = (
        "const EMBEDDED_TRACE_DATA = [\n" + ",\n".join(records) + "\n];\n"
        f"const __TRACE_JSONL_PATH__ = {jsonl_path_js};\n"
        f"const __TRACE_HTML_PATH__ = {html_path_js};\n"
    )

    html = template.read_text(encoding="utf-8")
    # Inject data script before the main <script> tag
    html = html.replace(
        "<script>\nconst $ = s =>",
        f"<script>\n{data_js}</script>\n<script>\nconst $ = s =>",
        1,
    )
    html_path.write_text(html, encoding="utf-8")
