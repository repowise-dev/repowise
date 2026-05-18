"""MCP Tool: get_answer — RAG-style synthesis over the wiki layer.

Single-call retrieval + LLM synthesis. Replaces the agent's multi-turn
search → context → read loop with one tool call that returns:

    {
      "answer":            str   — 2–5 sentence synthesised answer
      "citations":         list  — file paths backing the answer
      "confidence":        str   — "high" | "medium" | "low"
      "fallback_targets":  list  — top retrieval hits the agent should Read
                                   to verify (always present)
      "retrieval":         list  — raw top-N hits with snippets
    }

When no LLM provider is configured, the tool degrades to retrieval-only
mode (returns ranked hits + snippets, confidence="low") so C1 / index-only
deployments still benefit from the structured single-call shortcut.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json as _json
import os
import time
from pathlib import Path
from typing import Any

from sqlalchemy import select

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import AnswerCache, Page, WikiSymbol
from repowise.server.mcp_server._answer_context import (
    build_context_block as _build_context_block_v2,
)
from repowise.server.mcp_server._answer_context import (
    build_structured_prelude as _build_structured_prelude,
)
from repowise.server.mcp_server._answer_context import (
    fetch_relevant_decisions as _fetch_relevant_decisions,
)
from repowise.server.mcp_server._answer_context import is_why_question as _is_why_question
from repowise.server.mcp_server._answer_pipeline import (
    apply_pagerank_bias as _apply_pagerank_bias,
)
from repowise.server.mcp_server._answer_pipeline import (
    expand_via_graph as _expand_via_graph,
)
from repowise.server.mcp_server._answer_pipeline import (
    hybrid_retrieve as _hybrid_retrieve,
)
from repowise.server.mcp_server._answer_pipeline import hydrate_hits as _hydrate_hits
from repowise.server.mcp_server._helpers import (
    _get_repo,
    _resolve_repo_context,
    _unsupported_repo_all,
)
from repowise.server.mcp_server._meta import answer_hint as _answer_hint
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._server import mcp

# How many top retrieval hits to enrich with WikiSymbol context. Enriching
# every hit produces large responses that bloat the cached prompt prefix on
# multi-turn agent sessions without changing the answer — the agent typically
# cites the top-1 file. Top-2 captures the primary navigation need with a
# bounded payload.
_ENRICH_TOP_N_HITS = 2
# How many symbols per enriched file. Bounded to keep the context block from
# growing unboundedly on dense files. We allocate more slots to the top hit
# (where the answer usually lives) and fewer to secondary hits.
_MAX_SYMBOLS_TOP_HIT = 10
_MAX_SYMBOLS_PER_HIT = 4

# When a retrieved file contains symbols whose name matches an identifier
# from the question, we promote those to the top of the symbol list for that
# file, pass a longer docstring, and attach a source excerpt so the LLM
# actually sees the method body — not just a stub docstring. Without this,
# specific-method questions get hedged answers even on dominant retrievals.
_MATCHED_SYMBOL_DOC_CHARS = 400
_MATCHED_SYMBOL_SOURCE_LINES = 40

# Sort priority by symbol kind. Classes first because "what does X do" /
# "which class inherits from Y" questions resolve at the class level. Then
# top-level functions, then methods (which usually only matter once the
# class context is established).
_KIND_PRIORITY = {"class": 0, "interface": 0, "function": 1, "method": 2}
# Per-symbol docstring truncation. Keeps the context block bounded — the
# first sentence is typically sufficient and trailing prose mostly contributes
# cache-write cost on follow-up turns.
_MAX_SYMBOL_DOC_CHARS = 120

# Confidence gate for synthesis. When the top retrieval hit is NOT clearly
# dominant relative to the second-best hit, skip LLM synthesis and return
# ranked snippets only. This forces the agent to ground in source rather than
# trust a possibly-wrong frame. Generic, repo-agnostic, no question parsing.
# Failure modes addressed:
#   (a) wrong-target retrieval where top-1 and top-2 are both plausible;
#   (b) synthesis hallucination on tangential top hits.
_DOMINANCE_RATIO = 1.2
_COVERAGE_THRESHOLD = 0.66

# Hedge-phrase markers that indicate the LLM refused to synthesize even though
# retrieval was dominant. When the answer contains any of these, we downgrade
# confidence to "low" and drop the retrieval payload — the hits aren't useful
# to a consumer that has already been told to go read the source, and letting
# them ride through the conversation cache inflates multi-turn cost.
_HEDGE_MARKERS = (
    "do not contain",
    "does not contain",
    "is not contained",
    "are not contained",
    "not contain sufficient",
    "not contain enough",
    "is not covered",
    "not covered in the",
    "not covered by the",
    "you should inspect",
    "you should consult",
    "consult the source",
    "inspect the source",
    "cannot be determined",
    "cannot determine",
    "is not clear",
    "insufficient information",
    "not enough information",
    "without more context",
    "without additional context",
    "didn't surface",
    "did not surface",
    "was not surfaced",
    "was not found in",
)


def _extract_question_identifiers(question: str) -> set[str]:
    """Pull out Python-looking identifiers the question names explicitly.

    Targets: snake_case (``_local_reachability_density``), CamelCase
    (``NearestCentroid``), dotted paths (``BaseLabelPropagation.fit``).
    Filtered to ≥3 chars, non-stopwords, non-pure-lowercase-English (unless
    they contain an underscore or a digit — otherwise every common word
    matches). The result drives question-aware symbol promotion in
    ``_hydrate_symbols_for_hits``.
    """
    import re

    ids: set[str] = set()
    # Match bare identifiers and dotted paths: first char letter/underscore,
    # rest alnum/underscore, optionally with dotted continuations.
    for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", question):
        # Split dotted paths into both the full thing and the leaf.
        parts = tok.split(".")
        candidates = [tok] + parts
        for c in candidates:
            if len(c) < 3:
                continue
            if c.lower() in _STOPWORDS:
                continue
            # Heuristic: keep if it contains an uppercase letter anywhere
            # (covers CamelCase and sentence-initial capitalised nouns like
            # ``Version`` that are typically class names in Python), a
            # digit, or an underscore. Pure-lowercase English words like
            # ``method`` / ``class`` / ``dtype`` are dropped — they are
            # poor promotion signals and match too broadly.
            has_upper = any(ch.isupper() for ch in c)
            has_under = "_" in c
            has_digit = any(ch.isdigit() for ch in c)
            if has_upper or has_under or has_digit:
                ids.add(c)
    return ids


def _read_symbol_source(
    repo_root: Path | None,
    file_path: str,
    start_line: int,
    end_line: int,
    max_lines: int = _MATCHED_SYMBOL_SOURCE_LINES,
) -> str | None:
    """Return the literal source body for a symbol, bounded to max_lines.

    The bounded source is the key ingredient for question-matched symbols.
    The LLM was already getting the file-level summary and a truncated
    docstring; what it was missing was the actual code. With 40 lines of
    the method body in front of it, the synthesis step can answer "how
    does X work" without hedging back to "you should inspect the source".
    """
    if repo_root is None or start_line < 1:
        return None
    try:
        abs_path = (repo_root / file_path).resolve()
        try:
            abs_path.relative_to(repo_root.resolve())
        except ValueError:
            return None
        text = abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lines = text.splitlines()
    if start_line > len(lines):
        return None
    hi = end_line if end_line and end_line >= start_line else start_line + max_lines
    hi = min(hi, start_line + max_lines, len(lines))
    body = "\n".join(lines[start_line - 1:hi])
    return body


def _answer_is_hedged(answer_text: str) -> bool:
    """True when the synthesized answer confesses it can't answer.

    Retrieval dominance alone doesn't tell you whether the LLM produced a
    usable answer — the underlying model happily admits insufficiency even
    on a top-scoring hit. Treat an admitted non-answer as low confidence,
    regardless of how dominant retrieval was.
    """
    low = (answer_text or "").lower()
    return any(marker in low for marker in _HEDGE_MARKERS)

# The dominance ratio threshold (top_score / second_score >= 1.2x) separates
# reliable retrievals from ambiguous ones. This is a property of BM25-style
# retrieval with a coverage re-ranker on top, not of any particular repository;
# tune if a deployment shows systematic over- or under-gating.

# When the gate triggers and we drop synthesis, fetch this many chars of
# real page content per top hit so the agent has substantive raw material
# to ground in (vs. one-line summary that's too thin to act on).
_GATED_EXCERPT_CHARS = 600
_GATED_RETURN_HITS = 3

# Path-prefix domain heuristics — down-weight cross-domain retrievals so a
# clearly backend question doesn't anchor on a same-vocabulary UI file (and
# vice versa). The penalty is multiplicative, not absolute, so a strongly
# matching cross-domain file can still survive on raw signal; the goal is
# to break ties when retrieval is otherwise ambiguous, not to censor results.
_UI_PATH_PREFIXES = (
    "packages/ui/",
    "packages/web/",
    "frontend/",
    "website/",
)
_BACKEND_PATH_PREFIXES = (
    "packages/server/",
    "packages/core/",
    "packages/cli/",
    "backend/",
    "modal_app/",
)
# Tokens that flag a question as being about a specific domain. Kept small
# and conservative — ambiguous questions (both lists hit, or neither) fall
# through to no penalty rather than misclassify.
_UI_QUESTION_TOKENS = frozenset({
    "ui", "frontend", "component", "react", "tsx", "jsx", "render",
    "css", "tailwind", "view", "dashboard", "button", "modal", "page",
    "browser", "client-side",
})
_BACKEND_QUESTION_TOKENS = frozenset({
    "backend", "server", "api", "endpoint", "route", "indexer", "ingest",
    "ingestion", "pipeline", "database", "db", "schema", "migration",
    "orchestrat", "mcp", "fastapi", "sqlalchemy", "subprocess", "worker",
    "cli", "command", "sql",
})
# Penalty factor applied to cross-domain hits. 0.5 is strong enough to
# overtake a same-domain near-tie but small enough that a dominant cross-
# domain hit (real top score outlier) still survives.
_DOMAIN_PENALTY = 0.5

# Floor on raw top-hit score for "high" confidence. Below this the answer
# may be technically dominant but built on weak retrieval — downgrade to
# "medium" so the agent verifies. Tuned against observed BM25 ranges on
# the wiki corpus where useful hits routinely score >1.5.
_HIGH_CONFIDENCE_SCORE_FLOOR = 1.5

# Schema version stamped on every cached payload. Bump whenever the response
# shape changes in a way that would mislead a consumer reading an old cached
# entry (new top-level fields, semantics of existing fields). On cache reads
# we treat any payload at a lower version as a miss and re-synthesise — the
# alternative (returning stale-shape payloads) silently bypasses every
# improvement to the tool and was the failure mode that hid the entire
# get_answer rework behind a cache hit during testing.
# v3: retrieval pipeline overhaul (hybrid FTS+vector, PageRank bias, graph
# expansion, structured prelude, decision fusion). Cached v2 payloads were
# synthesised over weaker retrieval — bumping forces re-synthesis so the
# upgrade actually reaches callers without waiting for cache expiry.
_ANSWER_SCHEMA_VERSION = 3

# Intersection-retrieval connectives. If a question contains any of these
# (case-insensitive whole-word), it's likely a relational/multi-entity
# question. We split the question on the connective, run two FTS passes,
# and boost any page that appears in BOTH result sets — the page at the
# intersection is much more likely to be the actual answer than a page
# at the top of either single-side query.
# This is grammar, not domain — the same list applies to any English-language
# code question, independent of the repository or codebase.
_RELATIONAL_CONNECTIVES = (
    " between ", " from ", " across ", " through ", " with ",
    " and ", " versus ", " vs ",
)

# Term-coverage re-ranker tuning. Multiplies BM25 by (FLOOR + (1-FLOOR)*coverage)
# where coverage = (# distinct query terms present in hit) / (# query terms).
# FLOOR=0.5 → single-concept questions (coverage≈1.0) are unaffected;
# multi-constraint questions where a hit covers 1/3 of terms get scored at 0.67
# of their raw BM25 (vs 1.0 for a hit covering 3/3). Conjunctive coverage
# becomes a tie-breaker rather than a hard filter.
_COVERAGE_FLOOR = 0.5
# English stopwords — minimal list, just enough to keep "what is the" from
# dominating coverage. Not language-specific, not repo-specific.
_STOPWORDS = frozenset({
    "a","an","the","is","are","was","were","be","been","being","of","to","in",
    "on","at","by","for","with","from","as","that","this","these","those","it",
    "its","and","or","but","not","no","do","does","did","done","have","has",
    "had","what","which","who","whom","whose","when","where","why","how","can",
    "could","should","would","may","might","will","shall","i","you","he","she",
    "we","they","me","him","her","us","them","my","your","his","their","our",
    "if","then","than","so","such","there","here","about","into","through",
    "between","across","over","under","up","down","out","off","via",
})
# Cap on bytes read from source per symbol when we recover a real signature
# from disk (multi-line def with type annotations). Anything longer than this
# gets truncated; the agent can call get_symbol for the full body.
_MAX_RICH_SIG_LINES = 4


def _hash_question(question: str) -> str:
    """Stable SHA-256 of the normalized question. Lowercase + strip + collapse ws."""
    norm = " ".join(question.lower().strip().split())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()

_log = __import__("logging").getLogger("repowise.mcp.answer")

_SYSTEM_PROMPT = (
    "You are a code-aware retrieval assistant. You are given a developer "
    "question plus excerpts from a project wiki — file summaries, symbol "
    "signatures with docstrings, and (for symbols whose name matches the "
    "question) the actual source body. Answer thoroughly and concretely, "
    "citing source files by relative path inline like (path/to/file.py) "
    "and line numbers when you have them. Prefer a structured answer "
    "(headings / bullets / short code block citing the symbol) over a "
    "paragraph when the question asks about mechanism or architecture. "
    "Aim for 150–400 words — enough to cover the asked aspects without "
    "padding. If a [question-match] symbol's source body is provided, "
    "you have enough material to answer — ground in that body. Only "
    "hedge (say 'inspect the source' / 'the excerpts do not contain…') "
    "when there is genuinely no relevant signature, docstring, or source "
    "body in the excerpts. Never invent file paths."
)

_USER_TEMPLATE = """\
Question: {question}

Project wiki excerpts (top {n} retrieval hits):

{context}

Answer thoroughly (150–400 words). Cite file paths inline and line
numbers when the excerpt provides them. Prefer a structured layout
(headings, bullets, short code block from the source body) on
mechanism / architecture questions. Only hedge if no signature,
docstring, or source body in the excerpts is relevant.
"""


def _load_repo_provider_config(
    repo_path: Path | None,
) -> tuple[str | None, str | None, dict[str, str]]:
    """Read persisted provider config for a repo.

    `repowise init` writes the chosen provider + model into
    ``.repowise/state.json`` and the corresponding API key into
    ``.repowise/.env``. The MCP server doesn't load that .env at startup,
    so without this helper get_answer can't reach an LLM unless the user
    also exports REPOWISE_PROVIDER / OPENAI_API_KEY in the shell that
    launched Claude Code. This recovers the persisted values so the same
    provider used for init / update is reused for get_answer.

    Returns ``(provider_name, model, env_overlay)``. Any field may be
    None / empty — callers should fall back to process env when missing.
    """
    if repo_path is None:
        return None, None, {}

    state_path = repo_path / ".repowise" / "state.json"
    env_path = repo_path / ".repowise" / ".env"

    name: str | None = None
    model: str | None = None
    overlay: dict[str, str] = {}

    try:
        if state_path.is_file():
            data = _json.loads(state_path.read_text(encoding="utf-8"))
            name = data.get("provider") or None
            model = data.get("model") or None
    except Exception:
        _log.debug("Failed to read %s", state_path, exc_info=True)

    try:
        if env_path.is_file():
            for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip("'").strip('"')
                if key:
                    overlay[key] = val
    except Exception:
        _log.debug("Failed to read %s", env_path, exc_info=True)

    return name, model, overlay


def _resolve_provider_for_answer(repo_path: Path | None = None):
    """Best-effort provider lookup mirroring cli/helpers.resolve_provider.

    Avoids the click dependency from the cli package. Returns a BaseProvider
    or None if no API key / provider is configured.

    Resolution order: process env vars first, then ``.repowise/state.json``
    + ``.repowise/.env`` for the active repo. The persisted values are the
    same ones ``repowise init`` and ``repowise update`` use, so get_answer
    follows the user's existing provider choice without a separate config.
    """
    try:
        from repowise.core.providers.llm.registry import get_provider
    except Exception:
        _log.warning("Provider registry import failed", exc_info=True)
        return None

    persisted_name, persisted_model, env_overlay = _load_repo_provider_config(
        repo_path
    )

    def _env(key: str) -> str | None:
        # Prefer real process env so an explicit shell export still wins;
        # fall back to .repowise/.env only when the process env is empty.
        return os.environ.get(key) or env_overlay.get(key) or None

    name = os.environ.get("REPOWISE_PROVIDER") or persisted_name
    model = (
        os.environ.get("REPOWISE_DOC_MODEL")
        or os.environ.get("REPOWISE_MODEL")
        or persisted_model
    )

    def _try(provider_name: str, **kwargs: Any):
        try:
            return get_provider(provider_name, **kwargs)
        except Exception:
            _log.warning("get_provider(%s) failed", provider_name, exc_info=True)
            return None

    def _resolve_base_url(provider_name: str) -> str | None:
        mapping = {
            "openai": ["OPENAI_BASE_URL"],
            "anthropic": ["ANTHROPIC_BASE_URL"],
            "gemini": ["GEMINI_BASE_URL"],
            "deepseek": ["DEEPSEEK_BASE_URL"],
            "ollama": ["OLLAMA_BASE_URL"],
            "litellm": ["LITELLM_BASE_URL", "LITELLM_API_BASE"],
        }
        for env_var in mapping.get(provider_name, []):
            val = _env(env_var)
            if val:
                return val
        return None

    # Explicit selection wins.
    if name:
        kw: dict[str, Any] = {}
        if model:
            kw["model"] = model
        if name == "anthropic" and _env("ANTHROPIC_API_KEY"):
            kw["api_key"] = _env("ANTHROPIC_API_KEY")
        elif name == "openai" and _env("OPENAI_API_KEY"):
            kw["api_key"] = _env("OPENAI_API_KEY")
        elif name == "deepseek" and _env("DEEPSEEK_API_KEY"):
            kw["api_key"] = _env("DEEPSEEK_API_KEY")
        elif name == "gemini" and (
            _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY")
        ):
            kw["api_key"] = _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY")
        base_url = _resolve_base_url(name)
        if base_url:
            kw["base_url"] = base_url
        return _try(name, **kw)

    # Auto-detect from API keys.
    if _env("ANTHROPIC_API_KEY"):
        kw = {"api_key": _env("ANTHROPIC_API_KEY")}
        if model:
            kw["model"] = model
        base_url = _resolve_base_url("anthropic")
        if base_url:
            kw["base_url"] = base_url
        return _try("anthropic", **kw)
    if _env("OPENAI_API_KEY"):
        kw = {"api_key": _env("OPENAI_API_KEY")}
        if model:
            kw["model"] = model
        base_url = _resolve_base_url("openai")
        if base_url:
            kw["base_url"] = base_url
        return _try("openai", **kw)
    if _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY"):
        kw = {"api_key": _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY")}
        if model:
            kw["model"] = model
        base_url = _resolve_base_url("gemini")
        if base_url:
            kw["base_url"] = base_url
        return _try("gemini", **kw)
    if _env("OLLAMA_BASE_URL"):
        kw = {"base_url": _env("OLLAMA_BASE_URL")}
        if model:
            kw["model"] = model
        return _try("ollama", **kw)
    if _env("DEEPSEEK_API_KEY"):
        kw = {"api_key": _env("DEEPSEEK_API_KEY")}
        if model:
            kw["model"] = model
        base_url = _resolve_base_url("deepseek")
        if base_url:
            kw["base_url"] = base_url
        return _try("deepseek", **kw)
    return None


def _build_context_block(hits: list[dict], max_chars_per_hit: int = 800) -> str:
    """Format retrieval hits as a compact text block for the LLM.

    Each hit includes:
      * file path + title + retrieval score
      * file-level summary (Page.summary, capped at max_chars_per_hit)
      * per-symbol signature + docstring; for question-matched symbols
        (flagged ``_matched`` by ``_hydrate_symbols_for_hits``) the
        docstring runs to 400 chars and we append up to 40 lines of the
        actual source body as a fenced code block. The source body is
        what lets the LLM answer "how does X work" instead of hedging.
    """
    parts = []
    for i, h in enumerate(hits, start=1):
        body_src = h.get("summary") or h.get("snippet") or ""
        body = body_src[:max_chars_per_hit]
        block = [
            f"[{i}] {h['target_path']} (score={h['score']:.3f})",
            f"    title: {h['title']}",
            f"    summary: {body}",
        ]
        symbols = h.get("symbols") or []
        if symbols:
            block.append("    symbols:")
            for s in symbols:
                sig = s.get("signature") or s.get("name") or ""
                kind = s.get("kind") or "?"
                matched = bool(s.get("_matched"))
                doc = (s.get("docstring") or "").strip()
                doc_cap = _MATCHED_SYMBOL_DOC_CHARS if matched else _MAX_SYMBOL_DOC_CHARS
                tag = " [question-match]" if matched else ""
                block.append(f"      - [{kind}]{tag} {sig}")
                if doc:
                    trimmed = " ".join(doc.split())[:doc_cap]
                    block.append(f"          docstring: {trimmed}")
                src = s.get("source_excerpt")
                if src:
                    block.append("          source:")
                    for line in src.splitlines():
                        block.append(f"              {line}")
        parts.append("\n".join(block))
    return "\n\n".join(parts)


def _read_signature_from_source(
    repo_root: Path | None, file_path: str, start_line: int
) -> str | None:
    """Read the symbol's actual signature line from disk.

    Returns the def/class line (or its multi-line continuation) verbatim from
    the source file. Captures everything WikiSymbol.signature strips:
      * base classes for `class Foo(Bar, Baz):`
      * decorators (one line above the def)
      * full type annotations across line continuations

    None on any failure — caller falls back to the stored signature.
    """
    if repo_root is None:
        return None
    try:
        abs_path = (repo_root / file_path).resolve()
        # Defense in depth: never read outside the repo root.
        try:
            abs_path.relative_to(repo_root.resolve())
        except ValueError:
            return None
        text = abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lines = text.splitlines()
    if not lines or start_line < 1 or start_line > len(lines):
        return None
    # Walk forward up to _MAX_RICH_SIG_LINES until we close the parenthesis
    # group (Python signatures often span multiple lines for type hints).
    sig_lines: list[str] = []
    paren_depth = 0
    for i in range(start_line - 1, min(start_line - 1 + _MAX_RICH_SIG_LINES, len(lines))):
        line = lines[i]
        sig_lines.append(line.strip())
        paren_depth += line.count("(") - line.count(")")
        if line.rstrip().endswith(":") and paren_depth <= 0:
            break
    if not sig_lines:
        return None
    return " ".join(sig_lines)


async def _hydrate_symbols_for_hits(
    session,
    repo_id: str,
    hits: list[dict],
    ctx: Any = None,
    question_ids: set[str] | None = None,
) -> None:
    """Mutate `hits` in place: attach `symbols` list to top-N file_page hits.

    Question-aware promotion: if ``question_ids`` contains identifiers that
    match symbols in the retrieved files, those symbols move to the top of
    their file's symbol list, carry a longer docstring, and get a source
    excerpt (``source_excerpt``). This is the difference between the LLM
    seeing ``class LocalOutlierFactor`` at the file top (and hedging on a
    question about ``_local_reachability_density``) vs. seeing the actual
    method body and answering it.

    Top hit gets ``_MAX_SYMBOLS_TOP_HIT`` slots; secondaries get the smaller
    ``_MAX_SYMBOLS_PER_HIT``. Symbols not matching a question id carry the
    short 120-char docstring; matched symbols carry 400 chars + source body.
    """
    question_ids = question_ids or set()
    # Case-folded copy for matching.
    qids_lower = {q.lower() for q in question_ids}

    # Identify the top file_page hits in retrieval-rank order. `hits` is
    # already sorted by descending score upstream.
    enrich_paths: list[str] = []
    for h in hits:
        if (
            h.get("target_path")
            and h.get("page_type") == "file_page"
            and len(enrich_paths) < _ENRICH_TOP_N_HITS
        ):
            enrich_paths.append(h["target_path"])
    if not enrich_paths:
        return

    res = await session.execute(
        select(WikiSymbol)
        .where(
            WikiSymbol.repository_id == repo_id,
            WikiSymbol.file_path.in_(enrich_paths),
        )
        .order_by(WikiSymbol.file_path, WikiSymbol.start_line)
    )
    by_file: dict[str, list[dict]] = {}
    repo_root = Path(str(ctx.path)) if ctx and ctx.path else None
    for row in res.scalars().all():
        rich_sig = _read_signature_from_source(
            repo_root, row.file_path, row.start_line
        )
        # Does the symbol name match any identifier from the question?
        name_lower = (row.name or "").lower()
        qname_lower = (row.qualified_name or "").lower()
        matched = bool(
            qids_lower
            and (
                name_lower in qids_lower
                or qname_lower in qids_lower
                or any(
                    q in name_lower or q in qname_lower
                    for q in qids_lower
                    if len(q) >= 5  # avoid spurious substring matches on short tokens
                )
            )
        )
        entry: dict[str, Any] = {
            "name": row.name,
            "kind": row.kind,
            "signature": rich_sig or row.signature,
            "docstring": row.docstring or "",
            "start_line": row.start_line,
            "end_line": row.end_line,
            "_matched": matched,
        }
        if matched:
            src = _read_symbol_source(
                repo_root, row.file_path, row.start_line, row.end_line
            )
            if src:
                entry["source_excerpt"] = src
        by_file.setdefault(row.file_path, []).append(entry)

    # Sort: matched symbols first (document order within the match group),
    # then unmatched in start_line order. Cap per file — top hit gets more
    # slots than secondary hits.
    for i, h in enumerate(hits):
        path = h.get("target_path")
        if path not in by_file:
            continue
        syms = by_file[path]
        syms.sort(key=lambda s: (not s["_matched"], s["start_line"]))
        cap = _MAX_SYMBOLS_TOP_HIT if i == 0 else _MAX_SYMBOLS_PER_HIT
        # Guarantee at least one matched symbol survives the cap, even if
        # the file has more than `cap` symbols before it.
        kept: list[dict] = [s for s in syms if s["_matched"]][: cap]
        for s in syms:
            if s in kept:
                continue
            if len(kept) >= cap:
                break
            kept.append(s)
        # Sort final slice by start_line for natural reading order.
        kept.sort(key=lambda s: s["start_line"])
        h["symbols"] = kept


def _split_relational(question: str) -> list[str] | None:
    """If the question is relational (contains a connective like 'and' or
    'between'), split it into two sub-queries on the FIRST matching
    connective. Returns [left, right] or None if not relational.

    Heuristic only — works on English grammar, not on code or repo terms.
    """
    q = " " + question.strip() + " "
    qlow = q.lower()
    for conn in _RELATIONAL_CONNECTIVES:
        idx = qlow.find(conn)
        if idx > 0:
            left = q[:idx].strip()
            right = q[idx + len(conn):].strip()
            # Both sides must have at least 3 content terms to be a real
            # multi-entity question (not e.g. "what is X and how").
            if len(_question_terms(left)) >= 3 and len(_question_terms(right)) >= 3:
                return [left, right]
    return None


async def _intersection_boost(question: str, hits: list[dict], ctx: Any = None) -> None:
    """For relational questions, boost any hit that appears in both halves
    of a split-FTS retrieval. Mutates `hits` in place: adds a multiplicative
    bonus to `score` for hits that appear in both subset retrievals.

    Universal IR principle: pages at the intersection of two query halves
    are much more likely to answer relational questions than pages at the
    top of either half alone. Independent of repo or domain.
    """
    parts = _split_relational(question)
    if parts is None or ctx is None or ctx.fts is None:
        return
    sub_hit_ids: list[set] = []
    for sub_q in parts:
        try:
            sub = await asyncio.wait_for(
                ctx.fts.search(sub_q, limit=15), timeout=3.0
            )
            sub_hit_ids.append({h.page_id for h in sub})
        except Exception:
            return
    if len(sub_hit_ids) < 2:
        return
    intersection = sub_hit_ids[0] & sub_hit_ids[1]
    if not intersection:
        return
    # 2× boost for hits at the intersection — strong enough to overtake
    # a single-side top hit, not so strong that it ignores BM25 entirely.
    for h in hits:
        if h.get("page_id") in intersection:
            h["score"] = h.get("score", 0.0) * 2.0
            h["_intersection"] = True
    hits.sort(key=lambda h: h["score"], reverse=True)


async def _enrich_gated_excerpts(hits: list[dict], ctx: Any = None) -> None:
    """For the gated (low-confidence) return path, fetch real page content
    for top hits so the agent has substantive raw material instead of
    one-line summaries. Mutates `hits` in place — adds an `excerpt` field.

    Universal motivation: thin retrieval output forces consumers to fall
    back on priors instead of grounding in source. Symmetric with the
    enrichment we already do for synthesis.
    """
    if not hits:
        return
    page_ids = [h["page_id"] for h in hits[:_GATED_RETURN_HITS] if h.get("page_id")]
    if not page_ids:
        return
    try:
        async with get_session(ctx.session_factory) as session:
            res = await session.execute(
                select(Page.id, Page.content_md).where(Page.id.in_(page_ids))
            )
            content_by_id = {row[0]: (row[1] or "") for row in res.all()}
    except Exception:
        return
    for h in hits[:_GATED_RETURN_HITS]:
        body = content_by_id.get(h.get("page_id"), "")
        if body:
            h["excerpt"] = body[:_GATED_EXCERPT_CHARS]


def _detect_question_domain(question: str) -> str | None:
    """Return ``"ui"``, ``"backend"``, or ``None`` when the question is ambiguous.

    Used to break ties on retrievals where vocabulary overlaps across domains
    (e.g. "how does indexing work" could plausibly retrieve a UI status-pill
    component or the actual ingestion pipeline). The classifier is intentionally
    conservative: if both domain token sets fire, or neither does, we return
    ``None`` and apply no penalty — better to leave ranking alone than to
    miscategorise a cross-cutting question.
    """
    qlow = question.lower()
    has_ui = any(tok in qlow for tok in _UI_QUESTION_TOKENS)
    has_backend = any(tok in qlow for tok in _BACKEND_QUESTION_TOKENS)
    if has_ui and not has_backend:
        return "ui"
    if has_backend and not has_ui:
        return "backend"
    return None


def _apply_domain_penalty(hits: list[dict], question: str) -> None:
    """Multiplicatively penalise cross-domain hits in place.

    Mutates ``hits`` and re-sorts by adjusted score. No-op when the question
    domain is ambiguous (see ``_detect_question_domain``). Hits that take the
    penalty get a ``_domain_penalty`` marker so the gated-return path can
    surface the reason to the caller.
    """
    domain = _detect_question_domain(question)
    if domain is None or not hits:
        return
    if domain == "backend":
        bad_prefixes = _UI_PATH_PREFIXES
    else:
        bad_prefixes = _BACKEND_PATH_PREFIXES
    touched = False
    for h in hits:
        tp = h.get("target_path") or ""
        if tp and any(tp.startswith(p) for p in bad_prefixes):
            h["score"] = h.get("score", 0.0) * _DOMAIN_PENALTY
            h["_domain_penalty"] = f"{domain} question; cross-domain path"
            touched = True
    if touched:
        hits.sort(key=lambda h: h["score"], reverse=True)


def _candidate_justification(h: dict) -> str:
    """One-line reason this hit might answer the question.

    Used on the low-confidence return path so the agent sees something
    decision-shaped ("Read file X because it implements Y") instead of a
    flat list of paths it has to scan into. Prefers the matched-symbol name
    over the file summary because the matched symbol is what tied this hit
    to the question in the first place.
    """
    syms = h.get("symbols") or []
    matched = next((s for s in syms if s.get("_matched")), None)
    if matched:
        name = matched.get("name") or matched.get("signature") or "matched symbol"
        kind = matched.get("kind") or "symbol"
        return f"Implements {kind} {name}."
    summary = (h.get("summary") or h.get("snippet") or "").strip()
    if summary:
        # First sentence only; trailing prose is mostly cache-write cost on
        # the consumer side.
        first = summary.split(". ")[0]
        return (first[:160].rstrip() + ".") if first else ""
    title = h.get("title") or ""
    return title[:160]


def _question_terms(question: str) -> list[str]:
    """Extract content terms from a question. Lowercase, alnum-tokenized,
    stopwords + length<3 dropped. Used by the term-coverage re-ranker."""
    import re
    raw = re.findall(r"[a-zA-Z0-9_]+", question.lower())
    return [t for t in raw if len(t) >= 3 and t not in _STOPWORDS]


def _rerank_by_coverage(hits: list[dict], question: str) -> list[dict]:
    """Re-rank FTS hits by term-coverage boost on top of BM25.

    For each hit, compute the fraction of distinct query terms present in
    (title + snippet + summary), then multiply the raw BM25 score by
    (FLOOR + (1-FLOOR)*coverage). Single-concept questions (coverage≈1.0
    across all hits) are unaffected; multi-constraint questions push hits
    that cover all the terms above hits that repeat just one term.

    This addresses a common BM25 failure mode where a hit that matches one
    constraint very strongly can outrank a hit that matches all constraints
    moderately — the latter is usually the better answer for multi-constraint
    questions.
    """
    terms = set(_question_terms(question))
    if not terms or not hits:
        return hits
    n_terms = len(terms)
    for h in hits:
        haystack = " ".join([
            h.get("title", "") or "",
            h.get("snippet", "") or "",
            h.get("summary", "") or "",
        ]).lower()
        # Count distinct terms present (substring match — FTS5 already handles
        # stemming upstream, so we keep this simple).
        present = sum(1 for t in terms if t in haystack)
        coverage = present / n_terms
        raw = h.get("score", 0.0)
        h["_coverage"] = coverage
        h["_raw_score"] = raw
        h["score"] = raw * (_COVERAGE_FLOOR + (1.0 - _COVERAGE_FLOOR) * coverage)
    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits


@mcp.tool()
async def get_answer(
    question: str,
    scope: str | None = None,
    repo: str | None = None,
) -> dict:
    """Synthesised answer to a code question with verified citations and a calibrated trust signal.

    The only tool that pairs RAG retrieval over the wiki with an LLM-written
    answer plus a separately-reported retrieval_quality. Use it as the first
    call on "how does X work" / "where is Y" / "why is Z structured this way"
    questions — it eliminates the search → context → read loop when retrieval
    is dominant. On low confidence it returns a structured ``best_guesses``
    list (one-line justifications per candidate) instead of an empty answer,
    so the caller always has somewhere concrete to Read next.

    Returns ``{answer, citations, confidence, retrieval_quality,
    fallback_targets, best_guesses?, next_action_hint?}``. Always verify cited
    paths exist if you intend to act on them.

    Args:
        question: developer question.
        scope: optional path prefix to restrict retrieval (e.g. "src/pkg/").
        repo: repository identifier; usually omitted.
    """
    if repo == "all":
        return _unsupported_repo_all("get_answer")

    t0 = time.perf_counter()
    ctx = await _resolve_repo_context(repo)

    if not question or not question.strip():
        return {
            "answer": "",
            "citations": [],
            "confidence": "low",
            "fallback_targets": [],
            "retrieval": [],
            "error": "question is required",
            "_meta": _build_meta(timing_ms=(time.perf_counter() - t0) * 1000),
        }

    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        repo_id = repository.id

    # --- Cache lookup --------------------------------------------------------
    # Scope: ignore the (rare) `scope` argument in the cache key for now;
    # scoped queries are uncommon and including scope would balloon hit rate
    # variance. We hash on (repo_id, normalized_question) only.
    qhash = _hash_question(question)
    async with get_session(ctx.session_factory) as session:
        res = await session.execute(
            select(AnswerCache).where(
                AnswerCache.repository_id == repo_id,
                AnswerCache.question_hash == qhash,
            )
        )
        cached = res.scalar_one_or_none()
    if cached is not None:
        with contextlib.suppress(Exception):
            payload = _json.loads(cached.payload_json)
            # Schema bypass: payloads from a pre-rework code path don't carry
            # the fields the current consumer expects (retrieval_quality,
            # best_guesses, calibrated confidence). Returning them masks every
            # subsequent improvement until the cache happens to expire. Bypass
            # silently so the next write upgrades the row.
            cached_version = payload.get("_schema_version", 1)
            schema_stale = cached_version < _ANSWER_SCHEMA_VERSION
            # Bypass-on-hedged: if the cached answer hedged, the retrieval +
            # symbol pipeline has since been upgraded (question-aware symbol
            # promotion, source-body excerpts). Give synthesis another shot
            # with the new context rather than pinning the bad answer.
            hedged_cache = _answer_is_hedged(payload.get("answer", ""))
            if schema_stale:
                _log.info(
                    "Bypassing cache entry at schema v%s (current v%s)",
                    cached_version, _ANSWER_SCHEMA_VERSION,
                )
            elif hedged_cache:
                _log.info("Bypassing hedged cache entry for re-synthesis")
            else:
                payload["_meta"] = _build_meta(
                    timing_ms=(time.perf_counter() - t0) * 1000,
                    cached=True,
                    hint=_answer_hint(
                        payload.get("confidence", "low"),
                        len(payload.get("retrieval", [])),
                    ),
                    repository=repository,
                )
                return payload

    # --- Retrieval pipeline ------------------------------------------------
    # Stages live in ``_answer_pipeline`` so each can evolve without
    # rereading the orchestrator: hybrid retrieval (FTS + vector + RRF) →
    # hydration → coverage rerank → domain penalty → intersection boost →
    # PageRank bias → 1-hop graph expansion. The orchestrator only sequences
    # them and decides when to stop (cap at 5 for the response payload).
    hits = await _hybrid_retrieve(question, ctx)
    hits = await _hydrate_hits(hits, ctx, scope=scope)

    # Term-coverage re-rank before any graph-aware bias so conjunctive
    # matches survive the merge.
    hits = _rerank_by_coverage(hits, question)
    # Domain heuristic: down-weight cross-domain hits (e.g. UI files for a
    # clearly backend question). Cheap tie-breaker, never a hard filter.
    _apply_domain_penalty(hits, question)
    # Intersection-retrieval boost for relational questions (multi-entity).
    # Pages at the intersection of two split-FTS halves get a 2× bonus.
    with contextlib.suppress(Exception):
        await _intersection_boost(question, hits, ctx)
    # PageRank bias: nudge architecturally central files above peripheral
    # ones at the same retrieval score. Damped + normalised within the
    # candidate set so it's a tie-breaker, not a wholesale reordering.
    with contextlib.suppress(Exception):
        await _apply_pagerank_bias(hits, ctx)
    # Graph expansion: 1-hop walk from the top hits to rescue near-misses
    # where retrieval landed in the right module but on the wrong file
    # (consumer instead of orchestrator). Adds up to 3 neighbors with a
    # damped score, then re-sorts.
    with contextlib.suppress(Exception):
        hits = await _expand_via_graph(hits, ctx)
    # Always cap retrieval hits at 5 for the response payload.
    hits = hits[:5]

    # Enrich each file_page hit with its top-N WikiSymbol rows. Question-
    # aware: identifiers extracted from the question promote matching
    # symbols and attach a source-body excerpt — the difference between a
    # hedged answer on a specific-method question and a grounded one.
    question_ids = _extract_question_identifiers(question)
    if hits:
        with contextlib.suppress(Exception):
            async with get_session(ctx.session_factory) as session:
                await _hydrate_symbols_for_hits(
                    session, repo_id, hits, ctx, question_ids=question_ids
                )

    fallback_targets = [
        h["target_path"] for h in hits if h.get("target_path")
    ]

    if not hits:
        return {
            "answer": "",
            "citations": [],
            "confidence": "low",
            "fallback_targets": [],
            "retrieval": [],
            "note": (
                "No wiki hits for this question. Fall back to "
                "search_codebase or Grep to locate candidate files."
            ),
            "_meta": _build_meta(
                timing_ms=(time.perf_counter() - t0) * 1000,
                hint=_answer_hint("low", 0),
                repository=repository,
            ),
        }

    # --- Confidence gate ---------------------------------------------------
    # Skip synthesis when retrieval is NOT clearly dominant. The dominance
    # ratio (top score / second score) is the sole gating criterion: above
    # the threshold the top hit is reliably the right answer; below it the
    # top-1 / top-2 ambiguity is large enough that we hand the agent ranked
    # excerpts and let it ground in source.
    #
    # Coverage (fraction of query terms present in the top hit) is also
    # available via the re-ranker and is used to bias score-based ranking,
    # but is intentionally NOT used as a hard gate here. Natural-language
    # questions rarely have all their content terms co-occurring in a single
    # page (typical coverage is 0.15–0.25), so a coverage threshold over-
    # fires on confidently-dominant retrievals and degrades the cheap path.
    if len(hits) >= 2:
        top_score = hits[0].get("score", 0.0)
        second_score = hits[1].get("score", 0.0) or 1e-9

        # Two-tier gating: at high retrieval quality (both scores
        # excellent), close ratios are expected and normal — use an
        # absolute gap instead.  At lower quality, the ratio-based
        # gate prevents synthesis on genuinely ambiguous retrievals.
        if top_score >= 3.0:
            dominant = (top_score - second_score) >= 0.5
        else:
            dominant = (top_score / second_score) >= _DOMINANCE_RATIO

        if not dominant:
            # Enrich top hits with substantive excerpts so the agent has
            # real material to ground in (not one-line summaries).
            await _enrich_gated_excerpts(hits, ctx)
            # Structured candidate set: a decision-shaped list with a
            # one-line justification per file. Beats the prior flat
            # ``fallback_targets`` list because the agent can pick ONE file
            # to Read first instead of skimming five.
            best_guesses = [
                {
                    "file": h.get("target_path"),
                    "why_relevant": _candidate_justification(h),
                    "score": round(h.get("score", 0.0), 3),
                    "domain_penalty": h.get("_domain_penalty"),
                }
                for h in hits[:_GATED_RETURN_HITS]
                if h.get("target_path")
            ]
            return {
                "answer": "",
                "citations": [],
                "confidence": "low",
                "retrieval_quality": "weak",
                "best_guesses": best_guesses,
                "next_action_hint": (
                    f"Read {best_guesses[0]['file']} first — it scored highest "
                    "but retrieval was ambiguous, so verify before answering."
                    if best_guesses
                    else "Fall back to search_codebase or Grep."
                ),
                "fallback_targets": fallback_targets,
                "retrieval": hits[:_GATED_RETURN_HITS],
                "note": (
                    "Multiple plausible candidates — synthesis skipped to "
                    "avoid anchoring on a wrong frame. Each best_guess entry "
                    "names why that file is in the running."
                ),
                "_meta": _build_meta(
                    timing_ms=(time.perf_counter() - t0) * 1000,
                    hint=_answer_hint("low", len(hits)),
                    repository=repository,
                ),
            }

    # Confidence is the only axis we gate on. We deliberately do NOT add a
    # second gate keyed on question shape (e.g. relational questions
    # containing connectives like "between", "and", "from"). Relational vs
    # non-relational is the wrong axis to gate on: the hard relational
    # failures already surface as low-dominance retrievals and are caught
    # by the gate above, while a shape-based gate over-fires on confidently
    # dominant relational questions and pushes cost back onto the agent's
    # own reasoning loop.

    # --- Synthesis (LLM) ---------------------------------------------------
    provider = _resolve_provider_for_answer(getattr(ctx, "path", None))
    if provider is None:
        # Retrieval-only mode (no provider). Return the hits so the agent can
        # at least skip the search_codebase step.
        return {
            "answer": "",
            "citations": [],
            "confidence": "low",
            "fallback_targets": fallback_targets,
            "retrieval": hits,
            "note": (
                "No LLM provider configured (set REPOWISE_PROVIDER + API key). "
                "Returning retrieval hits only — Read the listed files to answer."
            ),
            "_meta": _build_meta(
                timing_ms=(time.perf_counter() - t0) * 1000,
                hint=_answer_hint("low", len(hits)),
                repository=repository,
            ),
        }

    # Decision fusion (why-shaped questions only) + structured prelude. Both
    # layers are gated on signal: no ADRs for the top hits → no decisions
    # block, no symbols / commits / decisions → no prelude. Empty layers are
    # dropped before formatting, so the prompt never carries hollow scaffolding.
    top_paths = [h["target_path"] for h in hits if h.get("target_path")]
    decisions: list[dict] = []
    if _is_why_question(question) and top_paths:
        with contextlib.suppress(Exception):
            decisions = await _fetch_relevant_decisions(ctx, repo_id, top_paths)
    prelude = ""
    with contextlib.suppress(Exception):
        prelude = await _build_structured_prelude(hits, decisions, ctx, repo_id)

    user_prompt = _USER_TEMPLATE.format(
        question=question.strip(),
        n=len(hits),
        context=_build_context_block_v2(hits, prelude=prelude, decisions=decisions),
    )

    answer_text = ""
    try:
        response = await asyncio.wait_for(
            provider.generate(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                max_tokens=1024,
                temperature=0.2,
            ),
            timeout=30.0,
        )
        answer_text = (response.content or "").strip()
    except Exception as exc:
        _log.warning("get_answer LLM call failed: %s", exc)
        return {
            "answer": "",
            "citations": [],
            "confidence": "low",
            "fallback_targets": fallback_targets,
            "retrieval": hits,
            "note": f"LLM synthesis failed ({type(exc).__name__}). Read the listed files to answer.",
            "_meta": _build_meta(
                timing_ms=(time.perf_counter() - t0) * 1000,
                hint=_answer_hint("low", len(hits)),
                repository=repository,
            ),
        }

    citations = [
        h["target_path"] for h in hits if h["target_path"] and h["target_path"] in answer_text
    ]
    if not citations:
        # Fall back to top-2 retrieval paths so the agent always has something to verify.
        citations = fallback_targets[:2]

    # Compute confidence from the dominance ratio (top hit vs second hit).
    # The dominance ratio is a more reliable separator than absolute BM25
    # thresholds, which tend to label most retrievals "high" indiscriminately.
    if len(hits) >= 2:
        _top = hits[0].get("score", 0.0)
        _second = hits[1].get("score", 0.0) or 1e-9
        _ratio = _top / _second
    else:
        _ratio = float("inf") if hits else 0.0
    _top_score = hits[0].get("score", 0.0) if hits else 0.0
    if _ratio >= _DOMINANCE_RATIO and _top_score >= _HIGH_CONFIDENCE_SCORE_FLOOR:
        confidence = "high"
    elif _ratio >= _DOMINANCE_RATIO:
        # Dominant but weak — the right file relative to its siblings, but
        # the signal isn't strong enough to trust the synthesised answer
        # without verification. Downgrade so the consumer Reads the source.
        confidence = "medium"
    else:
        confidence = "medium"

    # Second gate: downgrade when the LLM's own answer admits insufficiency.
    # Retrieval dominance only tells us we indexed the right file; it does
    # not mean the synthesized text is usable. Shipping a hedged answer with
    # confidence="high" misleads the consumer AND drags the full retrieval
    # payload (~10k chars) through the conversation cache for no benefit.
    hedged = _answer_is_hedged(answer_text)
    if hedged:
        confidence = "low"

    # Third gate — identifier-citation gate: when the question explicitly
    # names identifiers (classes / methods / snake_case / CamelCase) and
    # NONE of the top retrieval hits contain any of those identifiers as a
    # hydrated symbol, retrieval may be pointing at plausible-but-wrong
    # files (same module family, similar vocabulary). Downgrade high→medium
    # so the consumer Reads the `fallback_targets`. Only applies when the
    # question actually names identifiers — mechanism-descriptive questions
    # (no symbol names) are unaffected.
    if confidence == "high" and question_ids:
        top_n = [h for h in hits[:_ENRICH_TOP_N_HITS] if h.get("symbols")]
        has_match = any(
            s.get("_matched") for h in top_n for s in (h.get("symbols") or [])
        )
        if not has_match:
            confidence = "medium"

    # retrieval_quality is a separate signal from confidence. Where confidence
    # says "how much should you trust the synthesised text", retrieval_quality
    # says "how good was the retrieval that fed it". The agent uses confidence
    # to decide whether to re-read; retrieval_quality to decide whether to
    # call search_codebase again with a refined query.
    if _top_score >= _HIGH_CONFIDENCE_SCORE_FLOOR and _ratio >= _DOMINANCE_RATIO:
        retrieval_quality = "high"
    elif _ratio >= _DOMINANCE_RATIO:
        retrieval_quality = "partial"
    else:
        retrieval_quality = "weak"

    if hedged:
        # Hedged answers: drop the retrieval payload. The consumer has been
        # told to read the source — the symbol-docstring blob that helped
        # synthesis doesn't help them, and keeping it in the response bloats
        # every follow-up turn's prompt cache.
        payload = {
            "_schema_version": _ANSWER_SCHEMA_VERSION,
            "answer": answer_text,
            "citations": citations,
            "confidence": "low",
            "retrieval_quality": retrieval_quality,
            "fallback_targets": fallback_targets[:3],
            "retrieval": [],
            "note": (
                "Synthesis hedged: the LLM could not ground the question in "
                "the indexed wiki. Read one of fallback_targets to answer."
            ),
        }
    else:
        payload = {
            "_schema_version": _ANSWER_SCHEMA_VERSION,
            "answer": answer_text,
            "citations": citations,
            "confidence": confidence,
            "retrieval_quality": retrieval_quality,
            "fallback_targets": fallback_targets,
            "retrieval": hits,
        }
        if confidence == "high":
            payload["note"] = (
                "High confidence: top retrieval result clearly dominates "
                f"(dominance ratio {_ratio:.2f}x, top score {_top_score:.2f}) "
                "AND the synthesised answer is direct (no hedging). Cite this "
                "answer; do not re-read the source unless a specific detail "
                "is missing."
            )

    # Persist to cache. Best-effort: cache failures must NEVER block the
    # response (we already have the answer in hand).
    if answer_text:
        with contextlib.suppress(Exception):
            async with get_session(ctx.session_factory) as session:
                row = AnswerCache(
                    repository_id=repo_id,
                    question_hash=qhash,
                    question=question.strip(),
                    payload_json=_json.dumps(payload),
                    provider_name=getattr(provider, "provider_name", "") or "",
                    model_name=getattr(provider, "model_name", "") or "",
                )
                session.add(row)
                await session.commit()

    payload["_meta"] = _build_meta(
        timing_ms=(time.perf_counter() - t0) * 1000,
        hint=_answer_hint(confidence, len(hits)),
        repository=repository,
    )
    return payload
