"""LLM-output validation for generated documentation pages.

Rejects unusable provider responses before persistence and cross-checks
backtick-quoted names against the source ``ParsedFile`` so invented symbols
can be surfaced.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from repowise.core.generation.agent_digest import split_questions
from repowise.core.generation.mermaid_safety import invalid_mermaid_blocks, sanitize_mermaid
from repowise.core.generation.prose import prose_text, prose_word_count
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


class InvalidMermaidError(InvalidGeneratedContentError):
    """A mermaid block in the response would not render.

    Retried like any other rejection. When the retry breaks a diagram too, the
    caller keeps the page and strips the failing blocks: a page without its
    picture is still worth reading, an error box where the picture was is not.
    """


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
    """One phrasing that must never reach a reader, why, and what to write instead.

    ``remedy`` goes into the corrective re-ask only. It says what the page
    should do in place of the rejected sentence without quoting it, because a
    retry told only which rule it broke tends to rephrase the same sentence
    into the same rule.
    """

    name: str
    pattern: re.Pattern[str]
    explanation: str
    remedy: str

    @property
    def retry_hint(self) -> str:
        """The complaint and its remedy, without the phrase that broke the rule."""
        return f"{self.name}: {self.explanation}. {self.remedy}"


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
        remedy="End the page on its last fact about the code and offer nothing further.",
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
        remedy="Write in the third person about the code, with no narrator.",
    ),
    ArtifactRule(
        name="supplied_context",
        # The model narrating the shape of its own prompt.  The reader has no
        # idea what was "provided" and cannot go and look at it.
        #
        # Only nouns that name prompt material.  "the supplied input", "the
        # given data", "the provided list" and "the given files" describe a
        # function's arguments in plain English, and rejecting them cost whole
        # pages.  "context", "code", "files", "snippet" and "documentation"
        # count only after a reasoning frame ("based on the provided code",
        # "not shown in the given context"): "runs in the given context" is a
        # Go ``context.Context`` argument and "executes the provided code" is
        # a sandbox's.
        pattern=re.compile(
            r"\bthe (?:provided|supplied|given|attached|above) "
            r"(?:information|material|excerpts)\b"
            r"|\b(?:based on|according to|judging (?:by|from)) the "
            r"(?:provided|supplied|given|attached|above) "
            r"(?:context|code|source|files?|details|snippets?|documentation)\b"
            r"|\b(?:not|never) (?:shown|visible|included|present|defined|described|specified) "
            r"(?:in|within) the (?:provided|supplied|given|attached|above)\b"
            r"|\b(?:provided|supplied|shared|pasted|attached) above\b"
            r"|\byou (?:provided|supplied|gave|listed|shared|pasted|attached)\b"
            r"|\byou were (?:given|shown|provided|supplied)\b"
            # What the page was written from, named as a source that falls
            # short ("the available record names no functions", "the record
            # does not provide excerpts"). "The record does not include" and
            # "the facts do not match" are left alone: they describe data.
            r"|\bthe (?:available|provided|supplied|given) (?:record|facts|material)\b"
            r"|\bthe record (?:does|did) not "
            r"(?:provide|show|name|say|state|mention|give|supply|reveal)\b",
            re.IGNORECASE,
        ),
        explanation="refers to the prompt, which the reader cannot see",
        remedy=(
            "State what the code does. Where a detail is unknown, either leave it "
            "out or say it about the code (for example: the caller of a function is "
            "not traced here), never about what this request did or did not include."
        ),
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
    the log, the second names the rule and its remedy but not the phrase, so a
    re-ask built from it does not repeat the words that failed.
    """
    prose = prose_text(content)
    for rule in GENERATION_ARTIFACT_RULES:
        match = rule.pattern.search(prose)
        if match is not None:
            _artifact_check_counts["rejected"] += 1
            _artifact_check_counts[f"rejected:{rule.name}"] += 1
            detail = f"{rule.name}: {rule.explanation} ({match.group(0).strip()!r})"
            return detail, rule.retry_hint
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


# ---------------------------------------------------------------------------
# Page shape
#
# The rules a page type's template states and the model drifts from: the house
# vocabulary it bans, the table and length budget. A page that breaks one is
# still a page a reader can use, so these are retried once and then kept as
# written (``PageShapeError`` is soft), unlike the checks above.
# ---------------------------------------------------------------------------


class PageShapeError(InvalidGeneratedContentError):
    """The response breaks its template's shape rules; retried, then kept."""


#: Words the module page template bans: the abstraction the in/out/why opening
#: used to produce ("This subsystem turns X into Y") and talk about the wiki's
#: own structure. Measured on this repository's 101 module pages before the
#: template changed: 403 hits on 91 pages.
_HOUSE_JARGON = re.compile(
    r"\bsubsystems?\b|\bboundar(?:y|ies)\b|\b(?:child|concept) pages?\b"
    r"|\btakes in\b|\bhands? (?:it |them )?on\b|\bturns\b(?! out)[^.\n]{1,80}?\binto\b",
    re.IGNORECASE,
)
#: Two stray uses are tolerated: "word boundary" on a regex page and "error
#: boundary" on a React one are the code's own words.
_MAX_JARGON_HITS = 2
#: A map page tabulates its parts, one row each, and a leaf may put a few sets
#: of parallel options in tables; past these the page has become a reference.
_MAX_TABLES = 3
_MAX_TABLE_ROWS = 30
#: The template asks for at most 1100 words before the questions on the largest
#: module. This is the runaway guard above that, not the budget: a retry spent
#: on length alone is money spent on a page a reader could already use.
_MAX_PROSE_WORDS = 1400
_TABLE_DIVIDER = re.compile(r"^\s*\|[-:| ]+\|\s*$")


def _table_row_counts(prose: str) -> list[int]:
    """Body rows of each markdown table in *prose*, in order."""
    counts: list[int] = []
    run = 0
    for line in [*prose.splitlines(), ""]:
        if line.lstrip().startswith("|"):
            run += 0 if _TABLE_DIVIDER.match(line) else 1
        elif run:
            counts.append(run - 1)  # the header row is not a body row
            run = 0
    return counts


def _module_page_shape(content: str) -> str | None:
    """What breaks the module page template's own rules, or ``None``."""
    body, _questions = split_questions(content)
    prose = prose_text(body)
    if len(_HOUSE_JARGON.findall(prose)) > _MAX_JARGON_HITS:
        # The banned words are not repeated here: the re-ask quotes this text,
        # and naming them again is how a retry plants them back.
        return (
            "uses the abstract vocabulary the opening rule bans, or talks about pages "
            "of the wiki, instead of naming what the code does"
        )
    tables = _table_row_counts(prose)
    if len(tables) > _MAX_TABLES or any(rows > _MAX_TABLE_ROWS for rows in tables):
        return (
            f"has {len(tables)} tables (largest {max(tables)} rows); at most "
            f"{_MAX_TABLES} tables of at most {_MAX_TABLE_ROWS} rows are allowed"
        )
    words = prose_word_count(body)
    if words > _MAX_PROSE_WORDS:
        return f"runs to {words} words of prose; it must stay within the length it was asked for"
    return None


_SHAPE_CHECKS = {"module_page": _module_page_shape}


def validate_page_shape(page_type: str | None, content: str) -> None:
    """Raise :class:`PageShapeError` when *content* breaks *page_type*'s shape rules."""
    check = _SHAPE_CHECKS.get(page_type or "")
    detail = check(content) if check is not None else None
    if detail is not None:
        _artifact_check_counts["shape_rejected"] += 1
        raise PageShapeError(f"the page {detail}")


def validate_generated_response(
    response: GeneratedResponse, page_type: str | None = None
) -> None:
    """Reject fresh provider output that is unsafe to persist as a wiki page.

    With *page_type*, the page is also held to its template's shape rules
    (:func:`validate_page_shape`), checked last so a hard failure is reported
    ahead of a soft one.
    """
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

    # Checked on the repaired form, which is the form that gets stored.
    broken = invalid_mermaid_blocks(sanitize_mermaid(response.content))
    if broken:
        raise InvalidMermaidError(f"a mermaid diagram will not render: {broken[0]}")
    validate_page_shape(page_type, response.content)


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
