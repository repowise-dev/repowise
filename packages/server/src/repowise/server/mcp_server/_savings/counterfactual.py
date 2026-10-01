"""Per-tool counterfactual estimators — "what raw exploration did this replace?"

Each MCP tool answer stands in for some amount of raw file reading the agent
would otherwise have done. ``replaced_tokens_for`` returns a **conservative
undersell** of that raw cost, computed purely from fields already on the tool's
result dict — no disk reads, no DB queries. When a tool can't be estimated from
its dict alone it returns 0, and the recorder writes no row (undersell by
omission). High-value tools that hold an exact artifact (``get_symbol`` has the
whole source file in hand) instead *declare* their counterfactual via
:func:`repowise.server.mcp_server._savings.wrapper.declare_replaced`; the
wrapper prefers a declared value over anything computed here.

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

#: A ``get_context`` *file* card with no skeleton stands in for the agent
#: Reading that file. Skeleton-by-default was removed on 2026-08-11
#: (``tool_context/targets.py:87``) because the symbol-list card is cheaper,
#: and nothing replaced the credit — so the commonest call this tool serves has
#: been earning nothing at all. The card carries no size for the file it
#: describes and this module does no disk reads, so the credit is a flat floor
#: taken from the corpus rather than from the dict: across 4,115 source files
#: in this repository the p25 is 620 estimated tokens and the median is 1,296.
#: 600 is under the p25 — three quarters of real files cost more to Read than
#: this claims, which is the undersell this module is for.
CONTEXT_FILE_FLOOR = 600


def _estimate_get_context(result: dict[str, Any]) -> int:
    """Raw exploration the card replaced, summed over every target.

    File targets: ``get_context`` auto-upgrades them to a body-elided
    *skeleton* carrying ``skeleton.full_tokens`` — the exact size of the file
    the agent would have Read instead; summing those is a precise, dict-only
    counterfactual. Module and symbol targets carry no skeleton, so they used
    to contribute nothing; they now earn a conservative floor (see the module
    /symbol constants above) for the directory globbing / symbol lookup they
    each replace. Small-file file cards (no skeleton) still contribute nothing
    — a short file is about as cheap to Read directly.
    """
    targets = result.get("targets")
    if not isinstance(targets, dict):
        return 0
    total = 0
    for target in targets.values():
        if not isinstance(target, dict) or target.get("error"):
            continue
        skeleton = target.get("skeleton")
        if isinstance(skeleton, dict):
            full = skeleton.get("full_tokens")
            if isinstance(full, int) and full > 0:
                total += full
                # The skeleton already accounts for this file's full Read cost.
                continue
        target_type = target.get("type")
        if target_type == "module":
            docs = target.get("docs")
            files = docs.get("files") if isinstance(docs, dict) else None
            if isinstance(files, list) and files:
                total += min(CONTEXT_MODULE_MAX, len(files) * CONTEXT_MODULE_FILE_FLOOR)
        elif target_type == "symbol":
            total += CONTEXT_SYMBOL_FLOOR
        elif target_type == "file":
            # Reached only when the caller did not ask for a skeleton, which is
            # the default since 2026-08-11. See CONTEXT_FILE_FLOOR.
            total += CONTEXT_FILE_FLOOR
    return total


def _estimate_search_codebase(result: dict[str, Any]) -> int:
    """``len(distinct cited files) x SEARCH_FLOOR_PER_HIT`` — a conservative floor.

    Each result points the agent at a file it would otherwise have had to find
    and open by hand. We undersell to a per-hit floor because the result dict
    carries only the path (and a snippet), never the file's size.
    """
    results = result.get("results")
    if not isinstance(results, list):
        return 0
    paths = {r["target_path"] for r in results if isinstance(r, dict) and r.get("target_path")}
    return len(paths) * SEARCH_FLOOR_PER_HIT


#: get_answer subsumes a search_codebase call plus opening the cited files.
#: Same undersell philosophy as SEARCH_FLOOR_PER_HIT — the dict carries paths,
#: never sizes.
ANSWER_SEARCH_FLOOR = 400
ANSWER_READ_FLOOR_PER_FILE = 400

#: Modest fixed counterfactuals for the analysis tools. Each replaces an
#: exploration the agent cannot cheaply reproduce (git log/blame spelunking,
#: ADR archaeology, README + entry-point skim) but whose size the result dict
#: cannot ground — so a flat, deliberately-low floor per successful call.
#: ``get_dead_code`` stands in for the agent grepping every export for
#: usages across the tree to prove it unreachable — an expensive manual sweep.
#: Floored conservatively between a single-file health call and a full overview.
DEAD_CODE_FLOOR = 800
RISK_FLOOR = 2_000
#: ``get_why`` replaces git archaeology on the code it is asked about. A single
#: ``git log -p -5`` on one file measures 4,834-7,108 estimated tokens across
#: three of this repository's hot files, and that is the cheapest form of the
#: question -- ``git blame --line-porcelain`` on the same files runs to
#: 416,000-562,000. Floored under half of the cheapest one.
WHY_FLOOR = 3_000
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
#: The flat floor was the most plainly wrong constant here: it credited 500
#: tokens against live delivered sizes of 4,439-4,671, so the tool recorded
#: itself as a net cost on every call. Reading the diff is what it replaces,
#: and ``git show`` with patch measures a 6,094-token median on this
#: repository's commits, so the per-file term is a quarter of that median
#: divided across a typical diff. The cap keeps a 200-file refactor from
#: claiming a number nobody would have read.
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


def _estimate_get_answer(result: dict[str, Any]) -> int:
    """Search call + cited-file reads the answer obviated.

    Counts only answered calls (non-empty ``answer``): a gated/low response
    explicitly tells the agent to go read, so the reads still happen and
    claiming them as saved would repeat the E11 miscalibration.
    """
    if not str(result.get("answer") or "").strip():
        return 0
    paths = {
        p
        for p in [*(result.get("citations") or []), *(result.get("fallback_targets") or [])[:3]]
        if isinstance(p, str) and p
    }
    if not paths:
        return 0
    return ANSWER_SEARCH_FLOOR + len(paths) * ANSWER_READ_FLOOR_PER_FILE


def _estimate_get_change_risk(result: dict[str, Any]) -> int:
    """Floor plus a per-changed-file term — the diff the agent did not read.

    ``features.nf`` is the changed-file count the scorer already computed
    (``core/analysis/change_risk/service.py:316``), so this stays dict-only.
    Falls back to the flat floor when the payload carries no count.
    """
    if result.get("error"):
        return 0
    features = result.get("features")
    files = features.get("nf") if isinstance(features, dict) else None
    if not isinstance(files, int) or files <= 0:
        return CHANGE_RISK_FLOOR
    return min(CHANGE_RISK_MAX, CHANGE_RISK_FLOOR + files * CHANGE_RISK_PER_FILE)


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
    "get_why": _fixed_floor(WHY_FLOOR),
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
