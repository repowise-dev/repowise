"""Name, compile and place the overview's system map.

:mod:`.architecture_map` decides the boxes and arrows. This module turns that
structure into the page's diagram: an optional model pass renames the boxes and
verbs through a JSON block the overview call appends to its reply, the diagram
compiles to mermaid in code, and the section lands under the page's first
section. Model text is sanitised before it reaches mermaid or markdown, so the
compiled source is valid by construction.

The compiled mermaid carries no colors. Each tier gets a ``classDef`` that only
varies the stroke, so the renderer's theme owns the palette in light and dark.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any
from urllib.parse import quote

from .architecture_map import TIERS, MapEdge, MapNode, SystemMap
from .models import compute_page_id

HEADING = "## System map"
# Written by earlier versions at the end of the page; replaced on re-embed.
_LEGACY_HEADING = "## Architecture map"

_TIER_TITLES = {
    "actor": "Who uses it",
    "surface": "Ways in",
    "shared": "Shared libraries",
    "service": "Server",
    "engine": "Engine",
    "store": "Data store",
}
# Stroke only, no color: the renderer's theme tokens own the palette.
_TIER_STYLE = {
    "actor": "stroke-dasharray:4 4",
    "surface": "stroke-width:2px",
    "shared": "stroke-width:1px",
    "service": "stroke-width:2px",
    "engine": "stroke-width:1px",
    "store": "stroke-width:1px",
}
_SHAPES = {"actor": ('(["', '"])'), "store": ('[("', '")]')}

# A fenced JSON object, with or without a language tag. A reply cut off inside
# the block leaves an unterminated fence at the very end.
_NAMES_FENCE_RE = re.compile(
    r"\n?[ \t]*```(?:json)?[ \t]*\n\s*(?P<body>\{.*?\})\s*```[ \t]*",
    re.DOTALL | re.IGNORECASE,
)
_TRUNCATED_FENCE_RE = re.compile(r"\n?[ \t]*```(?:json)?[ \t]*\n\s*\{[^`]*\Z", re.IGNORECASE)
_NAMES_KEYS = ("nodes", "edges", "caption")
# Kept out of labels: each is syntax in a mermaid label or an HTML label.
_UNSAFE_CHARS_RE = re.compile(r"[\"'`<>|\[\]{}#;:\\]")
# Kept out of the caption, which is markdown: fences and HTML.
_CAPTION_UNSAFE_RE = re.compile(r"[`<>]")
_H2_RE = re.compile(r"(?m)^## ")


# -- naming -------------------------------------------------------------------


def naming_payload(system_map: SystemMap) -> str:
    """The structure a model names, as JSON for the overview prompt."""
    return json.dumps(
        {
            "nodes": [
                {"id": n.id, "tier": n.tier, "name": n.name, "facts": list(n.facts)}
                for n in system_map.nodes
            ],
            "edges": [
                {"from": e.source, "to": e.target, "evidence": e.basis} for e in system_map.edges
            ],
        },
        indent=1,
    )


def split_names(content: str) -> tuple[str, dict[str, Any] | None]:
    """Strip the names block from a model reply and parse it.

    The last fenced object that names the map is taken wherever it sits, so
    prose written after it is kept and a JSON example in the prose is left
    alone. A reply cut off inside the block loses the partial block.
    """
    for match in reversed(list(_NAMES_FENCE_RE.finditer(content))):
        try:
            parsed = json.loads(match.group("body"))
        except ValueError:
            continue
        if isinstance(parsed, dict) and any(k in parsed for k in _NAMES_KEYS):
            body = (content[: match.start()].rstrip() + "\n" + content[match.end() :]).rstrip()
            return body + "\n", parsed
    truncated = _TRUNCATED_FENCE_RE.search(content)
    if truncated and any(f'"{k}"' in truncated.group(0) for k in _NAMES_KEYS):
        return content[: truncated.start()].rstrip() + "\n", None
    return content, None


def apply_names(system_map: SystemMap, raw: dict[str, Any] | None) -> SystemMap:
    """Swap in model names, verbs and caption; unknown ids and pairs are ignored."""
    if not raw:
        return system_map
    by_id = {n.id: n for n in system_map.nodes}
    names: dict[str, tuple[str, str]] = {}
    for item in raw.get("nodes") or []:
        if not isinstance(item, dict) or item.get("id") not in by_id:
            continue
        label = _clean(item.get("label"), 4)
        if label:
            names[item["id"]] = (label, _clean(item.get("role"), 6) or by_id[item["id"]].role)
    pairs = {(e.source, e.target) for e in system_map.edges}
    verbs: dict[tuple[str, str], str] = {}
    for item in raw.get("edges") or []:
        if not isinstance(item, dict):
            continue
        pair = (item.get("from"), item.get("to"))
        verb = _clean(item.get("verb"), 3)
        if pair in pairs and verb:
            verbs[pair] = verb
    text = " ".join(_CAPTION_UNSAFE_RE.sub("", str(raw.get("caption") or "")).split())
    caption = " ".join(re.split(r"(?<=[.!?])\s+", text.lstrip("#").strip())[:2])
    return replace(system_map.with_names(names, verbs), caption=caption)


def _clean(value: Any, max_words: int) -> str:
    text = _UNSAFE_CHARS_RE.sub("", str(value or "")).replace("&", "and")
    return " ".join(text.split()[:max_words])


# -- compile ------------------------------------------------------------------


def compile_mermaid(system_map: SystemMap) -> str:
    """Mermaid flowchart source for *system_map*: one subgraph per tier."""
    lines = ["flowchart TB"]
    for tier in TIERS:
        members = [n for n in system_map.nodes if n.tier == tier]
        if not members:
            continue
        lines.append(f'  subgraph tier_{tier}["{_TIER_TITLES[tier]}"]')
        lines.append("    direction LR")
        lines += [f"    {_node(n)}" for n in members]
        lines.append("  end")
    lines += [f"  {_edge(e)}" for e in system_map.edges]
    for n in system_map.nodes:
        if n.page_id:
            href = "?page=" + quote(compute_page_id("module_page", n.page_id), safe="")
            lines.append(f'  click {n.id} "{href}" "Open the {_clean(n.name, 6)} page"')
    used = {n.tier for n in system_map.nodes}
    lines += [f"  classDef {t} {_TIER_STYLE[t]}" for t in TIERS if t in used]
    return "\n".join(lines)


def _node(node: MapNode) -> str:
    opener, closer = _SHAPES.get(node.tier, ('["', '"]'))
    label = f"<b>{_clean(node.name, 6)}</b>"
    if node.role:
        label += f"<br/><small>{_clean(node.role, 8)}</small>"
    return f"{node.id}{opener}{label}{closer}:::{node.tier}"


def _edge(edge: MapEdge) -> str:
    arrow = "==>" if edge.heavy else "-->"
    return f'{edge.source} {arrow}|"{_clean(edge.verb, 4)}"| {edge.target}'


# -- the page section ---------------------------------------------------------


def system_map_section(system_map: SystemMap, repo_name: str) -> str:
    """The markdown section for *system_map*: heading, caption and diagram.

    The caption says what the map leaves out, since a diagram that silently
    drops parts or arrows claims more coverage than it has.
    """
    caption = system_map.caption or f"The main parts of {repo_name} and how they connect."
    if system_map.omitted:
        n = system_map.omitted
        caption += f" {n} more part{'s are' if n != 1 else ' is'} not shown."
    if system_map.arrows_cut:
        caption += " Only the strongest links are drawn."
    body = compile_mermaid(system_map)
    return f"{HEADING}\n\n{caption}\n\n```mermaid\n{body}\n```\n"


def embed_system_map(content: str, section: str | None) -> str:
    """Place *section* under the page's first section, replacing any earlier map.

    Idempotent: a page that already carries a map (this one, or the older
    end-of-page architecture map) has it removed before the new one goes in.
    """
    content = _remove_section(_remove_section(content, HEADING), _LEGACY_HEADING)
    if not section:
        return content
    h2 = [m.start() for m in _H2_RE.finditer(content) if not _in_fence(content, m.start())]
    if len(h2) < 2:
        sep = "" if content.endswith("\n") else "\n"
        return f"{content}{sep}\n{section}"
    return f"{content[: h2[1]]}{section}\n{content[h2[1] :]}"


def _remove_section(content: str, heading: str) -> str:
    match = re.search(rf"(?m)^{re.escape(heading)}[ \t]*$", content)
    if match is None:
        return content
    end = len(content)
    for nxt in _H2_RE.finditer(content, match.end()):
        if not _in_fence(content, nxt.start()):
            end = nxt.start()
            break
    return content[: match.start()] + content[end:]


def _in_fence(content: str, pos: int) -> bool:
    return content.count("```", 0, pos) % 2 == 1
