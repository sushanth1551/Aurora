"""Turn an execution report (Markdown) into a PDF with a locally installed browser.

No new dependency: the Markdown subset our reports use (headings, tables, lists,
checkboxes, bold, code, quotes) is converted to styled HTML here, and Microsoft
Edge or Google Chrome prints it headlessly (``--print-to-pdf``) with a throw-away
profile, so an open browser window is not disturbed.
"""

from __future__ import annotations

import html
import re
import shutil
import subprocess
import tempfile
from datetime import date
from pathlib import Path

BROWSERS = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)
PRINT_TIMEOUT_SECONDS = 120

CSS = """
@page { size: A4; margin: 14mm 12mm; }
body { font-family: "Segoe UI", Arial, sans-serif; font-size: 10.5px; color: #1d2433; line-height: 1.45; }
h1 { font-size: 20px; color: #0b3b6f; border-bottom: 3px solid #0b3b6f; padding-bottom: 6px; }
h2 { font-size: 15px; color: #0b3b6f; margin-top: 18px; border-bottom: 1px solid #c9d3e0; padding-bottom: 3px; }
h3 { font-size: 12.5px; color: #24456b; margin-top: 12px; }
h4 { font-size: 11px; margin: 10px 0 4px; }
table { border-collapse: collapse; width: 100%; margin: 6px 0 10px; font-size: 9.5px; }
th, td { border: 1px solid #d5dce6; padding: 3px 5px; text-align: left; vertical-align: top; }
th { background: #eef3f9; }
tr { page-break-inside: avoid; }
code { background: #f1f3f6; padding: 0 3px; border-radius: 3px; font-size: 9.5px; }
blockquote { margin: 6px 0; padding: 6px 10px; background: #f5f8fc; border-left: 4px solid #8aa6c8; }
ul { margin: 4px 0 8px 18px; padding: 0; }
li { margin: 1px 0; }
.box { font-family: monospace; }
.cover { color: #5a6577; font-size: 11px; margin-bottom: 10px; }
"""

INLINE = [
    (re.compile(r"`([^`]+)`"), r"<code>\1</code>"),
    (re.compile(r"\*\*([^*]+)\*\*"), r"<strong>\1</strong>"),
]


def _inline(text: str) -> str:
    out = html.escape(text, quote=False)
    for pattern, repl in INLINE:
        out = pattern.sub(repl, out)
    return out


def markdown_to_html(markdown: str, title: str, subtitle: str = "") -> str:
    lines = markdown.replace("\r\n", "\n").split("\n")
    body: list[str] = []
    in_list = False
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if in_list and not re.match(r"^\s*- ", line):
            body.append("</ul>")
            in_list = False
        if (
            stripped.startswith("|")
            and i + 1 < len(lines)
            and set(lines[i + 1].replace("|", "").strip()) <= {"-", " ", ":"}
        ):
            header = [c.strip() for c in stripped.strip("|").split("|")]
            body.append(
                "<table><tr>"
                + "".join(f"<th>{_inline(c)}</th>" for c in header)
                + "</tr>"
            )
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [
                    c.strip()
                    for c in re.split(r"(?<!\\)\|", lines[i].strip().strip("|"))
                ]
                body.append(
                    "<tr>"
                    + "".join(
                        f"<td>{_inline(c.replace(chr(92) + '|', '|'))}</td>"
                        for c in cells
                    )
                    + "</tr>"
                )
                i += 1
            body.append("</table>")
            continue
        heading = re.match(r"^(#{1,4}) (.*)", stripped)
        if heading:
            level = len(heading.group(1))
            body.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
        elif re.match(r"^\s*- ", line):
            if not in_list:
                body.append("<ul>")
                in_list = True
            item = re.sub(r"^\s*- ", "", line)
            item = re.sub(r"^\[ \] ", "☐ ", item)
            item = re.sub(r"^\[x\] ", "☑ ", item)
            body.append(f"<li>{_inline(item)}</li>")
        elif stripped.startswith(">"):
            body.append(f"<blockquote>{_inline(stripped.lstrip('> '))}</blockquote>")
        elif stripped:
            body.append(f"<p>{_inline(stripped)}</p>")
        i += 1
    if in_list:
        body.append("</ul>")
    cover = f'<div class="cover">{html.escape(subtitle)}</div>' if subtitle else ""
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body>"
        f"<h1>{html.escape(title)}</h1>{cover}" + "\n".join(body) + "</body></html>"
    )


def find_browser() -> str | None:
    for candidate in BROWSERS:
        if Path(candidate).is_file():
            return candidate
    return shutil.which("msedge") or shutil.which("chrome")


def pdf_path(folder: Path, name_pattern: str, day: date) -> Path:
    """``folder/<name with date>.pdf``; a second report on the same day gets a counter."""
    base = name_pattern.replace("{date}", day.isoformat())
    path = folder / f"{base}.pdf"
    n = 2
    while path.exists():
        path = folder / f"{base} ({n}).pdf"
        n += 1
    return path


def render_pdf(page_html: str, target: Path, browser: str | None = None) -> Path:
    browser = browser or find_browser()
    if not browser:
        raise RuntimeError("No Microsoft Edge or Google Chrome found to print the PDF.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="aurora-pdf-") as tmp:
        source = Path(tmp) / "report.html"
        source.write_text(page_html, encoding="utf-8")
        result = subprocess.run(
            [
                browser,
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--no-pdf-header-footer",
                f"--user-data-dir={Path(tmp) / 'profile'}",
                f"--print-to-pdf={target}",
                source.as_uri(),
            ],
            capture_output=True,
            timeout=PRINT_TIMEOUT_SECONDS,
            check=False,
        )
    if not target.is_file() or target.stat().st_size == 0:
        raise RuntimeError(
            f"The browser did not write the PDF (exit {result.returncode})."
        )
    return target
