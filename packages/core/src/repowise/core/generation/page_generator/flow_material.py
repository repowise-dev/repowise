"""What a flow page's model reads besides the step list.

* **Excerpts.** Every step gets code: its head (signature and first lines),
  a window of two lines around each call to a traced step, and the single
  line of every other call. The heads shrink together until the whole set
  fits :data:`CODE_TOKEN_CAP`, so a 20-step operation costs about what a
  5-step one did with whole bodies, and no step is reduced to a signature.
* **Surface.** The root's options, one line each with the first sentence of
  the help text, read from the decorators the parser kept (or, for a Go
  command, the flag registrations in its file). The raw decorator block is
  not sent: full help strings invite the page to enumerate every option.
* **Docs.** The documentation sections that name the operation as a user
  types it, best first, under :data:`DOCS_TOKEN_CAP`.

Token counts use the 4-characters-per-token estimate.
"""

from __future__ import annotations

import math
import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from typing import Any

from ..flow_entries import CLI, DocSection, LineReader
from ..flow_pages import FlowPlan
from ..flow_trace import FlowStep

CODE_TOKEN_CAP = 9000
DOCS_TOKEN_CAP = 1200
DOC_SECTION_CAP = 400
OPTION_LINES = 30
_HEADS = (40, 30, 22, 16, 12, 8, 5)
_ROOT_EXTRA = 10  # the root's head: where options are unpacked and the run is set up
_WINDOW = 2
# Lines above a definition: decorators and annotations, comments, attributes.
_NOT_DEF = ("@", "#", "//", "/*", "*", "[")
_USER_DOC_DIRS = frozenset(
    {"start", "getting-started", "getting_started", "guide", "guides", "reference", "usage"}
)


def _tokens(text: str) -> int:
    return len(text) // 4


@dataclass(frozen=True)
class FlowMaterial:
    excerpts: tuple[str, ...]
    surface: str
    docs: tuple[DocSection, ...]
    root_cite: str
    # (path, start, end, label) spans a citation may be grounded on.
    extra_spans: tuple[tuple[str, int, int, str], ...] = field(default=())


# ---------------------------------------------------------------------------
# Excerpts
# ---------------------------------------------------------------------------


def def_line(lines: list[str], start: int) -> int:
    """The definition line at or after *start*: skips a decorator stack and comments.

    Only lines that are plainly not the definition are skipped, so a language
    whose definition keyword is unknown here still starts at *start*.
    """
    depth = 0
    for i in range(start - 1, min(len(lines), start + 80)):
        text = lines[i].strip()
        if depth == 0 and text and not text.startswith(_NOT_DEF):
            return i + 1
        depth = max(0, depth + text.count("(") - text.count(")"))
    return start


def decorator_start(lines: list[str], def_ln: int) -> int:
    """1-based first line of the decorator stack above a definition (*def_ln* if none)."""
    first = def_ln
    i = def_ln - 2
    while i >= 0 and lines[i].strip() and lines[i].startswith((" ", "\t", "@", ")")):
        if lines[i].lstrip().startswith("@"):  # a method's decorators are indented
            first = i + 1
        i -= 1
    return first


def _wanted_lines(
    step: FlowStep, start: int, end: int, child_lines: Iterable[int], head: int
) -> set[int]:
    want = set(range(start, min(end, start + head - 1) + 1))
    for line in child_lines:
        if start <= line <= end:
            want |= set(range(max(start, line - _WINDOW), min(end, line + _WINDOW) + 1))
    want |= {line for line in step.other_calls if start <= line <= end}
    return want


def _chunks(numbers: list[int]) -> list[tuple[int, int]]:
    """Contiguous runs of line numbers."""
    out: list[tuple[int, int]] = []
    for n in numbers:
        if out and n == out[-1][1] + 1:
            out[-1] = (out[-1][0], n)
        else:
            out.append((n, n))
    return out


def excerpt(
    step: FlowStep, lines: list[str], child_lines: Iterable[int], head: int
) -> tuple[str, list[tuple[int, int]]]:
    """Numbered source for one step and the line runs it shows."""
    if not step.start_line or not lines:
        return "", []
    start = def_line(lines, step.start_line)
    end = min(step.end_line or start, len(lines))
    runs = _chunks(sorted(_wanted_lines(step, start, end, child_lines, head)))
    out: list[str] = []
    for a, b in runs:
        if out:
            out.append("    ...")
        out += [f"{n:>5} {lines[n - 1]}" for n in range(a, b + 1)]
    if runs and runs[-1][1] < end:
        out.append("    ...")
    return "\n".join(out), runs


def _child_lines(plan: FlowPlan) -> list[list[int]]:
    children: list[list[int]] = [[] for _ in plan.steps]
    for step in plan.steps:
        if step.parent >= 0 and step.call_line:
            children[step.parent].append(step.call_line)
    return children


def excerpts(plan: FlowPlan, read: LineReader, cap: int = CODE_TOKEN_CAP) -> list[str]:
    """Excerpt every step; shrink the heads together until the total fits *cap*.

    When even the smallest heads overflow (a small model's window), the last
    steps, the deepest in the walk, lose their code first.
    """
    children = _child_lines(plan)
    texts: list[str] = []
    for head in _HEADS:
        texts = [
            excerpt(step, read(step.path), children[i], head + (_ROOT_EXTRA if i == 0 else 0))[0]
            for i, step in enumerate(plan.steps)
        ]
        if sum(map(_tokens, texts)) <= cap:
            return texts
    total = sum(map(_tokens, texts))
    for i in range(len(texts) - 1, 0, -1):
        if total <= cap:
            break
        total -= _tokens(texts[i])
        texts[i] = ""
    return texts


# ---------------------------------------------------------------------------
# Surface: options one line each
# ---------------------------------------------------------------------------


def _first_sentence(text: str, limit: int = 90) -> str:
    return re.split(r"(?<=[.;])\s", text.strip(), maxsplit=1)[0][:limit]


def _decorator_line(deco: str) -> str:
    head = re.match(r"@([\w.]+)", deco)
    if not head:
        return ""
    flags = re.findall(r"[\"'](-{1,2}[\w-]+(?:/-{1,2}[\w-]+)?)[\"']", deco)
    name = re.match(r"@[\w.]+\(\s*[\"']([\w-]+)[\"']", deco)
    help_m = re.search(r"help\s*=\s*\(?((?:\s*f?[\"'][^\"']*[\"']\s*)+)", deco)
    parts = re.findall(r"[\"']([^\"']*)[\"']", help_m.group(1)) if help_m else []
    helptext = re.sub(r"\s+", " ", " ".join(parts))
    kind = head.group(1).rsplit(".", 1)[-1]
    if kind in ("option", "argument") or flags:
        label = " / ".join(flags) or (name.group(1) if name else "")
        return f"- {kind} {label}: {_first_sentence(helptext)}".rstrip(": ")
    return f"- @{head.group(1)}" + (f"({name.group(1)})" if name else "")


def options_summary(decorators: Iterable[str]) -> str:
    """One line per decorator: flags plus the first sentence of its help text."""
    out = [line for line in map(_decorator_line, decorators) if line]
    return _capped(out)


def go_flags_summary(lines: Iterable[str]) -> str:
    """One line per pflag/cobra flag registration: name and first sentence of the help."""
    out = []
    for line in lines:
        m = re.search(r"Flags\(\)\.(\w+)\((.*)\)\s*$", line)
        strs = re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(2)) if m else []
        names = [x for x in strs if re.fullmatch(r"[a-zA-Z][\w-]+", x)]
        if names:
            help_ = strs[-1] if strs[-1] != names[0] else ""
            out.append(f"- option --{names[0]}: {_first_sentence(help_)}".rstrip(": "))
    return _capped(out)


def _capped(out: list[str]) -> str:
    more = f"\n- ... {len(out) - OPTION_LINES} more" if len(out) > OPTION_LINES else ""
    return "\n".join(out[:OPTION_LINES]) + more


def _surface(plan: FlowPlan, graph: Any, read: LineReader) -> str:
    root = plan.steps[0]
    if root.path.endswith(".go"):
        return go_flags_summary(read(root.path)) if plan.kind == CLI else ""
    attrs = graph.nodes.get(root.symbol_id, {}) if root.symbol_id in graph else {}
    return options_summary(attrs.get("decorators") or ())


def _root_cite(root: FlowStep, read: LineReader) -> str:
    """The root's span from its decorators: options and help text live there."""
    if root.start_line is None:
        return root.path
    lines = read(root.path)
    start = decorator_start(lines, def_line(lines, root.start_line)) if lines else root.start_line
    return f"{root.path}:{min(start, root.start_line)}-{root.end_line or root.start_line}"


# ---------------------------------------------------------------------------
# Docs that show the operation in use
# ---------------------------------------------------------------------------


def _phrases(plan: FlowPlan) -> tuple[list[str], list[str]]:
    """(phrases to count in a section, words to look for in its heading)."""
    public = plan.public or plan.steps[0].name
    last = re.split(r"[ .]", public)[-1]
    if plan.kind == CLI and " " not in public:
        # The bare program name: plain mentions are everywhere, so only typed forms count.
        return [f"`{public}`", f"`{public} ", f"$ {public}", f"\n{public} "], ["commands"]
    if plan.kind == CLI:
        return [public, f"`{public}`"], [last]
    if "." in public:
        return [public, f".{last}("], [last]
    return [public], [last]


def _section_score(sec: DocSection, phrases: list[str], heading_words: list[str]) -> float:
    low, head = sec.text.lower(), sec.heading.lower()
    score = sum(low.count(p.lower()) for p in phrases)
    score += 3 * sum(p.lower() in head for p in phrases)
    score += 4 * sum(w.lower() in head for w in heading_words)
    if not score:
        return 0.0
    parts = set(sec.path.lower().split("/")[:-1])
    if parts & _USER_DOC_DIRS or sec.path.lower().startswith("readme"):
        score *= 1.5
    return score / math.sqrt(_tokens(sec.text) / 200 + 1)


def _trimmed(sec: DocSection) -> DocSection:
    limit = DOC_SECTION_CAP * 4
    if len(sec.text) <= limit:
        return sec
    text = sec.text[:limit]
    end = sec.start + text.count("\n")
    return DocSection(sec.path, sec.heading, sec.start, end, text + "\n[...]", sec.spans)


def relevant_docs(plan: FlowPlan, sections: Collection[DocSection]) -> tuple[DocSection, ...]:
    """Doc sections that name the operation, best first, under the docs cap."""
    phrases, heading_words = _phrases(plan)
    scored = [(_section_score(s, phrases, heading_words), s) for s in sections]
    ranked = sorted((x for x in scored if x[0] > 0), key=lambda x: (-x[0], x[1].path, x[1].start))
    picked: list[DocSection] = []
    used = 0
    for _, sec in ranked:
        sec = _trimmed(sec)
        cost = _tokens(sec.text)
        if used + cost <= DOCS_TOKEN_CAP:
            picked.append(sec)
            used += cost
    return tuple(picked)


def build_material(
    plan: FlowPlan,
    graph: Any,
    read: LineReader,
    sections: Collection[DocSection],
    code_cap: int = CODE_TOKEN_CAP,
) -> FlowMaterial:
    if not plan.steps:
        return FlowMaterial((), "", (), "")
    docs = relevant_docs(plan, sections)
    root_cite = _root_cite(plan.steps[0], read)
    spans = [(d.path, d.start, d.end, d.heading) for d in docs]
    if m := re.fullmatch(r"(.+):(\d+)-(\d+)", root_cite):
        spans.append((m.group(1), int(m.group(2)), int(m.group(3)), plan.steps[0].name))
    return FlowMaterial(
        excerpts=tuple(excerpts(plan, read, code_cap)),
        surface=_surface(plan, graph, read),
        docs=docs,
        root_cite=root_cite,
        extra_spans=tuple(spans),
    )
