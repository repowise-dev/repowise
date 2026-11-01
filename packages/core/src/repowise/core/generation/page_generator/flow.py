"""Flow pages: one operation walked end to end, mixed into :class:`PageGenerator`.

The operations and their steps are chosen in :mod:`..flow_pages`. This renders
a chosen plan: a model writes the walkthrough when a provider is configured,
and a keyless run renders the same steps as a numbered list with a diagram
drawn from them.

What the model reads (:mod:`.flow_material`): an excerpt of every step under
one token cap, the root's options one line each, and the documentation
sections that show the operation in use. A citation the page makes is
grounded on the excerpt windows, the step spans and those doc sections.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

from ..architecture_mermaid import _clean
from ..flow_entries import doc_sections, line_reader
from ..flow_pages import FLOW_PAGE_TYPE, FlowPlan, FlowStep
from ..models import GENERATION_LEVELS, compute_source_hash
from ..page_sources import (
    _SPAN_CITE_RE,
    Span,
    _unique_file,
    ground_span_citations,
    source_entry,
)
from .flow_material import FlowMaterial, build_material
from .pertype import _stub_fallback
from .reuse import files_digest, model_reuse_key

_DIAGRAM_MARKER = "<!-- flow-diagram -->"
_H2_RE = re.compile(r"(?m)^## ")
# The diagram this module draws, and only it: its first box is always ``S1``.
_PRIOR_DIAGRAM_RE = re.compile(r"```mermaid\nflowchart TD\n  S1\[.*?```\n", re.DOTALL)
# Prose range a style that states length (the default) applies; others ignore it.
_WORD_BUDGET = (800, 1000)

#: ``file -> (module page id, module title)`` for the pages a step can point at.
ModuleIndex = Mapping[str, tuple[str, str]]


def _cite(step: FlowStep) -> str:
    if step.start_line is None:
        return step.path
    return f"{step.path}:{step.start_line}-{step.end_line or step.start_line}"


def _step_rows(plan: FlowPlan, graph: Any, modules: ModuleIndex) -> list[dict[str, Any]]:
    """One row per step: what the stub lists and the prompt's step headers read."""
    rows: list[dict[str, Any]] = []
    for number, step in enumerate(plan.steps, start=1):
        attrs = graph.nodes.get(step.symbol_id, {}) if step.symbol_id in graph else {}
        module = modules.get(step.path)
        rows.append(
            {
                "step": number,
                "name": step.name,
                "path": step.path,
                "cite": _cite(step),
                "called_by": step.parent + 1 if step.parent >= 0 else None,
                "how_called": step.context,
                "module": module[1] if module else "",
                "doc": re.sub(r"\s+", " ", str(attrs.get("docstring") or "")).strip()[:600],
            }
        )
    return rows


def _neighbours(rows: list[dict[str, Any]], related: Sequence[str]) -> list[dict[str, str]]:
    """Module pages the steps live in (with which steps), then the other walkthroughs."""
    by_module: dict[str, list[int]] = {}
    for row in rows:
        if row["module"]:
            by_module.setdefault(row["module"], []).append(row["step"])
    out = [
        {"title": title, "summary": "module page holding steps " + ", ".join(map(str, steps))}
        for title, steps in by_module.items()
    ]
    out += [{"title": t, "summary": "walkthrough of another main operation"} for t in related]
    return out[:8]


def flow_context(
    plan: FlowPlan,
    graph: Any,
    modules: ModuleIndex,
    source_map: Mapping[str, bytes],
    *,
    material: FlowMaterial | None = None,
    related: Sequence[str] = (),
) -> dict[str, Any]:
    """What both renderings read: one row per step plus, for the model, the material."""
    rows = _step_rows(plan, graph, modules)
    if material is not None:  # the keyless page lists steps without code
        for row, code in zip(rows, material.excerpts, strict=True):
            row["code"] = code
        if rows:
            rows[0]["cite"] = material.root_cite
    return {
        "title": plan.title,
        "kind": plan.kind,
        "public": plan.public or (rows[0]["name"] if rows else ""),
        "steps": rows,
        "exits": [r for r in rows if r["how_called"] in ("early_exit", "handler")],
        "surface": material.surface if material else "",
        "docs": material.docs if material else (),
        "skipped": list(plan.skipped),
        "modules": sorted({row["module"] for row in rows if row["module"]}),
        "neighbours": _neighbours(rows, related),
    }


def flow_diagram(plan: FlowPlan, modules: ModuleIndex) -> str:
    """A top-down flowchart of the steps, each box a function, each box a link."""
    lines = ["flowchart TD"]
    for number, step in enumerate(plan.steps, start=1):
        name = _clean(step.name, 3)
        file = _clean(step.path.rsplit("/", 1)[-1], 3)
        lines.append(f'  S{number}["<b>{name}</b><br/><small>{file}</small>"]')
    for number, step in enumerate(plan.steps, start=1):
        if step.parent >= 0:
            lines.append(f"  S{step.parent + 1} --> S{number}")
    for number, step in enumerate(plan.steps, start=1):
        module = modules.get(step.path)
        if module:
            href = "?page=" + quote(module[0], safe="")
            lines.append(f'  click S{number} "{href}" "Open the {_clean(module[1], 6)} page"')
    return "```mermaid\n" + "\n".join(lines) + "\n```\n"


def embed_flow_diagram(content: str, diagram: str) -> str:
    """Put the diagram at the marker, else after the opening and before the first section.

    Idempotent: a page reused from a prior run already carries the diagram that
    run drew, and it is replaced by the one drawn from the current steps.
    """
    content = _PRIOR_DIAGRAM_RE.sub("", content, count=1)
    if _DIAGRAM_MARKER in content:
        return content.replace(_DIAGRAM_MARKER, diagram.rstrip("\n"), 1)
    match = _H2_RE.search(content)
    if match is None:
        return content.rstrip("\n") + "\n\n" + diagram
    return f"{content[: match.start()]}{diagram}\n{content[match.start() :]}"


def _citable_spans(plan: FlowPlan, material: FlowMaterial) -> dict[str, list[Span]]:
    """Every span a citation may land on: steps, the root with its decorators, doc sections."""
    spans: dict[str, list[Span]] = {}
    for step in plan.steps:
        if step.start_line is not None:
            spans.setdefault(step.path, []).append(
                (step.start_line, step.end_line or step.start_line, step.name)
            )
    for path, start, end, label in material.extra_spans:
        spans.setdefault(path, []).append((start, end, label))
    return spans


def ground_flow_citations(content: str, spans_by_file: Mapping[str, list[Span]]) -> str:
    """Keep a citation that sits inside a step or doc span as written; ground the rest.

    The excerpts number the lines a step calls from, so a narrow citation
    (``command.py:532-564``) points at the lines that carry a paragraph's
    claim. Snapping it out to the whole function, as the shared grounding
    does for spans it cannot place, would make every paragraph on a long
    step cite the same range.
    """

    def ground(match: re.Match[str]) -> str:
        path = _unique_file(match.group(1).removeprefix("./"), spans_by_file)
        start = int(match.group(2))
        end = int(match.group(3) or start)
        spans = spans_by_file.get(path or "", ())
        if any(a <= start <= end <= b for a, b, _ in spans):
            return f"`{path}:{start}-{end}`"
        return ground_span_citations(match.group(0), spans_by_file)[0]

    return _SPAN_CITE_RE.sub(ground, content)


def _step_sources(plan: FlowPlan) -> list[dict[str, object]]:
    return [
        source_entry(
            step.path,
            "step",
            start_line=step.start_line,
            end_line=step.end_line,
            symbol=step.name,
        )
        for step in plan.steps
    ]


def _subject(plan: FlowPlan, source_map: Mapping[str, bytes], material: FlowMaterial) -> str:
    """The page's subject: the steps, their files' bytes, and the rest of what the prompt reads.

    The docs, options summary and call kinds feed the prompt too, so an edit
    to a doc section or an option's help text regenerates the page. The other
    walkthroughs' titles are left out: one operation added or renamed would
    otherwise rewrite every flow page for the sake of a "read next" link.
    """
    steps = "|".join(
        f"{s.symbol_id}@{s.start_line}-{s.end_line}:{s.context}" for s in plan.steps
    )
    extra = "\x00".join([material.surface, *(d.text for d in material.docs)])
    digest = hashlib.sha256(extra.encode()).hexdigest()[:16]
    return f"{plan.entry_id}|{steps}|{files_digest(plan.files, source_map)}|{digest}"


class FlowPageMixin:
    """``generate_flow_page`` for :class:`PageGenerator`."""

    def _stamp_flow(self, page: Any, plan: FlowPlan, diagram: str | None = None) -> Any:
        if diagram is not None:
            page.content = embed_flow_diagram(page.content, diagram)
        page.structural_key = plan.entry_id
        page.metadata["file_paths"] = plan.files
        page.metadata["flow_entry"] = plan.entry_id
        page.metadata["sources"] = _step_sources(plan)
        return page

    def _flow_stub(self, plan: FlowPlan, ctx: dict[str, Any]) -> Any:
        """The keyless rendering: the traced steps as a numbered list."""
        return self._stub_page(
            page_type=FLOW_PAGE_TYPE,
            target_path=plan.entry_id,
            title=plan.title,
            template="flow_page.j2",
            ctx=ctx,
        )

    async def generate_flow_page(
        self,
        plan: FlowPlan,
        *,
        graph: Any,
        modules: ModuleIndex,
        source_map: Mapping[str, bytes],
        repo_path: Any = None,
        related: Sequence[str] = (),
    ) -> Any:
        diagram = flow_diagram(plan, modules)
        if self._config.deterministic:
            ctx = flow_context(plan, graph, modules, source_map)
            return self._stamp_flow(self._flow_stub(plan, ctx), plan, diagram)
        material = build_material(
            plan, graph, line_reader(repo_path, source_map), doc_sections(repo_path)
        )
        ctx = flow_context(
            plan,
            graph,
            modules,
            source_map,
            material=material,
            related=related,
        )
        user_prompt = self._render("flow_page.j2", ctx=ctx, word_budget=_WORD_BUDGET)
        subject = _subject(plan, source_map, material)
        reuse_key = model_reuse_key(self, subject, FLOW_PAGE_TYPE, "flow_page.j2")
        try:
            response = await self._call_provider(
                FLOW_PAGE_TYPE,
                user_prompt,
                str(uuid.uuid4()),
                target_path=plan.entry_id,
                reuse_content_hash=reuse_key,
            )
        except Exception as exc:
            stub = _stub_fallback(self._flow_stub(plan, ctx), FLOW_PAGE_TYPE, exc)
            return self._stamp_flow(stub, plan, diagram)
        page = self._build_generated_page(
            FLOW_PAGE_TYPE,
            plan.entry_id,
            plan.title,
            response,
            compute_source_hash(user_prompt),
            GENERATION_LEVELS[FLOW_PAGE_TYPE],
            content_hash=reuse_key,
        )
        page.content = ground_flow_citations(page.content, _citable_spans(plan, material))
        # The model draws its own data-flow diagram; a page reused from an
        # older run may still carry the step diagram, which is dropped.
        page.content = _PRIOR_DIAGRAM_RE.sub("", page.content, count=1)
        return self._stamp_flow(page, plan)
