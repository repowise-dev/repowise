"""On-demand LLM enrichment: a deterministic plan -> named code + a diff.

This closes the loop from a structured ``RefactoringSuggestion`` (the split
groups, the clone occurrences, the cycle cut-edges) to the actual refactored
code a human or coding agent can apply. It is strictly opt-in and never runs in
the indexing hot path:

1. Gather the real source spans the plan references (the class body, every clone
   occurrence, the method to move, the files on each cut edge) straight off the
   working tree.
2. Build a behaviour-preservation prompt carrying the structured plan + that
   source + the graph/co-change context the deterministic layer already
   computed, and ask the configured provider for the refactored code and a
   unified diff.
3. For Extract Class, self-check the result with an LCOM4 before/after delta
   (re-walk the generated classes) so we can say whether the split actually
   improved cohesion. Other types skip validation gracefully.

Results are cached on disk by a content hash (plan + source + model), so the
same plan never pays for the same generation twice.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import structlog

from repowise.core.providers.llm.base import BaseProvider, CacheHint

log = structlog.get_logger(__name__)

# Bounds so a pathological plan can never balloon the prompt: per-span and
# total source-line caps, and a hard ceiling on how many spans we read.
_MAX_SPAN_LINES = 240
_MAX_SPANS = 12
# Cache lives next to the other repo-local refactoring artifacts.
_CACHE_SUBDIR = ("refactoring", "enrich")

# Extension -> tree-sitter language name (the names ``walk_file`` understands).
# Only the languages whose LCOM4 self-check is meaningful need to be here; an
# unknown extension simply skips validation.
_EXT_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".java": "java",
    ".go": "go",
    ".cs": "c_sharp",
    ".rb": "ruby",
    ".php": "php",
    ".kt": "kotlin",
    ".rs": "rust",
    ".scala": "scala",
    ".swift": "swift",
}


@dataclass
class SourceSpan:
    """A concrete slice of working-tree source the plan refers to."""

    file: str
    start_line: int
    end_line: int
    source: str


@dataclass
class EnrichmentResult:
    """The output of enriching one suggestion. Serialized to every surface."""

    refactoring_type: str
    file_path: str
    target_symbol: str
    suggestion_id: str | None
    # The model's full response (a short summary + the named code + the diff).
    content: str
    # The unified diff extracted from the response, when it emitted one.
    diff: str
    provider: str
    model: str
    cached: bool
    input_tokens: int
    output_tokens: int
    # LCOM4 before/after self-check (Extract Class only); ``{}`` when skipped.
    validation: dict[str, Any] = field(default_factory=dict)
    # The spans fed to the model, so a surface can show what was grounded on.
    spans: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Config gate + provider resolution (server / MCP surfaces)
# ---------------------------------------------------------------------------


def llm_enrichment_enabled(config: dict[str, Any]) -> bool:
    """Whether ``refactoring.llm.enabled`` is set in a loaded repo config.

    Off by default. The CLI's explicit ``--generate-code`` flag is its own
    opt-in and does not consult this; the server/MCP surfaces gate on it so a
    hosted deployment never exposes code-gen unless the repo enabled it.
    """
    refactoring = config.get("refactoring")
    if not isinstance(refactoring, dict):
        return False
    llm = refactoring.get("llm")
    if not isinstance(llm, dict):
        return False
    return bool(llm.get("enabled", False))


def build_enrichment_provider(
    repo_path: Path,
    *,
    provider_name: str | None = None,
    model: str | None = None,
) -> BaseProvider:
    """Resolve a provider for server/MCP enrichment from repo config + env.

    Mirrors the CLI resolver in spirit (config provider/model, key from env)
    but lives in core so the server and MCP layers don't depend on the CLI.
    Reads ``.repowise/config.yaml`` for provider/model and the per-repo
    ``.repowise/.env`` (without mutating ``os.environ``) plus the process env
    for the API key. Raises ``ValueError`` when no provider/key can be found.
    """
    import os

    from repowise.core.providers import get_provider
    from repowise.core.repo_config import load_repo_config, load_repo_env

    cfg = load_repo_config(repo_path)
    repo_env = load_repo_env(repo_path)

    def _env(name: str) -> str | None:
        return os.environ.get(name) or repo_env.get(name)

    name = provider_name or cfg.get("provider")
    chosen_model = model or cfg.get("model")

    # Map provider -> the env var carrying its key.
    key_vars = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "litellm": "LITELLM_API_KEY",
    }

    # Auto-detect from whichever key is present when the config names none.
    if name is None:
        for candidate, var in key_vars.items():
            if _env(var):
                name = candidate
                break
    if name is None:
        raise ValueError(
            "No LLM provider configured for refactoring enrichment. Set "
            "'provider' in .repowise/config.yaml or an API key env var."
        )

    kwargs: dict[str, Any] = {}
    if chosen_model:
        kwargs["model"] = chosen_model
    key_var = key_vars.get(name)
    if key_var and _env(key_var):
        kwargs["api_key"] = _env(key_var)
    if name == "gemini" and not kwargs.get("api_key") and _env("GOOGLE_API_KEY"):
        kwargs["api_key"] = _env("GOOGLE_API_KEY")

    return get_provider(name, **kwargs)


# ---------------------------------------------------------------------------
# Source gathering
# ---------------------------------------------------------------------------


def _read_span(repo_path: Path, file: str, start: int, end: int) -> SourceSpan | None:
    """Read a 1-indexed inclusive line range off the working tree, capped.

    Guards against reading outside the repo root and degrades to ``None`` on
    any read error (the caller simply omits the span).
    """
    abs_path = (repo_path / file).resolve()
    try:
        abs_path.relative_to(repo_path.resolve())
    except ValueError:
        log.warning("enrich_span_path_escape", file=file)
        return None
    try:
        text = abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    lines = text.splitlines()
    total = len(lines)
    if total == 0:
        return None
    s = max(1, min(start, total))
    e = max(s, min(end, total))
    if (e - s + 1) > _MAX_SPAN_LINES:
        e = s + _MAX_SPAN_LINES - 1
    return SourceSpan(file=file, start_line=s, end_line=e, source="\n".join(lines[s - 1 : e]))


def _span_requests(suggestion: Any) -> list[tuple[str, int, int]]:
    """Per-type (file, start, end) requests, derived from the plan.

    The target span (``file_path`` + ``line_start``/``line_end``) is the spine;
    each type adds the extra spans its plan references (clone occurrences, the
    move source/target files, the files on each cut edge). Bounded to
    ``_MAX_SPANS``; missing line info falls back to the file head.
    """
    plan = suggestion.plan or {}
    rtype = suggestion.refactoring_type
    requests: list[tuple[str, int, int]] = []

    def _add(file: str | None, start: Any, end: Any) -> None:
        if not file or len(requests) >= _MAX_SPANS:
            return
        try:
            s = int(start) if start is not None else 1
            e = int(end) if end is not None else s + _MAX_SPAN_LINES - 1
        except (TypeError, ValueError):
            s, e = 1, _MAX_SPAN_LINES
        requests.append((file, s, e))

    # The headline target span (the class / method / site).
    if suggestion.file_path:
        _add(suggestion.file_path, suggestion.line_start, suggestion.line_end)

    if rtype == "extract_helper":
        for occ in plan.get("occurrences", []) or []:
            if isinstance(occ, dict):
                _add(occ.get("file"), occ.get("line_start"), occ.get("line_end"))
    elif rtype == "move_method":
        to_file = plan.get("to_file")
        # The destination class file gives the model the landing context; read
        # its head (we don't have an exact span for it).
        _add(to_file, 1, _MAX_SPAN_LINES)
    elif rtype == "break_cycle":
        for edge in plan.get("cut_edges", []) or []:
            if isinstance(edge, dict):
                # The "from" file holds the import to invert/abstract.
                _add(edge.get("from"), 1, _MAX_SPAN_LINES)

    # De-dup identical (file, start, end) requests while preserving order.
    seen: set[tuple[str, int, int]] = set()
    unique: list[tuple[str, int, int]] = []
    for req in requests:
        if req not in seen:
            seen.add(req)
            unique.append(req)
    return unique


def _gather_spans(suggestion: Any, repo_path: Path) -> list[SourceSpan]:
    spans: list[SourceSpan] = []
    for file, start, end in _span_requests(suggestion):
        span = _read_span(repo_path, file, start, end)
        if span is not None:
            spans.append(span)
    return spans


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a senior software engineer performing a single, well-scoped refactoring.

You are given a STRUCTURED PLAN produced by deterministic static analysis and \
the exact SOURCE SPANS the plan refers to. Carry out exactly that plan and \
nothing more.

Hard rules:
- Preserve behaviour exactly. No functional changes, no API changes beyond what \
the plan requires, no opportunistic cleanup.
- Use the real names from the source. Invent clear names only for new things the \
plan leaves unnamed (e.g. extracted classes/helpers).
- Do not hallucinate code you were not shown. If a needed detail is missing, \
state the assumption briefly rather than guessing silently.

Respond in this order:
1. A 1-2 sentence summary of the change.
2. The new or changed code, each block in a fenced code block tagged with the \
language.
3. A single unified diff in one ```diff fenced block (git-style: `--- a/path`, \
`+++ b/path`, `@@` hunks) covering every file you changed.
"""

_TYPE_INSTRUCTIONS: dict[str, str] = {
    "extract_class": (
        "Refactoring: EXTRACT CLASS. Split the class into the cohesive groups in "
        "the plan — each group becomes a new class owning its listed methods and "
        "fields. Keep the original class as a thin coordinator that delegates, so "
        "all existing call sites keep working."
    ),
    "extract_helper": (
        "Refactoring: EXTRACT HELPER. The occurrences are duplicates of the same "
        "logic. Extract one shared helper (place it at the suggested site) and "
        "replace every occurrence with a call to it."
    ),
    "move_method": (
        "Refactoring: MOVE METHOD. Move the method from its current class to the "
        "target class it is more cohesive with, and update the call sites. Leave a "
        "thin delegating wrapper only if removing the method outright would break "
        "callers you cannot see."
    ),
    "break_cycle": (
        "Refactoring: BREAK IMPORT CYCLE. Remove the cyclic dependency by "
        "inverting or abstracting the import on each cut edge (dependency "
        "inversion, a shared interface/protocol module, or a local import as a "
        "last resort). Do not merge the files."
    ),
}


def _build_user_prompt(suggestion: Any, spans: list[SourceSpan]) -> str:
    """Render the plan + evidence + blast radius + source into a user prompt."""
    rtype = suggestion.refactoring_type
    parts: list[str] = []
    parts.append(_TYPE_INSTRUCTIONS.get(rtype, f"Refactoring: {rtype}."))
    parts.append(f"\nTarget: {suggestion.target_symbol} ({suggestion.file_path})")

    parts.append("\n## Structured plan\n")
    parts.append("```json")
    parts.append(
        json.dumps(
            {
                "type": rtype,
                "plan": suggestion.plan or {},
                "evidence": suggestion.evidence or {},
                "blast_radius": suggestion.blast_radius or {},
            },
            indent=2,
            sort_keys=True,
        )
    )
    parts.append("```")

    parts.append("\n## Source spans\n")
    if not spans:
        parts.append("_(no source spans were resolvable from the working tree)_")
    for span in spans:
        parts.append(f"### {span.file}:{span.start_line}-{span.end_line}")
        parts.append("```")
        parts.append(span.source)
        parts.append("```")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Response parsing + caching
# ---------------------------------------------------------------------------

_DIFF_FENCE = re.compile(r"```diff\s*\n(.*?)```", re.DOTALL)
_CODE_FENCE = re.compile(r"```([A-Za-z0-9_+-]*)\s*\n(.*?)```", re.DOTALL)


def _extract_diff(content: str) -> str:
    """Return the first ```diff fenced block, stripped, or ``""``."""
    match = _DIFF_FENCE.search(content)
    return match.group(1).strip() if match else ""


def _extract_code_blocks(content: str, language: str | None) -> str:
    """Concatenate non-diff fenced code blocks (best-effort) for the self-check.

    Prefers blocks tagged with the target language but accepts untagged blocks;
    always skips ``diff`` blocks (those are not parseable source).
    """
    wanted = {language} if language else set()
    # Accept common aliases so a "py"-tagged block still counts as python, etc.
    aliases = {"py": "python", "ts": "typescript", "tsx": "typescript"}
    blocks: list[str] = []
    for tag, body in _CODE_FENCE.findall(content):
        norm = aliases.get(tag.lower(), tag.lower())
        if norm == "diff":
            continue
        if wanted and norm and norm not in wanted:
            continue
        blocks.append(body.strip())
    return "\n\n".join(blocks)


def _language_for(file_path: str) -> str | None:
    return _EXT_LANGUAGE.get(Path(file_path).suffix.lower())


def _cache_key(suggestion: Any, spans: list[SourceSpan], model: str) -> str:
    payload = json.dumps(
        {
            "type": suggestion.refactoring_type,
            "target": suggestion.target_symbol,
            "file": suggestion.file_path,
            "plan": suggestion.plan or {},
            "spans": [
                [s.file, s.start_line, s.end_line, hashlib.sha256(s.source.encode()).hexdigest()]
                for s in spans
            ],
            "model": model,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _cache_dir(repo_path: Path) -> Path:
    return repo_path.joinpath(".repowise", *_CACHE_SUBDIR)


def _read_cache(cache_dir: Path, key: str) -> EnrichmentResult | None:
    path = cache_dir / f"{key}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        result = EnrichmentResult(**data)
    except TypeError:
        return None
    result.cached = True
    return result


def _write_cache(cache_dir: Path, key: str, result: EnrichmentResult) -> None:
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        # Persist with cached=False; the read path flips it to True on a hit.
        payload = result.to_dict()
        payload["cached"] = False
        (cache_dir / f"{key}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as exc:
        log.debug("enrich_cache_write_failed", error=str(exc))


# ---------------------------------------------------------------------------
# Validation — LCOM4 before/after self-check (Extract Class)
# ---------------------------------------------------------------------------


def _validate_extract_class(
    content: str, file_path: str, evidence: dict[str, Any]
) -> dict[str, Any]:
    """Re-walk the generated classes and compare LCOM4 against the original.

    A real Extract Class lowers the worst per-class LCOM4 (each split class
    should be cohesive, LCOM4 ~= 1) and yields >= 2 classes. Best-effort: any
    parse/walk failure degrades to ``status="skipped"`` rather than raising.
    """
    language = _language_for(file_path)
    if language is None:
        return {"status": "skipped", "reason": f"no walker for {Path(file_path).suffix}"}

    code = _extract_code_blocks(content, language)
    if not code.strip():
        return {"status": "skipped", "reason": "no parseable code blocks in response"}

    try:
        from repowise.core.analysis.health.complexity import walk_file

        fc = walk_file(f"generated{Path(file_path).suffix}", language, code.encode())
    except Exception as exc:  # walker is best-effort here
        return {"status": "skipped", "reason": f"walk failed: {exc}"}

    classes = list(getattr(fc, "classes", []) or [])
    if not classes:
        return {"status": "skipped", "reason": "no classes parsed from response"}

    before = evidence.get("lcom4") if isinstance(evidence, dict) else None
    after = [{"name": c.name, "lcom4": c.lcom4} for c in classes]
    after_max = max((c.lcom4 for c in classes), default=None)
    improved = (
        isinstance(before, int)
        and after_max is not None
        and after_max < before
        and len(classes) >= 2
    )
    return {
        "status": "checked",
        "before_lcom4": before,
        "after_classes": after,
        "after_max_lcom4": after_max,
        "class_count": len(classes),
        "improved": bool(improved),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def enrich_suggestion(
    suggestion: Any,
    *,
    provider: BaseProvider,
    repo_path: Path,
    cache_dir: Path | None = None,
    use_cache: bool = True,
    validate: bool = True,
    max_tokens: int = 8000,
) -> EnrichmentResult:
    """Generate refactored code + a diff for one deterministic suggestion.

    On-demand only. Gathers the plan's source spans off *repo_path*, prompts
    *provider*, parses the diff, runs the Extract Class self-check, and caches
    the result by a content hash so an unchanged plan never regenerates.
    """
    spans = _gather_spans(suggestion, repo_path)
    model = getattr(provider, "model_name", "") or ""
    provider_name = getattr(provider, "provider_name", "") or ""
    suggestion_id = getattr(suggestion, "id", None)

    cdir = cache_dir or _cache_dir(repo_path)
    key = _cache_key(suggestion, spans, model)
    if use_cache:
        cached = _read_cache(cdir, key)
        if cached is not None:
            return cached

    system = _SYSTEM_PROMPT + "\n" + _TYPE_INSTRUCTIONS.get(suggestion.refactoring_type, "")
    user = _build_user_prompt(suggestion, spans)
    response = await provider.generate(
        system,
        user,
        max_tokens=max_tokens,
        temperature=0.1,
        cache_hints=(CacheHint(segment="system"),),
    )

    content = response.content or ""
    diff = _extract_diff(content)
    validation: dict[str, Any] = {}
    if validate and suggestion.refactoring_type == "extract_class":
        validation = _validate_extract_class(
            content, suggestion.file_path, suggestion.evidence or {}
        )

    result = EnrichmentResult(
        refactoring_type=suggestion.refactoring_type,
        file_path=suggestion.file_path,
        target_symbol=suggestion.target_symbol,
        suggestion_id=suggestion_id,
        content=content,
        diff=diff,
        provider=provider_name,
        model=model,
        cached=False,
        input_tokens=getattr(response, "input_tokens", 0) or 0,
        output_tokens=getattr(response, "output_tokens", 0) or 0,
        validation=validation,
        spans=[{"file": s.file, "line_start": s.start_line, "line_end": s.end_line} for s in spans],
    )
    if use_cache:
        _write_cache(cdir, key, result)
    return result
