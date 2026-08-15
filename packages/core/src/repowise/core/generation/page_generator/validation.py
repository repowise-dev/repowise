"""LLM-output validation for generated documentation pages.

Rejects unusable provider responses before persistence and cross-checks
backtick-quoted names against the source ``ParsedFile`` so invented symbols
can be surfaced.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from repowise.core.generation.prose import prose_text
from repowise.core.ingestion.models import ParsedFile
from repowise.core.providers.llm.base import GeneratedResponse

_REPETITION_SHINGLE_WORDS = 12
_REPETITION_MIN_WORDS = 240
_REPETITION_MIN_OCCURRENCES = 20
_REPETITION_MAX_UNIQUE_RATIO = 0.25
_WORD_RE = re.compile(r"\w+", re.UNICODE)


class InvalidGeneratedContentError(ValueError):
    """Raised when a fresh LLM response cannot be persisted as documentation.

    ``retry_hint`` is the same complaint with any quoted offending phrase
    removed, for the corrective re-ask. The message itself keeps the quote
    because a log that does not say which words failed cannot be acted on, but
    feeding those words back to the model re-plants the vocabulary the retry is
    trying to get rid of. Defaults to the message when there is nothing to
    strip.

    ``retryable`` says whether asking again can plausibly help. It is False for
    a response that stopped at the token limit: the second call carries the
    same ``max_tokens``, so it truncates in the same place and the only certain
    result is twice the spend. Everything else is the model writing badly when
    told what "badly" means, which is what the re-ask supplies.
    """

    def __init__(
        self,
        message: str,
        *,
        retry_hint: str | None = None,
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.retry_hint = retry_hint if retry_hint is not None else message
        self.retryable = retryable


# ---------------------------------------------------------------------------
# Generation artifacts
#
# Phrasings in which the model addresses whoever prompted it instead of the
# reader of the page.  Each rule is checked against the page's prose only —
# fenced blocks and inline code are stripped first — because quoted source
# legitimately contains any word at all.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArtifactRule:
    """One phrasing that must never reach a reader, and why."""

    name: str
    pattern: re.Pattern[str]
    explanation: str


GENERATION_ARTIFACT_RULES: tuple[ArtifactRule, ...] = (
    ArtifactRule(
        name="trailing_offer",
        # An offer of further work, which only makes sense to whoever is
        # holding the conversation.  The offer verb is required: "let me know"
        # alone would be a bare idiom, and every phrasing here needs the model
        # to be proposing that *it* do something next.
        pattern=re.compile(
            r"\b(?:"
            r"I(?:'d| would)? (?:can|could|am happy to|would be happy to)"
            r"(?: also)? (?:produce|generate|provide|create|write|add|draw|"
            r"expand|prepare|put together|do)"
            r"|let me know if"
            r"|would you like me to"
            r"|do you want me to"
            r"|if you(?:'d| would)? (?:want|like), I"
            r"|just say the word"
            r")\b",
            re.IGNORECASE,
        ),
        explanation="offers the reader further work, which no page can deliver",
    ),
    ArtifactRule(
        name="first_person",
        # First-person singular in the *author's* voice.  Three deliberate
        # narrowings, each measured against this repo's own documentation:
        #
        # * A bare ``\bI\b`` is unusable.  It matches the ``I`` of ``I/O`` on
        #   every performance page, and it matches the reader-voice questions
        #   documentation is written in — "How do I update a workspace?",
        #   "should I worry about this file?".  So ``I`` must be followed by
        #   a verb that only the writer of the page would use of themselves.
        # * The verb list excludes the ones that read naturally after a
        #   question word ("how do I read", "where do I find", "how do I
        #   choose"), keeping only their past tense, which cannot.
        # * "we" is left alone entirely: plenty of projects write house
        #   documentation in the first-person plural on purpose.
        pattern=re.compile(
            r"\bI(?:'m|'ll|'ve|'d)\b"
            r"|\bI (?:do|did|have|had|am|was|would|could) not\b"
            r"|\bI (?:can|can't|cannot|could|couldn't|will|would|should|must|am|was|"
            r"haven't|hadn't|didn't|don't|note|noted|notice|noticed|assume|assumed|"
            r"inferred|chose|omitted|recommend|suggest|think|believe|focused|"
            r"included|excluded|generated|wrote|found)\b"
            r"|\bmyself\b"
        ),
        explanation="speaks as the generator rather than describing the code",
    ),
    ArtifactRule(
        name="supplied_context",
        # The model narrating the shape of its own prompt.  The reader has no
        # idea what was "provided" and cannot go and look at it.
        pattern=re.compile(
            r"\bthe (?:provided|supplied|given|attached|above) "
            r"(?:information|context|data|input|material|details|excerpt|"
            r"snippet|list|files?|documents?)\b"
            r"|\byou (?:provided|supplied|gave|listed)\b",
            re.IGNORECASE,
        ),
        explanation="refers to the prompt, which the reader cannot see",
    ),
)

# How many responses each rule has seen and rejected, for the whole process.
# A rule that matches nothing is indistinguishable from a rule that never ran
# unless the run count is recorded, and a check nobody can see the denominator
# for reads as a pass whether or not it happened.
_artifact_check_counts: Counter[str] = Counter()


def artifact_check_counts() -> dict[str, int]:
    """Snapshot of the artifact-check tallies since the last reset.

    ``responses_checked`` is the denominator: zero means the checks never ran,
    which is not the same fact as ``rejected`` being zero.
    """
    return dict(_artifact_check_counts)


def reset_artifact_check_counts() -> None:
    """Clear the tallies.  Used between runs and between tests."""
    _artifact_check_counts.clear()


def _artifact_detail(content: str) -> tuple[str, str] | None:
    """Describe the first generation artifact in ``content``, if any.

    Returns ``(detail, retry_hint)``: the first quotes the offending phrase for
    the log, the second names only the rule so a re-ask built from it does not
    repeat the words that failed.
    """
    prose = prose_text(content)
    for rule in GENERATION_ARTIFACT_RULES:
        match = rule.pattern.search(prose)
        if match is not None:
            _artifact_check_counts["rejected"] += 1
            _artifact_check_counts[f"rejected:{rule.name}"] += 1
            detail = f"{rule.name}: {rule.explanation} ({match.group(0).strip()!r})"
            return detail, f"{rule.name}: {rule.explanation}"
    return None


def _pathological_repetition_detail(content: str) -> str | None:
    """Describe gross repetition, using deliberately conservative thresholds.

    Twelve-word shingles tolerate ordinary repeated headings, table columns,
    API-list phrasing, and identifiers because the output must also be long,
    repeat one exact shingle at least twenty times, and have fewer than one
    unique shingle per four windows.
    """
    words = [match.group(0).casefold() for match in _WORD_RE.finditer(content)]
    if len(words) < _REPETITION_MIN_WORDS:
        return None

    shingle_count = len(words) - _REPETITION_SHINGLE_WORDS + 1
    if shingle_count <= 0:
        return None
    shingles = Counter(
        tuple(words[index : index + _REPETITION_SHINGLE_WORDS]) for index in range(shingle_count)
    )
    maximum_occurrences = max(shingles.values(), default=0)
    unique_ratio = len(shingles) / shingle_count
    if (
        maximum_occurrences >= _REPETITION_MIN_OCCURRENCES
        and unique_ratio <= _REPETITION_MAX_UNIQUE_RATIO
    ):
        return (
            f"12-word shingle repeated {maximum_occurrences} times "
            f"(unique ratio {unique_ratio:.3f})"
        )
    return None


def validate_generated_response(response: GeneratedResponse) -> None:
    """Reject fresh provider output that is unsafe to persist as a wiki page."""
    _artifact_check_counts["responses_checked"] += 1
    if response.stop_reason == "max_tokens":
        detail = (
            f" (provider reason: {response.provider_stop_reason})"
            if response.provider_stop_reason
            else ""
        )
        raise InvalidGeneratedContentError(
            f"generation reached a token limit before the documentation was complete{detail}",
            retryable=False,
        )
    if not response.content.strip():
        raise InvalidGeneratedContentError("provider returned empty documentation")

    repetition_detail = _pathological_repetition_detail(response.content)
    if repetition_detail is not None:
        raise InvalidGeneratedContentError(
            f"provider returned pathologically repetitive documentation: {repetition_detail}"
        )

    artifact = _artifact_detail(response.content)
    if artifact is not None:
        detail, retry_hint = artifact
        preamble = "provider returned text addressed to the prompter, not the reader — "
        raise InvalidGeneratedContentError(
            f"{preamble}{detail}", retry_hint=f"{preamble}{retry_hint}"
        )


# Common words that appear in backticks but are not code symbols.
_BACKTICK_SKIP = frozenset(
    {
        # Python builtins & keywords
        "True",
        "False",
        "None",
        "self",
        "cls",
        "super",
        "str",
        "int",
        "float",
        "bool",
        "list",
        "dict",
        "set",
        "tuple",
        "bytes",
        "object",
        "type",
        "Any",
        "Optional",
        "Union",
        "async",
        "await",
        "return",
        "yield",
        "import",
        "from",
        "class",
        "def",
        "if",
        "else",
        "for",
        "while",
        "try",
        "except",
        "raise",
        "with",
        "pass",
        "break",
        "continue",
        "lambda",
        "in",
        "not",
        "and",
        "or",
        "is",
        "del",
        "assert",
        "finally",
        "elif",
        "as",
        "global",
        "nonlocal",
        # JS/TS keywords
        "null",
        "undefined",
        "this",
        "const",
        "let",
        "var",
        "function",
        "export",
        "default",
        "extends",
        "implements",
        "interface",
        "enum",
        "new",
        "typeof",
        "instanceof",
        "void",
        "never",
        "string",
        "number",
        "boolean",
        "symbol",
        "bigint",
        "unknown",
        "readonly",
        "abstract",
        "static",
        "private",
        "protected",
        "public",
        "require",
        "module",
        "exports",
        "Promise",
        "Map",
        "Set",
        "Array",
        "Object",
        "Error",
        "Date",
        "RegExp",
        "JSON",
        "Math",
        "console",
        # Common tool/ecosystem names
        "pip",
        "npm",
        "npx",
        "yarn",
        "pnpm",
        "go",
        "rust",
        "python",
        "node",
        "cargo",
        "uv",
        "git",
        "docker",
        "make",
        # Common framework/lib names the LLM mentions in prose
        "FastAPI",
        "React",
        "Next",
        "Express",
        "Django",
        "Flask",
        "SQLAlchemy",
        "Pydantic",
        "Click",
        "Typer",
        "pytest",
        "asyncio",
        "pathlib",
        "dataclass",
        "dataclasses",
    }
)

# Regex: single-backtick references that look like identifiers.
_BACKTICK_REF_RE = re.compile(r"(?<!`)` *([A-Za-z_]\w*(?:\.\w+)*) *`(?!`)")

# Patterns that indicate the backtick content is a path, command, or
# value rather than a symbol reference — these should never be flagged.
_PATH_OR_CMD_RE = re.compile(
    r"[/\\]"  # contains path separator
    r"|\.(?:py|ts|js|json|yaml|yml|toml|md|sh|sql|css|html)$"  # file extension
    r"|^[a-z][\w-]*$"  # all-lowercase with hyphens = CLI command/flag
)


def _validate_symbol_references(
    content: str,
    parsed: ParsedFile,
) -> list[str]:
    """Cross-check backtick-quoted names in LLM output against actual symbols.

    Returns a list of warning strings for references that don't match any
    known symbol, export, or import in the ParsedFile. Designed to have low
    false-positive rates — only flags references that look like symbol names
    but can't be found anywhere in the file's AST, imports, or source text.
    """
    refs = set(_BACKTICK_REF_RE.findall(content))
    if not refs:
        return []

    # Build the known-names set from AST data
    known: set[str] = set()
    for s in parsed.symbols:
        known.add(s.name)
        known.add(s.qualified_name)
        # Decorator names are valid references (e.g. @app.command("init"))
        for dec in s.decorators:
            # Extract the decorator function name: "@app.command" → "command"
            dec_name = dec.lstrip("@").split("(")[0]
            known.add(dec_name)
            known.add(dec_name.split(".")[-1])
    known.update(parsed.exports)
    for imp in parsed.imports:
        if imp.module_path:
            # Add both the final component and intermediate segments
            parts = imp.module_path.split(".")
            known.update(parts)
        known.update(imp.imported_names)
        # Named bindings from import resolution
        for binding in getattr(imp, "bindings", []):
            known.add(binding.local_name)
            if binding.exported_name:
                known.add(binding.exported_name)

    # Also add all string literals from the source that look like identifiers
    # (catches Click command names, decorator arguments, dict keys, etc.)
    # The source is in the context, but we only have the parsed file here.
    # Use docstring and symbol names as a cheap approximation.
    if hasattr(parsed, "file_info") and hasattr(parsed.file_info, "path") and parsed.docstring:
        known.update(w for w in parsed.docstring.split() if w.isidentifier())

    warnings: list[str] = []
    for ref in refs:
        if ref in _BACKTICK_SKIP:
            continue
        # Skip short refs (1-2 chars are usually variables like `x`, `i`, `db`)
        if len(ref) <= 2:
            continue
        # Skip anything that looks like a path, file, or CLI command
        if _PATH_OR_CMD_RE.search(ref):
            continue
        # Skip all-uppercase (likely constants from other files: `MAX_RETRIES`)
        if ref.isupper():
            continue
        # Skip dotted refs entirely: they are member/attribute accesses
        # (`GeneratedPage.updated_at`, `config.coverage_pct`) or qualified
        # module paths. Dataclass fields, ORM columns, and cross-file
        # attribute chains are not AST symbols, so they cannot be verified
        # here and were the dominant false-positive source in dogfooding.
        # Whole-name hallucinations — the high-signal case — are
        # single-segment and still flagged below.
        if "." in ref:
            continue
        # Check against known names
        if ref in known:
            continue
        # Skip if the ref is a substring of any known symbol (covers partial
        # references like `parse` when `parse_file` exists)
        if any(ref in k for k in known if len(k) > len(ref)):
            continue
        warnings.append(ref)
    return warnings
