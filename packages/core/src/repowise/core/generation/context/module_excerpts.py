"""Code excerpts for a module page prompt, so the model reads code, not only paths.

Candidates are the Public API first, then each file's public or documented
top-level symbols by file PageRank, at most ``per_file`` per file (three by
default), the ones most called and most on traced flows first. Each file with
candidates costs one line of signatures, reserved first (up to half the
budget); bodies (signature, docstring and code, up to 60 lines) get the rest,
so bodies are what a tight budget drops first. A file whose symbols got no
body keeps its line while the budget lasts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .evidence import EvidenceSelection, select_symbol_evidence
from .token_budget import estimate_tokens, items_within_budget

_PER_FILE = 3
_MAX_LINES = 60


def _file_of(symbol: Any) -> str:
    return symbol.id.split("::", 1)[0]


def _symbol(parsed: Mapping[str, Any], path: str, symbol_id: str) -> Any | None:
    pf = parsed.get(path)
    # An overload's implementation shares its declarations' id; serve the implementation.
    hits = [s for s in (pf.symbols if pf else ()) if s.id == symbol_id]
    return min(hits, key=lambda s: s.is_declaration, default=None)


def excerpt_candidates(
    public_api: Sequence[dict],
    parsed: Mapping[str, Any],
    ranked_paths: Sequence[str],
    weight: Mapping[str, int] | None = None,
    per_file: int = _PER_FILE,
) -> list[Any]:
    """Symbols to excerpt, best first: the Public API, then ranked files' symbols.

    Within a file, *weight* (symbol id to how much other code runs through it)
    orders the symbols, heaviest first; file order breaks ties.
    """
    out: list[Any] = []
    seen: set[str] = set()
    for e in public_api:
        sym = _symbol(parsed, e["file"], e["id"]) if e.get("id") else None
        if sym is not None and sym.id not in seen:
            out.append(sym)
            seen.add(sym.id)
    for path in ranked_paths:
        room = per_file - sum(1 for s in out if _file_of(s) == path)
        for s in _by_weight(_worth_excerpting(parsed.get(path)), weight):
            if room <= 0:
                break
            if s.id in seen:
                continue
            out.append(_symbol(parsed, path, s.id))
            seen.add(s.id)
            room -= 1
    return out


def _worth_excerpting(pf: Any) -> list[Any]:
    """A file's top-level symbols that are public or documented, in file order."""
    symbols = pf.symbols if pf is not None else ()
    return [
        s for s in symbols if s.parent_name is None and (s.visibility == "public" or s.docstring)
    ]


def _by_weight(symbols: list[Any], weight: Mapping[str, int] | None) -> list[Any]:
    if not weight:
        return symbols
    return sorted(symbols, key=lambda s: -weight.get(s.id, 0))  # stable: file order on ties


def _declared_line(path: str, symbols: list[Any]) -> str:
    sigs = "; ".join(" ".join((s.signature or s.name).split()) for s in symbols[:_PER_FILE])
    return f"`{path}`: `` {sigs} ``"


def module_excerpts(
    public_api: Sequence[dict],
    parsed: Mapping[str, Any],
    ranked_paths: Sequence[str],
    source_map: Mapping[str, bytes],
    budget: int,
    weight: Mapping[str, int] | None = None,
    per_file: int = _PER_FILE,
) -> tuple[EvidenceSelection, list[str]]:
    """Code excerpts and one-line declarations for the files left without one."""
    candidates = excerpt_candidates(public_api, parsed, ranked_paths, weight, per_file)
    by_file: dict[str, list[Any]] = {}
    for s in candidates:
        by_file.setdefault(_file_of(s), []).append(s)
    lines = {path: _declared_line(path, syms) for path, syms in by_file.items()}
    # Signatures outrank bodies, but claim at most half so central files still show code.
    _, reserved = items_within_budget(lines.values(), 0, budget // 2, estimate_tokens)
    body_budget = budget - reserved
    selection = select_symbol_evidence(
        source_map,
        [s.id for s in candidates],
        [parsed[p] for p in by_file if p in parsed],
        token_budget=body_budget,
        max_lines=_MAX_LINES,
    )
    excerpted = {item.path for item in selection.included}
    leftover = [line for path, line in lines.items() if path not in excerpted]
    kept, _ = items_within_budget(leftover, selection.estimated_tokens, budget, estimate_tokens)
    return selection, kept
