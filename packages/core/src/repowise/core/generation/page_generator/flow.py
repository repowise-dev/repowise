"""Flow pages: one operation walked end to end, mixed into :class:`PageGenerator`.

The operations and their steps are chosen in :mod:`..flow_pages` from the call
graph alone. This renders a chosen plan: a model writes the walkthrough when a
provider is configured, and a keyless run renders the same steps as a numbered
list. Both carry the same diagram, drawn here from the steps so every box is a
real function, and the same line-span sources.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from ..architecture_mermaid import _clean
from ..flow_pages import FLOW_PAGE_TYPE, FlowPlan, FlowStep
from ..models import GENERATION_LEVELS, compute_source_hash
from ..page_sources import Span, ground_span_citations, source_entry
from .pertype import _stub_fallback
from .reuse import files_digest, model_reuse_key

#: Steps whose source body goes into the prompt; the rest carry signatures.
_EXCERPT_STEPS = 5
_EXCERPT_LINES = 30
_DIAGRAM_MARKER = "<!-- flow-diagram -->"
_H2_RE = re.compile(r"(?m)^## ")
# The diagram this module draws, and only it: its first box is always ``S1``.
_PRIOR_DIAGRAM_RE = re.compile(r"```mermaid\nflowchart TD\n  S1\[.*?```\n", re.DOTALL)

#: ``file -> (module page id, module title)`` for the pages a step can point at.
ModuleIndex = Mapping[str, tuple[str, str]]


def _cite(step: FlowStep) -> str:
    if step.start_line is None:
        return step.path
    return f"{step.path}:{step.start_line}-{step.end_line or step.start_line}"


def _excerpt(step: FlowStep, source_map: Mapping[str, bytes]) -> str:
    if step.start_line is None:
        return ""
    raw = source_map.get(step.path) or b""
    lines = raw.decode("utf-8", errors="replace").splitlines()
    end = min(step.end_line or step.start_line, step.start_line + _EXCERPT_LINES - 1)
    return "\n".join(lines[step.start_line - 1 : end])


def _first_line(text: Any) -> str:
    return str(text or "").strip().split("\n", 1)[0].strip()


def flow_context(
    plan: FlowPlan, graph: Any, modules: ModuleIndex, source_map: Mapping[str, bytes]
) -> dict[str, Any]:
    """What both renderings read: one row per step, plus the prompt's JSON."""
    rows: list[dict[str, Any]] = []
    for number, step in enumerate(plan.steps, start=1):
        attrs = graph.nodes.get(step.symbol_id, {}) if step.symbol_id in graph else {}
        module = modules.get(step.path)
        row: dict[str, Any] = {
            "step": number,
            "name": step.name,
            "path": step.path,
            "cite": _cite(step),
            "called_by": step.parent + 1 if step.parent >= 0 else None,
            "call_line": step.call_line,
            "module": module[1] if module else "",
            "signature": _first_line(attrs.get("signature")),
            "doc": _first_line(attrs.get("docstring")),
        }
        if number <= _EXCERPT_STEPS:
            row["code"] = _excerpt(step, source_map)
        rows.append(row)
    facts = [{k: v for k, v in row.items() if v not in (None, "")} for row in rows]
    return {
        "title": plan.title,
        "steps": rows,
        "modules": sorted({row["module"] for row in rows if row["module"]}),
        "facts_json": json.dumps({"operation": plan.title, "steps": facts}, indent=1),
    }


def flow_diagram(plan: FlowPlan, modules: ModuleIndex) -> str:
    """A top-down flowchart of the steps, each box a function, each box a link."""
    lines = ["flowchart TD"]
    for number, step in enumerate(plan.steps, start=1):
        label = f"<b>{_clean(step.name, 3)}</b><br/><small>{_clean(step.path.rsplit('/', 1)[-1], 3)}</small>"
        lines.append(f'  S{number}["{label}"]')
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


def _step_spans(plan: FlowPlan) -> dict[str, list[Span]]:
    spans: dict[str, list[Span]] = {}
    for step in plan.steps:
        if step.start_line is not None:
            spans.setdefault(step.path, []).append(
                (step.start_line, step.end_line or step.start_line, step.name)
            )
    return spans


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


def _material(plan: FlowPlan, source_map: Mapping[str, bytes]) -> str:
    """The page's subject: which steps, where they sit, and their files' bytes."""
    steps = "|".join(f"{s.symbol_id}@{s.start_line}-{s.end_line}" for s in plan.steps)
    return f"{plan.entry_id}|{steps}|{files_digest(plan.files, source_map)}"


class FlowPageMixin:
    """``generate_flow_page`` for :class:`PageGenerator`."""

    def _stamp_flow(self, page: Any, plan: FlowPlan, diagram: str) -> Any:
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
    ) -> Any:
        ctx = flow_context(plan, graph, modules, source_map)
        diagram = flow_diagram(plan, modules)
        if self._config.deterministic:
            return self._stamp_flow(self._flow_stub(plan, ctx), plan, diagram)
        user_prompt = self._render("flow_page.j2", ctx=ctx)
        reuse_key = model_reuse_key(
            self, _material(plan, source_map), FLOW_PAGE_TYPE, "flow_page.j2"
        )
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
        page.content, _cited = ground_span_citations(page.content, _step_spans(plan))
        return self._stamp_flow(page, plan, diagram)
