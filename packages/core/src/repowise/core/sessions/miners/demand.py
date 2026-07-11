"""FAQ-weighted docs budget: mine per-file question demand from transcripts.

The generation planner documents a repo uniformly by default: page budget is
spread by graph centrality, blind to what agents actually ask about. This
miner reads the shared :class:`~repowise.core.sessions.Event` stream and counts,
per file, how many agent *questions* resolved to it: ``get_answer`` calls
(attributed to the files their answer cited) and ``search_codebase`` calls
(attributed to the files their hits resolved to). Rolled up to the module
granularity the generation pipeline uses for wiki pages, those counts let the
planner tilt documentation depth toward the modules agents keep asking about
and lean out the cold ones (see
``core.generation.selection.budget.allocate_module_file_pages``).

Read-only and local: transcripts are read from the user's own machine and never
leave it. No LLM, no writes. A repo with no session history yields an empty
map, and the planner then reproduces its uniform behaviour byte-for-byte.

Design notes
------------
- Demand is a *snapshot* signal, not accreting institutional memory, so this is
  a full re-sweep each generation (like the gate's ``scan_usage``), not a
  cursor-advanced incremental miner. It reads only question-bearing lines
  (``_prefilter``), so the sweep stays cheap.
- One question call contributes at most once per file it resolves to, capped
  per call, so a single broad search can't flood a module's count.
- Served/hit paths from the index are already repo-relative POSIX; attribution
  keys on them directly and drops anything that isn't a plain in-repo path.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import structlog

from repowise.core.sessions import ClaudeCodeAdapter, Event

logger = structlog.get_logger(__name__)

__all__ = [
    "aggregate_file_demand",
    "demand_summary_line",
    "faq_weighting_enabled",
    "mine_events_demand",
    "rollup_to_modules",
]

#: repowise MCP tools whose results resolve to files. Kept narrow on purpose:
#: get_context/get_symbol/get_risk targets are agent-chosen inputs, not the
#: index answering a question, so they are not demand in the FAQ sense.
_ANSWER_TOOL = "get_answer"
_SEARCH_TOOL = "search_codebase"

#: Per-call caps so one wide answer/search can't dominate a module's tally.
_MAX_ANSWER_FILES = 5
_MAX_SEARCH_FILES = 10

#: Only these transcript lines can carry a question or its result; skipping the
#: rest keeps the full-history sweep cheap.
_PREFILTER_TOKENS = (
    '"type":"user"',
    '"type": "user"',
    '"type":"assistant"',
    '"type": "assistant"',
)


def faq_weighting_enabled(repo_config: dict[str, Any] | None) -> bool:
    """Resolve the ``generation.faq_weighting`` config gate (default on)."""
    cfg = repo_config or {}
    gen_cfg = cfg.get("generation") or {}
    if not isinstance(gen_cfg, dict):
        return True
    return gen_cfg.get("faq_weighting", True) is not False


def _short_mcp(name: str) -> str | None:
    """The bare repowise tool name for an MCP tool call, else None."""
    if "repowise" not in name.lower():
        # Direct (non-MCP) tool names never match a repowise tool.
        return name if name in (_ANSWER_TOOL, _SEARCH_TOOL) else None
    return name.rsplit("__", 1)[-1]


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
    return str(content or "")


def _parse_result_json(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return {}
    if isinstance(data, dict) and isinstance(data.get("result"), dict):
        return data["result"]
    return data if isinstance(data, dict) else {}


def _norm_rel(path: Any) -> str | None:
    """Repo-relative POSIX path for an index-served reference, else None.

    Strips a ``::symbol`` suffix and a trailing ``:line-range`` so a symbol id
    or ranged reference attributes to its file. Rejects absolute paths, drive
    letters, and parent escapes, none of which the index emits for in-repo
    files.
    """
    if not isinstance(path, str):
        return None
    p = path.strip().replace("\\", "/")
    if not p:
        return None
    p = p.split("::", 1)[0]  # symbol id -> file
    # Trailing line range on a bare path (``a/b.py:10-40`` or ``a/b.py:10``).
    head, sep, tail = p.rpartition(":")
    if sep and head and all(c.isdigit() or c == "-" for c in tail):
        p = head
    while p.startswith("./"):
        p = p[2:]
    if not p or p.startswith("/") or ".." in p.split("/") or ":" in p:
        return None
    return p


def _answer_files(result: dict[str, Any]) -> list[str]:
    """Files a ``get_answer`` response resolved to, most-grounded first."""
    files: list[str] = []
    files.extend(f for f in result.get("citations") or [] if isinstance(f, str))
    for q in result.get("quotes") or []:
        if isinstance(q, dict) and isinstance(q.get("path"), str):
            files.append(q["path"])
    for r in result.get("retrieval") or []:
        if isinstance(r, dict):
            hit = r.get("path") or r.get("target_path")
            if isinstance(hit, str):
                files.append(hit)
    for g in result.get("best_guesses") or []:
        if isinstance(g, dict) and isinstance(g.get("file"), str):
            files.append(g["file"])
    return files


def _search_files(result: dict[str, Any]) -> list[str]:
    """Files a ``search_codebase`` response's hits resolved to, in rank order."""
    files: list[str] = []
    for item in result.get("results") or []:
        if not isinstance(item, dict):
            continue
        hit = item.get("file") or item.get("target_path") or item.get("symbol_id")
        if isinstance(hit, str):
            files.append(hit)
    return files


def _attribute(files: Iterable[str], cap: int, demand: Counter) -> None:
    """Count each distinct in-repo file once, up to *cap* files for this call."""
    seen: set[str] = set()
    for raw in files:
        rel = _norm_rel(raw)
        if rel is None or rel in seen:
            continue
        seen.add(rel)
        demand[rel] += 1
        if len(seen) >= cap:
            break


def mine_events_demand(events: Iterable[Event], repo_prefix: str) -> Counter:
    """Per-file question demand over one session's events (pure, streaming).

    *repo_prefix* is the lowercased resolved repo root; only events whose
    ``cwd`` sits inside it count, matching the distill/decision miners. Pairs
    each ``get_answer`` / ``search_codebase`` call to its result by tool-use id
    and attributes the call to the files the result named.
    """
    demand: Counter = Counter()
    #: tool_use id -> short tool name, awaiting its result.
    pending: dict[str, str] = {}

    for event in events:
        cwd = (event.cwd or "").lower().rstrip("\\/")
        if cwd and not cwd.startswith(repo_prefix):
            continue

        for use in event.tool_uses:
            short = _short_mcp(use.name)
            if short in (_ANSWER_TOOL, _SEARCH_TOOL):
                pending[use.id] = short
        # Results normally arrive within a couple of events; cap orphans.
        while len(pending) > 200:
            pending.pop(next(iter(pending)))

        for result in event.tool_results:
            tool = pending.pop(result.tool_use_id, None)
            if tool is None:
                continue
            parsed = _parse_result_json(_result_text(result.content))
            if not parsed:
                continue
            if tool == _ANSWER_TOOL:
                _attribute(_answer_files(parsed), _MAX_ANSWER_FILES, demand)
            else:
                _attribute(_search_files(parsed), _MAX_SEARCH_FILES, demand)

    return demand


def _prefilter(raw: str) -> bool:
    return any(tok in raw for tok in _PREFILTER_TOKENS)


def aggregate_file_demand(
    repo_path: Path | str,
    *,
    adapter: ClaudeCodeAdapter | None = None,
    projects_root: Path | None = None,
    repo_config: dict[str, Any] | None = None,
) -> dict[str, int]:
    """Per-file question demand for *repo_path* from its transcript history.

    Full best-effort sweep over the repo's Claude Code transcripts. Returns
    ``{repo_relative_path: question_count}``; an empty dict when FAQ weighting
    is disabled, no transcripts exist, or anything goes wrong (the planner then
    falls back to its uniform behaviour). Never raises.
    """
    if not faq_weighting_enabled(repo_config):
        return {}
    repo_root = Path(repo_path).resolve()
    repo_prefix = str(repo_root).lower().rstrip("\\/")
    adapter = adapter or ClaudeCodeAdapter()

    demand: Counter = Counter()
    try:
        transcripts = adapter.discover(repo_root, projects_root=projects_root)
    except OSError:
        return {}

    for path in transcripts:
        try:
            with path.open("rb") as fh:
                events = _iter_events(adapter, fh)
                demand.update(mine_events_demand(events, repo_prefix))
        except OSError:
            continue

    if demand:
        logger.info(
            "faq_demand.aggregated",
            transcripts=len(transcripts),
            files_with_demand=len(demand),
            total_questions=sum(demand.values()),
        )
    return dict(demand)


def _iter_events(adapter: ClaudeCodeAdapter, fh: Iterable[bytes]) -> Iterable[Event]:
    """Normalized events from prefiltered transcript lines."""
    for raw in fh:
        line = raw.decode("utf-8", errors="replace")
        if not _prefilter(line):
            continue
        event = adapter.normalize(line)
        if event is not None:
            yield event


def demand_summary_line(file_demand: dict[str, int]) -> str | None:
    """A one-line, human summary of the demand behind the budget tilt.

    ``None`` when there is no demand (nothing to say, keeps fresh installs
    quiet). Reports only exact, non-eroding counts: files and questions.
    """
    if not file_demand:
        return None
    questions = sum(file_demand.values())
    files = len(file_demand)
    return (
        f"FAQ-weighted docs: tilted page budget toward the files behind "
        f"{questions} question{'s' if questions != 1 else ''} across "
        f"{files} file{'s' if files != 1 else ''} in this repo's session history."
    )


def rollup_to_modules(
    file_demand: dict[str, int],
    file_to_module: Callable[[str], str | None] | dict[str, str],
) -> dict[str, int]:
    """Roll per-file demand up to modules via *file_to_module*.

    *file_to_module* maps a repo-relative path to its module key (the same
    granularity the generation pipeline groups wiki pages by), or a plain dict.
    Files that map to ``None`` are dropped. Deterministic; empty in, empty out.
    """
    resolve = file_to_module.get if isinstance(file_to_module, dict) else file_to_module
    modules: Counter = Counter()
    for path, count in file_demand.items():
        module = resolve(path)
        if module:
            modules[module] += count
    return dict(modules)
