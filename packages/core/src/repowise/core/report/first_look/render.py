"""The repo report as Markdown and as one self-contained HTML file.

Both are renderers on the ``report-renderer`` seam (``first-look-markdown``,
``first-look-html``) and, like every renderer there, take only a document and
return text. The document is the report's JSON (the living report's, with each
item's status) plus what a renderer cannot look up itself
(:func:`.write.render_document`): the HTML page's agent prompts and the app's
address when a server is known.

They print the report's ``prose`` and nothing else in words; everything else
is structure read off the items: location chips, status, basis and confidence
badges, the do-now mark and links built from route keys. The Markdown export
carries no prompt: the HTML page and the app hold them.
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

from repowise.core.report.render import _code, html_page, report_renderers

from .model import SECTION_CAP

#: Renderer names on the ``report-renderer`` seam. They read a first-look
#: document, so the assessment-style commands leave them out of ``--format``.
MARKDOWN = "first-look-markdown"
HTML = "first-look-html"
PREFIX = "first-look-"

#: Chips one item shows before "N more".
CHIPS_SHOWN = 8

#: Each route key's path under ``/repos/{id}/`` in the local web app.
_SECTION_ROUTES = {
    "security": "security",
    "performance": "code-health?tab=performance",
    "health": "code-health",
    "tests": "coverage",
    "dead_code": "code-health?tab=dead-code",
    "docs": "docs/drift",
    "decisions": "decisions",
    "hygiene": "architecture",
    "ownership": "ownership",
}


def route_path(route: str, params: Mapping[str, Any]) -> str:
    """The local web app path (under ``/repos/{id}/``) of a route key."""
    if route == "file":
        line = params.get("line")
        return f"files/{quote(str(params['path']), safe='/')}" + (f"?line={line}" if line else "")
    if route == "health_file":
        return f"code-health?file={quote(str(params['path']), safe='/')}"
    if route == "decision":
        return f"decisions/{quote(str(params['id']), safe='')}"
    if route == "dead_code":
        return _SECTION_ROUTES["dead_code"]
    if route == "docs_drift":
        return _SECTION_ROUTES["docs"]
    return route  # security, ownership


def _href(doc: Mapping[str, Any], path: str) -> str | None:
    base = doc.get("app_url")
    return f"{base.rstrip('/')}/{path}" if base else None


def _item_href(doc: Mapping[str, Any], item: Mapping[str, Any]) -> str | None:
    link = item.get("link") or {}
    return _href(doc, route_path(link.get("route", ""), link.get("params") or {}))


def _loc_href(doc: Mapping[str, Any], path: str, line: int | None) -> str | None:
    return _href(doc, route_path("file", {"path": path, "line": line}))


def _chips(item: Mapping[str, Any]) -> tuple[list[tuple[str, int | None, list[str]]], int]:
    """Locations grouped by place (a file's symbols on one chip), and how many are not shown."""
    grouped: dict[tuple[str, int | None], list[str]] = {}
    for loc in item.get("locations") or ():
        symbols = grouped.setdefault((loc["path"], loc.get("line")), [])
        if loc.get("symbol"):
            symbols.append(loc["symbol"])
    chips = [(p, ln, s) for (p, ln), s in grouped.items()]
    total = int(item.get("locations_total") or 0)
    listed = len(item.get("locations") or ())
    hidden = max(len(chips) - CHIPS_SHOWN, 0) + max(total - listed, 0)
    return chips[:CHIPS_SHOWN], hidden


#: How a status reads, after the person's answer (:mod:`.marks`).
_STATUS_LABELS = {
    "done": "done",
    "acknowledged": "acknowledged",
    "false_positive": "false positive",
    "snoozed": "snoozed",
    "resolved": "resolved",
}


def status_text(item: Mapping[str, Any]) -> str | None:
    """``acknowledged: <reason>``, ``snoozed until 2026-12-04``; ``None`` while open."""
    status = item.get("status") or "open"
    if status == "open":
        return None
    label = _STATUS_LABELS.get(status, status)
    if status == "snoozed" and item.get("status_until"):
        label += f" until {str(item['status_until'])[:10]}"
    if item.get("status_reason"):
        label += f": {_one(item['status_reason'])}"
    return label


def _badges(item: Mapping[str, Any]) -> list[str]:
    status = status_text(item)
    out = [status] if status else []
    if item.get("change") == "new":
        out.append("new")
    if item.get("severity") == "act_now" and status is None:
        out.append("do now")
    return [*out, item["basis"], f"{item['confidence']} confidence"]


def progress_text(doc: Mapping[str, Any]) -> str | None:
    """What moved since the stored report, on one line; ``None`` when nothing did."""
    baseline = doc.get("baseline") or {}
    if not baseline.get("stamp"):
        return None
    counts: dict[str, int] = {"resolved": len(baseline.get("resolved") or ())}
    new = 0
    for section in doc.get("sections") or ():
        for item in section.get("items") or ():
            status = item.get("status") or "open"
            if status != "open":
                counts[status] = counts.get(status, 0) + 1
            new += item.get("change") == "new"
    counts["new"] = new
    parts = [f"{n:,} {_STATUS_LABELS.get(k, k)}" for k, n in counts.items() if n]
    if not parts:
        return None
    commit = (baseline["stamp"].get("commit") or "")[:8] or "an unknown commit"
    return f"Since the first look at {commit}: {', '.join(parts)}."


def _shown(section: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], int]:
    items = section.get("items") or []
    return items[:SECTION_CAP], max(len(items) - SECTION_CAP, 0)


def _prose(doc: Mapping[str, Any]) -> Mapping[str, Any]:
    return doc.get("prose") or {}


def _text(doc: Mapping[str, Any], item: Mapping[str, Any]) -> str:
    return str((_prose(doc).get("items") or {}).get(item["id"]) or "")


def _title(doc: Mapping[str, Any], key: str) -> str:
    return str((_prose(doc).get("titles") or {}).get(key) or key)


def _by_id(doc: Mapping[str, Any]) -> dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]]:
    return {i["id"]: (s, i) for s in doc.get("sections") or () for i in s.get("items") or ()}


def _meta(doc: Mapping[str, Any]) -> list[str]:
    repo, stamp = doc.get("repo") or {}, doc.get("stamp") or {}
    out = []
    commit = (stamp.get("commit") or "")[:8]
    if repo.get("branch") or commit:
        out.append(f"{repo.get('branch') or 'HEAD'} @ {commit or 'unknown commit'}")
    if stamp.get("indexed_at"):
        out.append(f"indexed {stamp['indexed_at'][:10]}")
    if repo.get("files"):
        out.append(f"{int(repo['files']):,} files")
    health = repo.get("health") or {}
    if health.get("score") is not None:
        band = f" ({health['band_label']})" if health.get("band_label") else ""
        out.append(f"health {float(health['score']):.1f}/10{band}")
    return out


def writer_line(stamp: Mapping[str, Any]) -> str:
    """Who wrote the prose and what it cost."""
    generator = stamp.get("generator") or "template"
    if generator == "template":
        return "Written from the index by a template: no model, $0."
    cost = stamp.get("cost")
    spent = f", ${cost:.3f}" if isinstance(cost, int | float) else ""
    return (
        f"Written by {generator} from the index findings{spent}. "
        "Paths, links and prompts come from the findings, not the model."
    )


_SINCE_LAST = "repowise report repo --since-last"


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def _one(text: Any) -> str:
    """*text* on one line. Prose keeps its backtick spans: they mark paths."""
    return " ".join(str(text).split())


def _md_link(label: str, href: str | None) -> str:
    return f"[{label}]({href})" if href else label


def _md_chip(doc: Mapping[str, Any], path: str, line: int | None, symbols: Sequence[str]) -> str:
    at = f"{path}:{line}" if line else path
    chip = _md_link(_code(at), _loc_href(doc, path, line))
    return chip + (f" ({', '.join(symbols)})" if symbols else "")


def _md_item(doc: Mapping[str, Any], item: Mapping[str, Any]) -> list[str]:
    chips, hidden = _chips(item)
    where = [_md_chip(doc, p, ln, s) for p, ln, s in chips]
    if hidden:
        where.append(f"and {hidden:,} more")
    badges = " · ".join(_badges(item))
    lines = [f"- {_one(_text(doc, item))}"]
    if where:
        lines.append(f"  {' · '.join(where)}")
    verify = (item.get("title_facts") or {}).get("verify")
    if verify:
        lines.append(f"  verify: {_code(verify)}")
    open_link = _item_href(doc, item)
    lines.append(f"  {badges}" + (f" · [Open in app]({open_link})" if open_link else ""))
    return lines


def _top(doc: Mapping[str, Any]) -> list[tuple[Mapping[str, Any], Mapping[str, Any]]]:
    """The "Do these first" items with their sections, in order."""
    by_id = _by_id(doc)
    return [by_id[i] for i in doc.get("top") or () if i in by_id]


def _md_top(doc: Mapping[str, Any]) -> list[str]:
    top = _top(doc)
    if not top:
        return []
    out = ["", "## Do these first", ""]
    for n, (section, item) in enumerate(top, 1):
        open_link = _item_href(doc, item)
        # Each line names its place, so this list reads on its own as a summary.
        chips, hidden = _chips(item)
        places = [f"{p}:{ln}" if ln else p for p, ln, _ in chips[:1]]
        more = len(chips) - 1 + hidden
        where = f" ({_code(places[0])}{f' and {more:,} more' if more > 0 else ''})" if places else ""
        out.append(
            f"{n}. {_title(doc, section['key']).split(':')[0]}: {_one(_text(doc, item))}{where}"
            + (f" [Open]({open_link})" if open_link else "")
        )
    return out


def _md_section(doc: Mapping[str, Any], n: int, section: Mapping[str, Any]) -> list[str]:
    key = section["key"]
    out = ["", f"## {n}. {_one(_title(doc, key))}"]
    intro = (_prose(doc).get("sections") or {}).get(key)
    if intro:
        out += ["", _one(intro)]
    shown, more = _shown(section)
    if shown:
        out.append("")
    for item in shown:
        out += _md_item(doc, item)
    if more:
        label = f"{more:,} more in the app"
        out += ["", _md_link(label, _href(doc, _SECTION_ROUTES.get(key, ""))) + "."]
    return out


def render_first_look_markdown(doc: dict[str, Any]) -> str:
    prose = _prose(doc)
    repo, stamp = doc.get("repo") or {}, doc.get("stamp") or {}
    out = [
        f"# Repo report: {_one(repo.get('name') or '')}",
        "",
        " · ".join(_meta(doc)),
        "",
        writer_line(stamp),
        *(["", progress] if (progress := progress_text(doc)) else []),
        "",
        _one(prose.get("summary") or ""),
    ]
    if prose.get("headline"):
        out += ["", f"{_one(prose['headline'])} (measured)"]
    out += _md_top(doc)
    for n, section in enumerate(doc.get("sections") or (), 1):
        out += _md_section(doc, n, section)
    out += _md_tail(doc)
    return "\n".join(out).rstrip() + "\n"


def _md_resolved(doc: Mapping[str, Any]) -> list[str]:
    """Baseline items the index no longer finds, as the report wrote them."""
    resolved = (doc.get("baseline") or {}).get("resolved") or ()
    if not resolved:
        return []
    out = ["", "## Resolved since the first look", ""]
    for item in resolved:
        loc = (item.get("locations") or [{}])[0]
        at = f"{loc['path']}:{loc['line']}" if loc.get("line") else loc.get("path")
        out.append(f"- {_one(item.get('text') or '')}" + (f" ({_code(at)})" if at else ""))
    return out


def _md_tail(doc: Mapping[str, Any]) -> list[str]:
    prose = _prose(doc)
    out: list[str] = _md_resolved(doc)
    steps = doc.get("setup") or ()
    if steps:
        out += ["", "## Turn on more", ""]
        for step in steps:
            text = (prose.get("setup") or {}).get(step["action_id"]) or ""
            command = f": {_code(step['command'])}" if step.get("command") else ""
            out.append(f"- {_one(text)}{command}")
    unavailable = doc.get("unavailable") or {}
    if unavailable:
        out += ["", "Not read from this index: " + ", ".join(
            f"{_title(doc, k)} ({_one(v)})" for k, v in unavailable.items()
        )]
    commit = ((doc.get("stamp") or {}).get("commit") or "")[:8]
    out += ["", "---", "", f"Report at {commit or 'an unknown commit'}. Since then: {_code(_SINCE_LAST)}"]
    return out


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

_LIGHT = """--bg: #fbf6f1; --surface: #ffffff; --inset: #f0ebe6;
  --line: rgba(88, 67, 108, 0.12); --line-hover: rgba(88, 67, 108, 0.22);
  --ink: #241b2c; --ink-2: #5e5360; --ink-3: #8c7f88; --inverse: #ffffff;
  --accent: #a16215; --action: #241b2c; --soft: rgba(36, 27, 44, 0.06);
  --error: #b23a2e; --warning: #856700; color-scheme: light;"""
_DARK = """--bg: #0e0e0f; --surface: #141416; --inset: #0a0a0b;
  --line: rgba(255, 255, 255, 0.07); --line-hover: rgba(255, 255, 255, 0.15);
  --ink: #f2f2f3; --ink-2: #b4b4b9; --ink-3: #7c7c82; --inverse: #0e0e0f;
  --accent: #f59520; --action: #f2f2f3; --soft: rgba(255, 255, 255, 0.08);
  --error: #e06a5a; --warning: #e8b339; color-scheme: dark;"""

_CSS = (
    f":root {{ {_LIGHT} }}\n"
    f"@media (prefers-color-scheme: dark) {{ :root {{ {_DARK} }} }}\n"
    """* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif; overflow-x: hidden; }
a { color: var(--accent); text-decoration: none; } a:hover { text-decoration: underline; }
code, pre, .mono, .chip, .meta, .eyebrow, .badge, .tag {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
.page { max-width: 880px; margin: 0 auto; padding: 32px 16px 80px; }
.eyebrow, .badge, .tag, .sec { font-size: 10px; letter-spacing: .06em; text-transform: uppercase; }
.eyebrow, .sec { color: var(--ink-3); }
h1 { font-size: 32px; line-height: 1.2; font-weight: 600; margin: 6px 0 8px; overflow-wrap: anywhere; }
.meta { font-size: 12px; color: var(--ink-2); display: flex; flex-wrap: wrap; gap: 4px 14px; }
.writer { margin-top: 14px; font-size: 12px; color: var(--ink-2); display: flex; gap: 6px;
  align-items: center; flex-wrap: wrap; }
.tag { padding: 2px 6px; border-radius: 4px; background: var(--soft); color: var(--ink-2); }
.summary { font-size: 16px; line-height: 1.65; margin: 22px 0 0; max-width: 68ch; }
.headline { font-size: 12px; color: var(--ink-2); margin-top: 10px; }
.first h2 { font-size: 18px; margin: 30px 0 6px; font-weight: 600; }
.first ol { margin: 0; padding-left: 22px; }
.first li { padding: 10px 0; border-top: 1px solid var(--line); }
.first .sec { display: block; }
section.s { margin-top: 44px; }
.s-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px;
  flex-wrap: wrap; border-bottom: 1px solid var(--line); padding-bottom: 8px; }
.s-head h2 { font-size: 22px; font-weight: 600; margin: 0; }
.s-head a { font-size: 12px; }
.s-intro { margin: 12px 0 4px; color: var(--ink-2); max-width: 68ch; }
.s-more { font-size: 12px; margin-top: 10px; color: var(--ink-3); }
.item { padding: 16px 0; border-bottom: 1px solid var(--line); }
.item:last-of-type { border-bottom: 0; }
.item .t { font-weight: 500; overflow-wrap: anywhere; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.chip { display: inline-flex; gap: 6px; max-width: 100%; font-size: 12px; padding: 3px 8px;
  border-radius: 6px; background: var(--inset); color: var(--ink); border: 1px solid var(--line);
  overflow-wrap: anywhere; word-break: break-all; }
a.chip:hover { border-color: var(--line-hover); text-decoration: none; }
.chip .ln { color: var(--accent); } .chip .note { color: var(--ink-3); word-break: normal; }
.verify { font-size: 12px; color: var(--ink-3); margin-top: 8px; overflow-wrap: anywhere; }
.row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 14px; margin-top: 12px; }
.badge { padding: 2px 6px; border-radius: 4px; border: 1px solid var(--line); color: var(--ink-2); }
.badge.now { color: var(--error); border-color: var(--error); }
.btn { display: inline-block; border-radius: 8px; padding: 6px 11px; font-size: 12px;
  background: var(--soft); color: var(--ink); cursor: pointer; }
details.prompt { margin-top: 10px; }
details.prompt > summary { list-style: none; display: inline-block; border-radius: 8px;
  padding: 6px 11px; font-size: 12px; cursor: pointer; background: var(--action); color: var(--inverse); }
details.prompt > summary::-webkit-details-marker { display: none; }
details.prompt pre { margin: 8px 0 0; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12.5px;
  line-height: 1.6; background: var(--inset); border-radius: 8px; padding: 12px; }
.more-strip { margin-top: 48px; padding-top: 12px; border-top: 1px solid var(--line); }
.more-strip h2 { font-size: 18px; margin: 0 0 4px; font-weight: 600; }
.more-strip ul { margin: 6px 0 0; padding-left: 18px; color: var(--ink-2); }
code { font-size: .85em; background: var(--inset); padding: 1px 5px; border-radius: 4px;
  overflow-wrap: anywhere; }
.note-line { font-size: 12px; color: var(--warning); margin-top: 24px; }
footer { margin-top: 48px; font-size: 12px; color: var(--ink-3); display: flex; flex-wrap: wrap;
  gap: 6px 16px; }
@media (max-width: 640px) { h1 { font-size: 26px; } .s-head h2 { font-size: 20px; } }
@media print { body { background: #fff; } details.prompt pre { display: none; } }
"""
)

_CODE_SPAN = re.compile(r"(`+)\s?(.+?)\s?\1")


def _inline(text: str) -> str:
    """*text* escaped, with its backtick spans as ``<code>`` (prose marks paths that way)."""
    out, last = [], 0
    for m in _CODE_SPAN.finditer(text):
        out.append(html.escape(text[last : m.start()]))
        out.append(f"<code>{html.escape(m.group(2))}</code>")
        last = m.end()
    out.append(html.escape(text[last:]))
    return "".join(out)


def _h_link(label_html: str, href: str | None, cls: str = "") -> str:
    attr = f' class="{cls}"' if cls else ""
    if href:
        return f'<a{attr} href="{html.escape(href)}">{label_html}</a>'
    return f"<span{attr}>{label_html}</span>"


def _h_item(doc: Mapping[str, Any], item: Mapping[str, Any]) -> str:
    e = html.escape
    chips, hidden = _chips(item)
    parts = [f'<div class="item" id="{e(item["id"])}">', f'<div class="t">{_inline(_text(doc, item))}</div>']
    if chips or hidden:
        parts.append('<div class="chips">')
        for path, line, symbols in chips:
            inner = e(path) + (f'<span class="ln">:{line}</span>' if line else "")
            if symbols:
                inner += f'<span class="note">{e(", ".join(symbols))}</span>'
            parts.append(_h_link(inner, _loc_href(doc, path, line), "chip"))
        if hidden:
            parts.append(f'<span class="chip"><span class="note">and {hidden:,} more</span></span>')
        parts.append("</div>")
    verify = (item.get("title_facts") or {}).get("verify")
    if verify:
        parts.append(f'<div class="verify">verify: {e(verify)}</div>')
    badges = "".join(
        f'<span class="badge{" now" if b == "do now" else ""}">{e(b)}</span>' for b in _badges(item)
    )
    open_link = _item_href(doc, item)
    parts.append(
        f'<div class="row">{badges}'
        + (f'<a class="btn" href="{e(open_link)}">Open in app</a>' if open_link else "")
        + "</div>"
    )
    prompt = (doc.get("prompts") or {}).get(item["id"])
    if prompt:
        parts.append(
            '<details class="prompt"><summary>Fix with your agent</summary>'
            f"<pre>{e(prompt)}</pre></details>"
        )
    parts.append("</div>")
    return "".join(parts)


def _h_top(doc: Mapping[str, Any]) -> list[str]:
    e = html.escape
    top = _top(doc)
    if not top:
        return []
    out = ['<div class="first"><h2>Do these first</h2><ol>']
    for section, item in top:
        out.append(
            f'<li><span class="sec">{e(_title(doc, section["key"]).split(":")[0])}</span>'
            f'{_inline(_text(doc, item))} <a href="#{e(item["id"])}">Go to item</a></li>'
        )
    return [*out, "</ol></div>"]


def _h_section(doc: Mapping[str, Any], section: Mapping[str, Any]) -> list[str]:
    e = html.escape
    key = section["key"]
    app = _href(doc, _SECTION_ROUTES.get(key, ""))
    out = [
        f'<section class="s" id="sec-{e(key)}"><div class="s-head"><h2>{e(_title(doc, key))}</h2>'
        + (f'<a href="{e(app)}">open in app</a>' if app else "")
        + "</div>"
    ]
    intro = (_prose(doc).get("sections") or {}).get(key)
    if intro:
        out.append(f'<p class="s-intro">{_inline(intro)}</p>')
    shown, more = _shown(section)
    out += [_h_item(doc, item) for item in shown]
    if more:
        out.append(f'<div class="s-more">{_h_link(e(f"{more:,} more in the app"), app)}</div>')
    return [*out, "</section>"]


def render_first_look_html(doc: dict[str, Any]) -> str:
    e = html.escape
    prose = _prose(doc)
    repo, stamp = doc.get("repo") or {}, doc.get("stamp") or {}
    name = repo.get("name") or ""
    tag = "template" if (stamp.get("generator") or "template") == "template" else "model"
    body = [
        '<div class="page"><header>',
        '<div class="eyebrow">Repo report</div>',
        f"<h1>{e(name)}</h1>",
        '<div class="meta">' + "".join(f"<span>{e(m)}</span>" for m in _meta(doc)) + "</div>",
        f'<div class="writer"><span class="tag">{tag}</span><span>{e(writer_line(stamp))}</span></div>',
        "</header>",
        f'<p class="summary">{_inline(prose.get("summary") or "")}</p>',
    ]
    if prose.get("headline"):
        body.append(
            f'<div class="headline">{_inline(prose["headline"])} <span class="badge">measured</span></div>'
        )
    body += _h_top(doc)
    for section in doc.get("sections") or ():
        body += _h_section(doc, section)
    body += _h_tail(doc)
    body.append("</div>")
    return html_page(f"Repo report: {name}", _CSS, body)


def _h_tail(doc: Mapping[str, Any]) -> list[str]:
    e = html.escape
    prose = _prose(doc)
    out: list[str] = []
    steps = doc.get("setup") or ()
    if steps:
        out.append('<div class="more-strip"><h2>Turn on more</h2><ul>')
        for step in steps:
            text = (prose.get("setup") or {}).get(step["action_id"]) or ""
            command = f": <code>{e(step['command'])}</code>" if step.get("command") else ""
            out.append(f"<li>{_inline(text)}{command}</li>")
        out.append("</ul></div>")
    unavailable = doc.get("unavailable") or {}
    if unavailable:
        out.append(
            '<p class="note-line">Not read from this index: '
            + e(", ".join(f"{_title(doc, k)} ({v})" for k, v in unavailable.items()))
            + "</p>"
        )
    if doc.get("report_prompt"):
        out.append(
            '<details class="prompt"><summary>Work through this report with your agent</summary>'
            f"<pre>{e(doc['report_prompt'])}</pre></details>"
        )
    commit = ((doc.get("stamp") or {}).get("commit") or "")[:8]
    out.append(
        f"<footer><span>Report at <code>{e(commit or 'unknown')}</code></span>"
        f"<span>Since then: <code>{e(_SINCE_LAST)}</code></span></footer>"
    )
    return out


report_renderers.register(MARKDOWN, render_first_look_markdown)
report_renderers.register(HTML, render_first_look_html)


__all__ = [
    "HTML",
    "MARKDOWN",
    "PREFIX",
    "render_first_look_html",
    "render_first_look_markdown",
    "route_path",
    "writer_line",
]
