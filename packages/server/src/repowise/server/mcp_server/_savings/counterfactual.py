"""Per-tool counterfactual estimators — "what raw exploration did this replace?"

Each MCP tool answer stands in for some amount of raw file reading the agent
would otherwise have done. ``replaced_tokens_for`` returns a **conservative
undersell** of that raw cost, computed purely from fields already on the tool's
result dict — no disk reads, no DB queries. When a tool can't be estimated from
its dict alone it returns 0, and the recorder writes no row (undersell by
omission). High-value tools that hold an exact artifact (``get_symbol`` has the
whole source file in hand) instead *declare* their counterfactual via
:func:`repowise.server.mcp_server._savings.wrapper.declare_replaced`; the
wrapper prefers a declared value over anything computed here. Tools whose
answer replaces reading files declare through the ``*_replaced_tokens``
functions below with a live reader (:mod:`.reads`), so the formula has one
owner and the dict-only estimator is its fallback.

Floors mirror :data:`repowise.core.distill.missed.RATIO_FLOOR` philosophy:
estimates undersell rather than oversell, so the savings number on the Costs
page is always defensible.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

#: Conservative per-cited-file floor for ``search_codebase``. A semantic hit
#: stands in for the agent grepping then opening that file; we have no file
#: size on the result dict (search never loads bodies), so we undersell each
#: distinct cited path to a small fraction of a typical source file rather than
#: re-reading disk. Deliberately low — the goal is never to overstate.
SEARCH_FLOOR_PER_HIT = 400


#: A ``get_context`` *module* card lists the module's child files (each with a
#: one-line summary); the agent would otherwise glob the directory and open
#: those files to learn what the module does. Credit a low per-listed-file
#: floor — well under a real Read, since the card only carries the summary.
#: The child-page query is recursive and uncapped, so a top-level module can
#: list hundreds of files; ``CONTEXT_MODULE_MAX`` caps the per-target credit
#: because the agent would never actually have opened all of them. The cap is
#: set at roughly eight full file reads: orienting in a large unfamiliar module
#: without this card means globbing the tree (the path listing alone runs to
#: thousands of tokens at that size) and opening five to fifteen files, so the
#: real counterfactual sits higher still — the cap keeps the claim an undersell
#: while leaving the per-file scaling meaningful for mid-sized modules.
CONTEXT_MODULE_FILE_FLOOR = 600
CONTEXT_MODULE_MAX = 20_000

#: A ``get_context`` *symbol* card carries the signature, docstring and usage
#: sites for one symbol — standing in for the agent locating and reading that
#: definition plus scanning who calls it. Finding it at all is a capped grep,
#: measured at 1,584 tokens of output on this repository (5,443 uncapped);
#: reading enough of the definition and its callers to answer the same question
#: is more again. Floored at well under half that measured grep alone.
CONTEXT_SYMBOL_FLOOR = 1_200

#: A ``get_context`` *file* card stands in for the agent Reading that file.
#: The tool declares what that Read costs (:func:`context_replaced_tokens` with
#: a live reader); this floor is only the fallback when the file cannot be
#: read. Across 4,115 source files in this repository the p25 is 620 estimated
#: tokens and the median is 1,296, so 600 sits under the p25.
CONTEXT_FILE_FLOOR = 600

#: Sizes one Read of a repo-relative path, or None when it cannot. Tools pass
#: :func:`.reads.file_read_tokens` bound to their checkout; the dict-only
#: estimators below pass none and fall back to the flat floors.
ReadCost = Callable[[str], int | None]


def _read_cost(read_cost: ReadCost | None, path: Any, fallback: int) -> int:
    """One Read of *path* through *read_cost*, else *fallback*."""
    if read_cost is not None and isinstance(path, str) and path:
        tokens = read_cost(path)
        if isinstance(tokens, int) and tokens > 0:
            return tokens
    return fallback


def _context_target_tokens(key: Any, target: dict[str, Any], read_cost: ReadCost | None) -> int:
    """What one card replaced: a Read of its file, a module skim, or a symbol lookup."""
    skeleton = target.get("skeleton")
    if isinstance(skeleton, dict):
        full = skeleton.get("full_tokens")
        if isinstance(full, int) and full > 0:
            # A ``file.py::Symbol`` target renders the file that defines it.
            return _read_cost(read_cost, skeleton.get("of_file") or key, full)
    target_type = target.get("type")
    if target_type == "module":
        docs = target.get("docs")
        files = docs.get("files") if isinstance(docs, dict) else None
        if isinstance(files, list) and files:
            return min(CONTEXT_MODULE_MAX, len(files) * CONTEXT_MODULE_FILE_FLOOR)
        return 0
    if target_type == "symbol":
        return CONTEXT_SYMBOL_FLOOR
    if target_type == "file":
        return _read_cost(read_cost, key, CONTEXT_FILE_FLOOR)
    return 0


def context_replaced_tokens(result: dict[str, Any], read_cost: ReadCost | None = None) -> int:
    """Raw exploration a ``get_context`` reply replaced, summed over every target.

    A file card, with or without a skeleton, replaces one Read of that file.
    Module and symbol cards earn the floors above for the directory globbing
    and symbol lookup they replace.
    """
    targets = result.get("targets")
    if not isinstance(targets, dict):
        return 0
    return sum(
        _context_target_tokens(key, target, read_cost)
        for key, target in targets.items()
        if isinstance(target, dict) and not target.get("error")
    )


def _estimate_get_context(result: dict[str, Any]) -> int:
    return context_replaced_tokens(result)


def _estimate_search_codebase(result: dict[str, Any]) -> int:
    """``len(distinct cited files) x SEARCH_FLOOR_PER_HIT`` — a conservative floor.

    Each result points the agent at a file it would otherwise have had to find
    and open by hand. We undersell to a per-hit floor because the result dict
    carries only the path (and a snippet), never the file's size.
    """
    results = result.get("results")
    if not isinstance(results, list):
        return 0
    # ``path`` is on every row that names a file, symbol rows included, which
    # the old ``target_path`` read missed. Fallback for pre-``path`` payloads.
    paths = {
        r.get("path") or r.get("target_path")
        for r in results
        if isinstance(r, dict) and (r.get("path") or r.get("target_path"))
    }
    return len(paths) * SEARCH_FLOOR_PER_HIT


#: get_answer subsumes a search_codebase call plus opening the cited files.
#: The tool declares the real Read cost of each cited file
#: (:func:`answer_replaced_tokens` with a live reader); the per-file floor is
#: only the fallback when a file cannot be read.
ANSWER_SEARCH_FLOOR = 400
ANSWER_READ_FLOOR_PER_FILE = 400
#: Confidence tiers at which a synthesised answer stands in for reading its
#: citations. A low one tells the agent to verify, so those reads still happen.
_ANSWERED_CONFIDENCE = frozenset({"high", "medium"})

#: Modest fixed counterfactuals for the analysis tools. Each replaces an
#: exploration the agent cannot cheaply reproduce (git log/blame spelunking,
#: ADR archaeology, README + entry-point skim) but whose size the result dict
#: cannot ground — so a flat, deliberately-low floor per successful call.
#: ``get_dead_code`` stands in for the agent grepping every export for
#: usages across the tree to prove it unreachable — an expensive manual sweep.
#: Floored conservatively between a single-file health call and a full overview.
DEAD_CODE_FLOOR = 800
RISK_FLOOR = 2_000
#: ``get_why`` replaces searching the decision records and commit history for
#: the question (the per-call floor) plus git archaeology on each file whose
#: history it serves (the per-file term). ``git log -p -5`` on one file has a
#: median of 2,054 estimated tokens across 23 httpx source files and 3,089
#: across 16 sampled files of this repository, and it is the cheapest form of
#: the question: ``git blame --line-porcelain`` on a hot file runs to 416,000+.
#: The per-file term sits under both medians; the cap stops a many-target call
#: claiming more than anyone would dig through.
WHY_FLOOR = 3_000
WHY_PER_FILE = 2_000
WHY_MAX = 15_000
#: ``get_health`` replaces reading a file and judging its complexity by eye.
#: The per-file term is roughly the measured 1,296-token median source file;
#: the base covers locating the right files to look at in the first place.
HEALTH_FLOOR = 1_500
HEALTH_PER_FILE = 800
HEALTH_MAX = 12_000
#: ``get_overview`` replaces the README-plus-entry-points skim an agent does to
#: orient in an unfamiliar repository. This repository's README alone measures
#: 15,941 estimated tokens, so the old 1,200 credited 7.5% of one file; 6,000
#: is still barely a third of it, before any entry point is opened.
OVERVIEW_FLOOR = 6_000

#: ``get_change_risk`` reasons about a whole diff's defect risk — standing in
#: for the agent reading the diff and its coverage/co-change context by hand.
#: Reading the diff is what it replaces, and ``git show`` with patch measures a
#: 6,094-token median on this repository's commits, so the per-file term is a
#: quarter of that median divided across a typical diff. The cap keeps a
#: 200-file refactor from claiming a number nobody would have read. The tool
#: declares this from the changed-file count it scored
#: (:func:`change_risk_replaced_tokens`), because the count is not on its
#: default response; the floor is for replies that score nothing.
CHANGE_RISK_FLOOR = 500
CHANGE_RISK_PER_FILE = 1_500
CHANGE_RISK_MAX = 12_000
#: ``get_architecture`` renders the C4 map — replacing a read of many files and
#: entry points to infer how the system is layered (workspace-level, so higher).
ARCHITECTURE_FLOOR = 1200
#: ``get_dependency_path`` returns the import chain between two files — replacing
#: the agent tracing imports hop by hop.
DEPENDENCY_FLOOR = 300
#: ``get_conformance`` checks contract conformance — replacing cross-file /
#: cross-repo contract reading.
CONFORMANCE_FLOOR = 400

#: Signal-scaled floors: where a tool's result *names* the files or graph nodes
#: its answer replaced, credit grows with that count (still bounded and
#: undersold) instead of a flat floor.
#:  * ``get_risk`` — per assessed target (a file whose history the agent would
#:    reconstruct) plus per PR blast-radius file it surfaces.
#:    Reconstructing one file's history means reading the file (median 1,296
#:    estimated tokens across this repository's 4,115 source files) and
#:    grepping for what depends on it (a capped grep measures 1,584). The
#:    per-target term is under the sum of the two.
RISK_PER_TARGET = 2_000
RISK_PER_RELATED_FILE = 120
#:  * ``get_blast_radius`` — per downstream file that breaks (one the agent
#:    would otherwise trace across repos), capped so a huge fan-out still
#:    undersells.
BLAST_FLOOR = 400
BLAST_PER_IMPACTED = 150
BLAST_MAX = 6000
#:  * ``get_execution_flows`` — per node on a traced flow (a call-graph hop the
#:    agent would follow by opening files), capped likewise.
FLOWS_FLOOR = 400
FLOWS_PER_NODE = 80
FLOWS_MAX = 6000


def _answer_paths(result: dict[str, Any]) -> set[str]:
    """Every file the reply points at: citations, top fallbacks, ranked guesses."""
    guesses = [g.get("file") for g in result.get("best_guesses") or [] if isinstance(g, dict)]
    return {
        p
        for p in [
            *(result.get("citations") or []),
            *(result.get("fallback_targets") or [])[:3],
            *guesses,
        ]
        if isinstance(p, str) and p
    }


def _whole_body_paths(result: dict[str, Any]) -> set[str]:
    """Files a verified, uncut symbol body was served from."""
    rows = [r for r in result.get("symbol_bodies") or [] if isinstance(r, dict)]
    return {
        row["path"]
        for row in rows
        if isinstance(row.get("path"), str) and row.get("verified") is True and _uncut(row)
    }


def _uncut(row: dict[str, Any]) -> bool:
    return not row.get("truncated") and "continuation" not in row


def answer_replaced_tokens(result: dict[str, Any], read_cost: ReadCost | None = None) -> int:
    """Search plus the reads a ``get_answer`` reply stood in for.

    A synthesised answer at high or medium confidence replaces the search and
    a Read of every file it cites. Any other reply (low confidence, or no
    provider) only replaces what it actually served: a Read of each file whose
    whole body it inlined, as ``get_symbol`` credits, and the search step for
    every other file it points at, as ``search_codebase`` credits.
    """
    if result.get("error"):
        return 0
    paths = _answer_paths(result)
    answered = (
        not result.get("degraded")
        and str(result.get("answer") or "").strip()
        and result.get("confidence") in _ANSWERED_CONFIDENCE
    )
    if answered:
        if not paths:
            return 0
        reads = sum(_read_cost(read_cost, p, ANSWER_READ_FLOOR_PER_FILE) for p in paths)
        return ANSWER_SEARCH_FLOOR + reads
    bodies = _whole_body_paths(result)
    reads = sum(_read_cost(read_cost, p, ANSWER_READ_FLOOR_PER_FILE) for p in bodies)
    return reads + len(paths - bodies) * SEARCH_FLOOR_PER_HIT


def _estimate_get_answer(result: dict[str, Any]) -> int:
    return answer_replaced_tokens(result)


def change_risk_replaced_tokens(changed_files: int) -> int:
    """Floor plus a per-changed-file term — the diff the agent did not read."""
    if changed_files <= 0:
        return CHANGE_RISK_FLOOR
    return min(CHANGE_RISK_MAX, CHANGE_RISK_FLOOR + changed_files * CHANGE_RISK_PER_FILE)


def _estimate_get_change_risk(result: dict[str, Any]) -> int:
    """The declared value's fallback: ``features.nf`` when diagnostics carry it."""
    if result.get("error"):
        return 0
    features = result.get("features")
    files = features.get("nf") if isinstance(features, dict) else None
    return change_risk_replaced_tokens(files if isinstance(files, int) else 0)


def _why_files(result: dict[str, Any]) -> set[str]:
    """Files whose history a ``get_why`` reply serves: its path and target cards."""
    files = {
        key
        for key, card in (result.get("target_context") or {}).items()
        if isinstance(key, str) and isinstance(card, dict) and card
    }
    if result.get("mode") == "path" and isinstance(result.get("path"), str):
        files.add(result["path"])
    return files


def _estimate_get_why(result: dict[str, Any]) -> int:
    """Per-call floor plus a per-file term for each file whose history it serves."""
    if result.get("error"):
        return 0
    return min(WHY_MAX, WHY_FLOOR + len(_why_files(result)) * WHY_PER_FILE)


def _estimate_get_health(result: dict[str, Any]) -> int:
    """Floor plus a per-file term — the files the agent did not open to judge.

    ``targets`` (targets mode) and ``metrics`` (repository mode) each name the
    files the answer is about, and reading one to form the same judgement is
    roughly the measured 1,296-token median source file. Falls back to the
    flat floor when the payload names none.
    """
    if result.get("error"):
        return 0
    files = 0
    for key in ("targets", "metrics"):
        value = result.get(key)
        if isinstance(value, list):
            files = max(files, len(value))
    if not files:
        return HEALTH_FLOOR
    return min(HEALTH_MAX, HEALTH_FLOOR + files * HEALTH_PER_FILE)


def _fixed_floor(tokens: int) -> Callable[[dict[str, Any]], int]:
    """Flat counterfactual for successful calls; errors contribute nothing."""

    def _estimate(result: dict[str, Any]) -> int:
        return 0 if result.get("error") else tokens

    return _estimate


#: Directive lists this estimator counts as "files the agent would have opened".
#: Read by name out of the response, so a rename on the tool side undercounts
#: silently instead of failing - test_risk.py asserts these keys still exist.
RISK_RELATED_FILE_KEYS = ("may_break", "missing_cochanges")


def _estimate_get_risk(result: dict[str, Any]) -> int:
    """Per assessed target + per PR blast-radius file, floored at ``RISK_FLOOR``.

    Each ``targets`` entry is a file whose churn/ownership/co-change history the
    agent would otherwise reconstruct from ``git log``/``git blame``. PR mode
    adds a ``directive`` naming the files that may break (``may_break``) and the
    co-change partners the diff missed — each one more file the agent would
    have had to find by hand.
    """
    if result.get("error"):
        return 0
    targets = result.get("targets")
    total = len(targets) * RISK_PER_TARGET if isinstance(targets, dict) else 0
    directive = result.get("directive")
    if isinstance(directive, dict):
        # Union the two lists: a file that both breaks and is a missed
        # co-change partner is still just one file the agent would open.
        related_files: set[str] = set()
        for key in RISK_RELATED_FILE_KEYS:
            related = directive.get(key)
            if isinstance(related, list):
                related_files.update(f for f in related if isinstance(f, str))
        total += len(related_files) * RISK_PER_RELATED_FILE
    return max(RISK_FLOOR, total)


def _estimate_get_blast_radius(result: dict[str, Any]) -> int:
    """Per downstream file the change breaks, floored and capped.

    ``total_impacted`` counts the files the agent would otherwise trace across
    repos to answer "what breaks if I change this". Undersold to a per-file
    floor and capped so a large fan-out never overstates.
    """
    if result.get("error"):
        return 0
    impacted = result.get("total_impacted")
    n = impacted if isinstance(impacted, int) and impacted > 0 else 0
    return min(BLAST_MAX, max(BLAST_FLOOR, n * BLAST_PER_IMPACTED))


def _estimate_get_execution_flows(result: dict[str, Any]) -> int:
    """Per node across every traced flow, floored and capped.

    Each node on a flow's ``trace`` is a call-graph hop the agent would follow
    by opening the next file. Falls back to ``depth + 1`` when a flow omits its
    node list. Errors / empty results contribute nothing.
    """
    if result.get("error"):
        return 0
    flows = result.get("flows")
    if not isinstance(flows, list) or not flows:
        return 0
    nodes = 0
    for flow in flows:
        if not isinstance(flow, dict):
            continue
        trace = flow.get("trace")
        if isinstance(trace, list):
            nodes += len(trace)
        elif isinstance(flow.get("depth"), int):
            nodes += flow["depth"] + 1
    if nodes <= 0:
        return 0
    return min(FLOWS_MAX, max(FLOWS_FLOOR, nodes * FLOWS_PER_NODE))


#: ``generate_refactoring_code`` hands back the rewritten code for a plan. Its
#: ``spans`` field carries the exact working-tree slices fed to the model --
#: which is precisely what the agent would have had to read to do the refactor
#: by hand -- so the counterfactual is grounded in those rather than guessed.
#: Only the reading is credited, never the writing, which is the larger half:
#: undersell by construction. Capped like the other signal-scaled floors so one
#: enormous span cannot dominate the ledger.
REFACTOR_FLOOR = 400
REFACTOR_SPAN_CHARS_PER_TOKEN = 4
REFACTOR_MAX = 8000


def _estimate_generate_refactoring_code(result: dict[str, Any]) -> int:
    if result.get("error"):
        return 0
    spans = result.get("spans")
    if not isinstance(spans, list) or not spans:
        return 0
    chars = 0
    for span in spans:
        if isinstance(span, dict) and isinstance(span.get("source"), str):
            chars += len(span["source"])
    if chars <= 0:
        return 0
    return min(REFACTOR_MAX, max(REFACTOR_FLOOR, chars // REFACTOR_SPAN_CHARS_PER_TOKEN))


#: ``list_repos`` replaces the agent working out which repos are indexed at all
#: -- listing a workspace tree and checking each entry for a ``.repowise``.
#: Small on purpose: it is a directory walk, not a file read.
REPOS_FLOOR = 120
REPOS_PER_REPO = 40
REPOS_MAX = 800


def _estimate_list_repos(result: dict[str, Any]) -> int:
    if result.get("error"):
        return 0
    repos = result.get("repos")
    if not isinstance(repos, list) or not repos:
        return 0
    return min(REPOS_MAX, max(REPOS_FLOOR, len(repos) * REPOS_PER_REPO))


#: Tool name → estimator. Tools absent here emit no counterfactual (skip). Every
#: agent-facing tool that replaces real exploration is listed: leaving a tool
#: out means it can never earn credit yet still takes a dead-end debit on an
#: error (``recorder.record_mcp_dead_end``), so its ledger contribution could
#: only ever go negative. Listing a tool here is necessary but not sufficient to
#: even that out — ``recorder.record_mcp_saving`` still drops any row whose
#: counterfactual doesn't exceed the delivered response, so a floor set below a
#: tool's own answer size records nothing (undersell by omission, by design).
_ESTIMATORS: dict[str, Callable[[dict[str, Any]], int]] = {
    "get_context": _estimate_get_context,
    "search_codebase": _estimate_search_codebase,
    "get_answer": _estimate_get_answer,
    "get_risk": _estimate_get_risk,
    "get_why": _estimate_get_why,
    "get_health": _estimate_get_health,
    "get_overview": _fixed_floor(OVERVIEW_FLOOR),
    "get_dead_code": _fixed_floor(DEAD_CODE_FLOOR),
    "get_change_risk": _estimate_get_change_risk,
    "get_blast_radius": _estimate_get_blast_radius,
    "get_execution_flows": _estimate_get_execution_flows,
    "get_architecture": _fixed_floor(ARCHITECTURE_FLOOR),
    "get_dependency_path": _fixed_floor(DEPENDENCY_FLOOR),
    "get_conformance": _fixed_floor(CONFORMANCE_FLOOR),
    "generate_refactoring_code": _estimate_generate_refactoring_code,
    "list_repos": _estimate_list_repos,
}


def replaced_tokens_for(tool: str, result: Any) -> int:
    """Conservative raw-exploration tokens *tool*'s answer replaced.

    Returns 0 for unknown tools, non-dict results, or anything the per-tool
    estimator can't ground in the result dict. Never raises.
    """
    if not isinstance(result, dict):
        return 0
    estimator = _ESTIMATORS.get(tool)
    if estimator is None:
        return 0
    try:
        value = estimator(result)
    except Exception:
        return 0
    return value if isinstance(value, int) and value > 0 else 0
