"""Renderers for a report document: the ``report-renderer`` seam.

A renderer takes only the document and returns text. ``json`` is the
canonical form; ``markdown`` and ``html`` present the same items. The HTML
is one self-contained file: inline CSS, no scripts, and a content security
policy that forbids every fetch, so it opens offline and prints as is.
"""

from __future__ import annotations

import html
import json
from collections.abc import Callable
from typing import Any

from repowise.core.registry.plugins import SeamRegistry

Renderer = Callable[[dict[str, Any]], str]


def render_json(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def _score(value: Any) -> str:
    if value is None:
        return ""
    return "?" if value == -1 else f"{value:g}"


def _heading(section: dict[str, Any]) -> str:
    return f"{section['title']} ({section['repo']})" if section.get("repo") else section["title"]


def _index_line(repo: dict[str, Any]) -> tuple[str, str, str]:
    snap = repo.get("last_snapshot") or {}
    name = repo.get("alias") or repo.get("name") or ""
    if repo.get("indexed") is False:
        return name, "not indexed", "none"
    if repo.get("index_state") == "behind":
        return name, "index behind this version, run repowise update", "none"
    if repo.get("index_state") == "unreadable":
        return name, "index could not be read", "none"
    commit = (repo.get("head_commit") or "")[:12]
    taken = f"{snap['taken_at'][:10]} ({snap['stamp']})" if snap else "none"
    return name, commit, taken


def _line(text: Any) -> str:
    """*text* on one line, with backticks escaped so it cannot open code."""
    return " ".join(str(text).split()).replace("`", "\\`")


def _cell(text: Any) -> str:
    return _line(text).replace("|", "\\|")


def _code(text: Any) -> str:
    """*text* as an inline code span; a backtick cannot be escaped inside one."""
    return "`" + " ".join(str(text).split()).replace("`", "'") + "`"


def _title(document: dict[str, Any]) -> str:
    kind = str(document.get("kind") or "assessment").capitalize()
    return f"{kind}: {document['scope'].get('name') or document['scope']['kind']}"


def render_markdown(document: dict[str, Any]) -> str:
    gen = document["generator"]
    lines = [
        f"# {_line(_title(document))}",
        "",
        document["notice"],
        "",
        f"Generated {document['generated_at']} by {gen['name']} {gen['version']} "
        f"(stamp {gen['stamp']}), schema version {document['schema_version']}. "
        "Score is 0 to 10, `?` is inconclusive, blank is a descriptive item.",
        "",
        "| Repository | Indexed commit | Last snapshot |",
        "|---|---|---|",
        *(f"| {' | '.join(_cell(v) for v in _index_line(r))} |" for r in document["repos"]),
    ]
    for section in document["sections"]:
        lines += [
            "",
            f"## {_line(_heading(section))}",
            "",
            _line(section["summary"]),
            "",
            "| Check | Score | Basis | Reason |",
            "|---|---|---|---|",
        ]
        for c in section["checks"]:
            name = _code(c["name"]).replace("|", "\\|")
            cells = (name, _score(c["score"]), c["basis"], _cell(c["reason"]))
            lines.append(f"| {' | '.join(cells)} |")
        for method in dict.fromkeys(c["method"] for c in section["checks"]):
            lines += ["", f"Method: {_line(method)}."]
        detailed = [c for c in section["checks"] if c["details"]]
        if detailed:
            lines.append("")
            for c in detailed:
                lines.append(f"- {_code(c['name'])}")
                lines += [f"  - {_line(d)}" for d in c["details"]]
    return "\n".join(lines) + "\n"


_CSS = """
:root { color-scheme: light; --ink: #1d232a; --muted: #5b6672; --line: #d7dde3;
  --band: #f3f5f7; --measured: #1f6f43; --inferred: #6a4d00; --declared: #23527c;
  --unresolved: #6b6b6b; --gated: #8a2f2f; }
body { margin: 0 auto; max-width: 64rem; padding: 2rem 1rem; color: var(--ink);
  background: #fff; font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
h1 { font-size: 1.6rem; margin: 0 0 .5rem; }
h2 { font-size: 1.15rem; margin: 2rem 0 .25rem; border-bottom: 1px solid var(--line); }
p.muted, .method { color: var(--muted); }
table { width: 100%; border-collapse: collapse; margin: .5rem 0; table-layout: fixed; }
th, td { text-align: left; vertical-align: top; padding: .3rem .5rem;
  border-bottom: 1px solid var(--line); overflow-wrap: anywhere; }
th { background: var(--band); font-weight: 600; }
td.score { width: 4rem; font-variant-numeric: tabular-nums; }
col.name { width: 28%; } col.score { width: 4.5rem; } col.basis { width: 6.5rem; }
code { font: 12px ui-monospace, SFMono-Regular, Consolas, monospace; }
.basis { font-weight: 600; }
.b-measured { color: var(--measured); } .b-inferred { color: var(--inferred); }
.b-declared { color: var(--declared); } .b-unresolved { color: var(--unresolved); }
.b-gated { color: var(--gated); }
ul.details { margin: .25rem 0 0; padding-left: 1.1rem; color: var(--muted); }
.notice { background: var(--band); padding: .6rem .8rem; border-left: 3px solid var(--declared); }
@media print { body { padding: 0; max-width: none; font-size: 11px; }
  h2 { break-after: avoid; } tr { break-inside: avoid; } a { color: inherit; } }
"""


def render_html(document: dict[str, Any]) -> str:
    e = html.escape
    gen = document["generator"]
    title = _title(document)
    out = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<meta http-equiv=\"Content-Security-Policy\" "
        "content=\"default-src 'none'; style-src 'unsafe-inline'\">",
        f"<title>{e(title)}</title><style>{_CSS}</style></head><body>",
        f"<h1>{e(title)}</h1>",
        f'<p class="notice">{e(document["notice"])}</p>',
        f'<p class="muted">Generated {e(document["generated_at"])} by {e(gen["name"])} '
        f"{e(gen['version'])} (stamp {e(gen['stamp'])}), schema version "
        f"{document['schema_version']}. Score is 0 to 10, ? is inconclusive, blank is a "
        "descriptive item.</p>",
        "<table><thead><tr><th>Repository</th><th>Indexed commit</th><th>Last snapshot</th>"
        "</tr></thead><tbody>",
        *(
            "<tr>" + "".join(f"<td>{e(v)}</td>" for v in _index_line(r)) + "</tr>"
            for r in document["repos"]
        ),
        "</tbody></table>",
    ]
    for section in document["sections"]:
        out += [
            f'<section id="{e(section["id"])}"><h2>{e(_heading(section))}</h2>',
            f'<p class="muted">{e(section["summary"])}</p>',
            '<table><colgroup><col class="name"><col class="score"><col class="basis"><col>'
            "</colgroup><thead><tr><th>Check</th><th>Score</th><th>Basis</th><th>Reason</th>"
            "</tr></thead><tbody>",
        ]
        for c in section["checks"]:
            details = "".join(f"<li>{e(d)}</li>" for d in c["details"])
            out.append(
                f"<tr><td><code>{e(c['name'])}</code></td>"
                f'<td class="score">{e(_score(c["score"]))}</td>'
                f'<td class="basis b-{e(c["basis"])}">{e(c["basis"])}</td>'
                f"<td>{e(c['reason'])}"
                + (f'<ul class="details">{details}</ul>' if details else "")
                + "</td></tr>"
            )
        out.append("</tbody></table>")
        for method in dict.fromkeys(c["method"] for c in section["checks"]):
            out.append(f'<p class="method">Method: {e(method)}.</p>')
        out.append("</section>")
    out.append("</body></html>")
    return "\n".join(out) + "\n"


_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")


def spreadsheet_cell(value: Any) -> str:
    """*value* as text safe to open in a spreadsheet: a leading formula sign is quoted.

    Titles and evidence quote commit messages, which anyone can write.
    """
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(_FORMULA_LEAD) else text


_CSV_COLUMNS = ("section", "repo", "check", "score", "basis", "reason", "details")


def render_csv(document: dict[str, Any]) -> str:
    """One row per check. A check whose ``data`` holds a ``row`` mapping (one
    change, say) adds its keys as further columns, in first-seen order."""
    import csv
    import io

    checks = [(s, c) for s in document["sections"] for c in s["checks"]]
    extra: dict[str, None] = {}
    for _section, c in checks:
        extra.update(dict.fromkeys((c.get("data") or {}).get("row") or {}))
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow([*_CSV_COLUMNS, *extra])
    for section, c in checks:
        row = (c.get("data") or {}).get("row") or {}
        cells = [
            section["id"],
            section.get("repo"),
            c["name"],
            _score(c["score"]),
            c["basis"],
            c["reason"],
            " | ".join(map(str, c["details"])),
            *(row.get(k) for k in extra),
        ]
        writer.writerow(spreadsheet_cell(v) for v in cells)
    return buf.getvalue()


report_renderers: SeamRegistry[Renderer] = SeamRegistry("report-renderer")
report_renderers.register("json", render_json, default=True)
report_renderers.register("markdown", render_markdown)
report_renderers.register("html", render_html)
report_renderers.register("csv", render_csv)


__all__ = [
    "Renderer",
    "render_csv",
    "render_html",
    "render_json",
    "render_markdown",
    "report_renderers",
    "spreadsheet_cell",
]
