"""Renderers that build a page from structure instead of prompting a model.

Two different jobs share this machinery, and telling them apart matters:

**Sole renderers.** ``file_page``, ``symbol_spotlight``, ``api_contract``,
``infra_page`` and ``scc_page`` state facts a parser knows
exactly: symbols, signatures, imports, dependents, cycle membership, git
history. A model adds nothing to that and introduces staleness, so these have
one renderer and no model path at all. Their templates sit at
``templates/<name>.j2``.

**Keyless stubs.** ``module_page``, ``repo_overview``, ``architecture_diagram``
and ``onboarding`` exist to synthesise, which is exactly what a template
cannot do. They keep a model path; what lives here is the honest thin version
a user without an API key gets, which the same page upgrades away from once a
key is present. Their templates sit at ``templates/stub/<name>.j2``.

Everything either renderer emits is derived from the parsed AST, the import
graph, git history or the knowledge graph, so it is factual by construction
and needs no hallucination check.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import structlog

from ..models import (
    GENERATION_LEVELS,
    TEMPLATE_PAGE_CONFIDENCE,
    GeneratedPage,
    compute_page_id,
    compute_source_hash,
)
from .helpers import _extract_summary, _now_iso

log = structlog.get_logger(__name__)

# Bumped by hand when a change to the structural renderers improves their
# output without changing any template's bytes: new context fields, a changed
# helper, a reordered section. Template edits are picked up automatically
# (their source is hashed), so this is only for the cases hashing cannot see.
STRUCTURAL_GENERATION_VERSION = "2"

# Keyless stub templates live one directory down so their filenames can match
# the prompt templates they stand in for.
_STUB_PREFIX = "stub"

# Cap for free text folded into a list item or table cell. Long enough for a
# real summary sentence, short enough that a bullet stays a bullet.
_ONELINE_LIMIT = 200


def oneline(value: object, limit: int = _ONELINE_LIMIT) -> str:
    """Flatten free text so it can sit inside a markdown list item or cell.

    Deterministic templates interpolate text the pipeline produced elsewhere:
    docstrings, page summaries, decision rationales. That text is routinely
    multi-paragraph, and a raw newline inside a bullet ends the list, dumping
    the remainder as body text and restarting the numbering after it. The LLM
    templates never had this problem because their output was a prompt, not
    page content.
    """
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


# reStructuredText roles (:meth:`x`, :class:`~pkg.X`) and the double-backtick
# literal. Our docstrings are predominantly Sphinx flavoured, and markdown
# renders none of it: ``:meth:`foo``` shows the role name as body text and the
# double backticks come out as a stray empty code span.
# Allow-listed rather than ``:[a-z:]+:`` so ordinary prose is left alone. A
# permissive pattern eats any "word:word:" that happens to precede a backtick,
# which turns "O(n:m:`k`)" into "O(n`k`)".
_REST_ROLE_RE = re.compile(
    r":(?:py:)?(?:meth|class|func|attr|mod|ref|data|exc|obj|const|term|doc|file):`~?([^`]+)`"
)
# A directive owns its indented body, so dropping only the ``.. note::`` line
# leaves the body dangling at +4 spaces, which markdown then renders as a code
# block. Consume the body with it.
_REST_DIRECTIVE_RE = re.compile(
    r"^([ \t]*)\.\.[ \t]+[a-z-]+::.*(?:\n(?:\1[ \t]+.*|[ \t]*(?=\n|$)))*", re.MULTILINE
)
# Not preceded or followed by a third backtick, so a ``` fence is left intact.
_DOUBLE_TICK_RE = re.compile(r"(?<!`)``([^`\n]+)``(?!`)")
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)


def as_markdown(value: object) -> str:
    """Convert a source docstring into markdown that renders as intended.

    Docstrings reach a deterministic page verbatim, so whatever dialect the
    author used lands in the rendered wiki. Sphinx roles and directives are by
    far the most common here and are also the ones markdown mangles worst, so
    those are converted; everything else is left alone rather than guessed at.
    """
    text = str(value or "")
    if not text.strip():
        return ""

    # Fenced blocks are already markdown and must survive untouched, so lift
    # them out, rewrite the prose around them, and put them back.
    fences: list[str] = []

    def _stash(match: re.Match[str]) -> str:
        fences.append(match.group(0))
        return f"\x00FENCE{len(fences) - 1}\x00"

    text = _FENCE_RE.sub(_stash, text)
    text = _REST_DIRECTIVE_RE.sub("", text)
    # ``:meth:`Store.get``` -> ``Store.get``, keeping the reference visible as
    # code rather than dropping it.
    text = _REST_ROLE_RE.sub(r"`\1`", text)
    text = _DOUBLE_TICK_RE.sub(r"`\1`", text)
    text = dedent_body(text).strip()
    for i, fence in enumerate(fences):
        text = text.replace(f"\x00FENCE{i}\x00", fence)
    return text


def dedent_body(text: str) -> str:
    """Strip the common indent from every line after the first.

    A docstring's first line starts at the quote, later lines carry the source
    indentation. Left in, four or more leading spaces make markdown treat the
    body as a code block.
    """
    lines = text.splitlines()
    if len(lines) < 2:
        return text
    rest = [ln for ln in lines[1:] if ln.strip()]
    if not rest:
        return lines[0]
    indent = min(len(ln) - len(ln.lstrip()) for ln in rest)
    return "\n".join([lines[0]] + [ln[indent:] if ln.strip() else "" for ln in lines[1:]])


def signature(value: object, limit: int = 120) -> str:
    """Format a captured signature so it reads as a declaration, not as source.

    A signature is captured verbatim across source lines, so it arrives with
    the author's line breaks and indentation in it, and sometimes without its
    tail: a constant whose value opens a bracket on the next line is stored as
    ``PRUNED_DIRS: frozenset[str] = frozenset(``.

    Three passes, cheapest first. Collapse the whitespace. Close the brackets
    an unfinished capture left open. Then, if it is still too long, shorten the
    parameter list to bare names and keep the return annotation — slicing the
    string instead cuts the annotation off, and what a function returns is the
    half a reader is looking for.
    """
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    text = _tidy_punctuation(text)
    text = _normalize_params(text)
    text = _close_unfinished_capture(text)
    if len(text) <= limit:
        return text
    shortened = _names_only_params(text)
    if shortened is not None and len(shortened) <= limit:
        return shortened
    head, params, tail = _split_params(text) or (text, None, "")
    if params is not None and len(head) + len(tail) + 3 <= limit:
        return f"{head}…){tail}"
    cut = text[:limit].rfind(", ")
    if cut > limit // 3:
        return text[:cut] + " …"
    return text[: limit - 1].rstrip().rstrip(",") + "…"


def _code_spans(text: str) -> list[tuple[bool, str]]:
    """*text* split into (is_code, chunk), where a string literal is not code.

    Every rewrite below reshapes source punctuation, and a signature routinely
    carries a regex or a path in a string literal: ``re.compile(r"[|&;]")``,
    ``("Cargo.toml",)``. Reshaping inside one corrupts a value the page then
    states as fact, so the passes see only what is outside the quotes.
    """
    out: list[tuple[bool, str]] = []
    buf: list[str] = []
    quote = ""
    escaped = False
    for ch in text:
        if escaped:
            escaped = False
            buf.append(ch)
            continue
        if ch == "\\":
            escaped = True
            buf.append(ch)
        elif quote:
            buf.append(ch)
            if ch == quote:
                out.append((False, "".join(buf)))
                buf = []
                quote = ""
        elif ch in _QUOTES:
            if buf:
                out.append((True, "".join(buf)))
            buf = [ch]
            quote = ch
        else:
            buf.append(ch)
    if buf:
        out.append((not quote, "".join(buf)))
    return out


_QUOTES = "\"'"

# The spacing a multi-line declaration leaves behind once it is collapsed onto
# one line. Nothing here removes a comma: a trailing one is cosmetic in a
# parameter list and load-bearing in ``("Cargo.toml",)``, so the parameter list
# normalises its own and every other bracket group is left as written.
_SPACING_RULES = (
    (re.compile(r"\(\s+"), "("),
    (re.compile(r"\s+\)"), ")"),
    (re.compile(r"\[\s+"), "["),
    (re.compile(r"\s+\]"), "]"),
    (re.compile(r"\s+,"), ","),
)


def _tidy_punctuation(text: str) -> str:
    """Undo the spacing a multi-line signature leaves once it is collapsed."""
    out = []
    for is_code, chunk in _code_spans(text):
        if is_code:
            for pattern, repl in _SPACING_RULES:
                chunk = pattern.sub(repl, chunk)
        out.append(chunk)
    return "".join(out)


def _close_unfinished_capture(text: str) -> str:
    """Close the brackets a capture that stopped mid-expression left open.

    A constant whose value opens a bracket on the next source line is stored
    as ``PRUNED_DIRS: frozenset[str] = frozenset(``, which renders as a broken
    fragment. Closing it reads as a declaration and, unlike cutting the
    initializer off, keeps every token; the page is the index entry.
    """
    if text.endswith("="):
        return text[:-1].rstrip()
    unclosed = _unclosed_brackets(text)
    if not unclosed:
        return text
    return text + "\u2026" + "".join(_CLOSERS[ch] for ch in reversed(unclosed))


_CLOSERS = {"(": ")", "[": "]", "{": "}"}


def _unclosed_brackets(text: str) -> list[str]:
    """The still-open brackets of *text*, outermost first, quotes excluded."""
    stack: list[str] = []
    for is_code, chunk in _code_spans(text):
        if not is_code:
            continue
        for ch in chunk:
            if ch in _CLOSERS:
                stack.append(ch)
            elif ch in ")]}" and stack and _CLOSERS[stack[-1]] == ch:
                stack.pop()
    return stack


def _split_params(text: str) -> tuple[str, str, str] | None:
    """Split ``head(``, the parameter list, and the trailing ``) -> T``."""
    depth = 0
    open_at = -1
    index = 0
    for is_code, chunk in _code_spans(text):
        if not is_code:
            index += len(chunk)
            continue
        for offset, ch in enumerate(chunk):
            if ch in "([{":
                if depth == 0 and ch == "(" and open_at < 0:
                    open_at = index + offset
                depth += 1
            elif ch in ")]}":
                depth -= 1
                if depth == 0 and open_at >= 0:
                    close_at = index + offset
                    return (
                        text[: open_at + 1],
                        text[open_at + 1 : close_at],
                        text[close_at + 1 :],
                    )
        index += len(chunk)
    return None


def _split_top_level(params: str) -> list[str]:
    """*params* split on the commas that separate one parameter from the next.

    Depth- and quote-aware, so neither ``dict[str, int]`` nor ``sep = ", "``
    splits. A lambda default is the one shape this cannot see through, since
    its comma really is at depth zero; :func:`_names_only_params` then refuses
    the signature rather than printing a name it cannot vouch for.
    """
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for is_code, chunk in _code_spans(params):
        if not is_code:
            current.append(chunk)
            continue
        for ch in chunk:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            if ch == "," and depth == 0:
                parts.append("".join(current))
                current = []
                continue
            current.append(ch)
    parts.append("".join(current))
    return [p for p in (part.strip() for part in parts) if p]


def _normalize_params(text: str) -> str:
    """Rejoin a callable's parameter list, dropping a magic trailing comma.

    Only where the bracket group is a parameter list. A group that follows an
    ``=`` is a value, and ``filenames: tuple[str, ...] = ("Cargo.toml",)`` is a
    one-tuple that becomes a plain string if that comma goes.
    """
    split = _split_params(text)
    if split is None:
        return text
    head, params, tail = split
    if "=" in "".join(c for is_code, c in _code_spans(head) if is_code):
        return text
    return f"{head}{', '.join(_split_top_level(params))}){tail}"


def _names_only_params(text: str) -> str | None:
    """The same signature with each parameter reduced to its name.

    ``root: Path | str`` becomes ``root``, ``*, prune_dirs: frozenset[str] =
    PRUNED_DIRS`` becomes ``*, prune_dirs``. Returns None when there is no
    parameter list, or when a parameter does not reduce to something shaped
    like a name: eliding the whole list is better than printing an identifier
    the signature never declared.
    """
    split = _split_params(text)
    if split is None:
        return None
    head, params, tail = split
    names = [_param_name(part) for part in _split_top_level(params)]
    if not names or not all(_NAME_RE.fullmatch(name) for name in names):
        return None
    return f"{head}{', '.join(names)}){tail}"


_NAME_RE = re.compile(r"[*/]{0,2}[A-Za-z_$][\w$]*|[*/]{1,2}")


def _param_name(param: str) -> str:
    """The declared name of one parameter, without annotation or default."""
    text = param.strip()
    if text in {"*", "**", "/"}:
        return text
    consumed = 0
    for is_code, chunk in _code_spans(text):
        if not is_code:
            break
        cut = min((chunk.find(c) for c in ":=" if c in chunk), default=-1)
        if cut >= 0:
            return text[: consumed + cut].strip()
        consumed += len(chunk)
    return text.strip()


def datestamp(value: object) -> str:
    """A date, as ``YYYY-MM-DD``, from whatever git metadata carries.

    Dates only, never "three days ago": a page's bytes are its reuse key, and
    a relative date would move every one of them every day.
    """
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    return text[:10]


def is_public_api(symbol: Mapping[str, Any]) -> bool:
    """Whether a public symbol is API a caller would reach for.

    Two kinds of name are public to the parser and are not an API. A dunder is
    the language talking to itself, and ``__all__`` in particular is metadata
    *about* the API rather than part of it. A module-level ``variable`` with no
    capital in its name is module state — ``log``, ``logger``, ``router``, and
    Alembic's ``revision`` / ``down_revision`` are 771 of the 878 such symbols
    in this repository, and not one of them is something another file calls.
    The type aliases that share the kind (``SourceFile``, ``ReasoningMode``)
    keep their capitals and stay.

    Demoted, not dropped: the caller still names them on the page, so the
    identifier stays in ``content`` for the index.
    """
    name = str(symbol.get("name") or "")
    if name.startswith("__") and name.endswith("__"):
        return False
    if symbol.get("kind") == "variable" and not symbol.get("parent_name"):
        return any(c.isupper() for c in name)
    return True


def api_symbols(symbols: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The public symbols that belong in the API table."""
    return [s for s in symbols if s.get("visibility") == "public" and is_public_api(s)]


def internal_symbols(symbols: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The public symbols the API table leaves out, in declaration order."""
    return [s for s in symbols if s.get("visibility") == "public" and not is_public_api(s)]


# A prose line rather than a table cell, so it can carry a whole constructor.
# The table's 120 is what keeps a column readable; here the only reason to cut
# is a generated signature long enough to be the page.
_DECLARATION_LIMIT = 400


def declarations(symbols: Iterable[Mapping[str, Any]]) -> list[str]:
    """How to spell symbols the API table leaves out, one string each.

    The signature rather than the name, because the name alone would take the
    rest of the declaration out of ``content``: ``log`` on its own drops
    ``structlog``, ``get_logger`` and ``__name__`` from the page, and the page
    is the index entry. Falls back to the name for a symbol the parser
    captured no signature for, and appends it where the signature somehow does
    not spell it, so no name can go missing either way.
    """
    out: list[str] = []
    for symbol in symbols:
        name = str(symbol.get("name") or "")
        declared = signature(symbol.get("signature") or "", _DECLARATION_LIMIT)
        if not declared:
            declared = name
        elif name and name not in declared:
            declared = f"{name}: {declared}"
        if declared:
            out.append(declared)
    return out


def code_span(text: str) -> str:
    """*text* as a markdown code span, fenced long enough to contain it.

    A single backtick fence ends at the first backtick inside it, and 47 of
    this repository's own signatures carry one — a regex matching a fence, for
    instance. Markdown's own rule is a longer fence, which keeps the bytes
    exactly as captured rather than escaping them into something else.
    """
    body = str(text)
    longest = max((len(run) for run in re.findall(r"`+", body)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if body.startswith("`") or body.endswith("`") else ""
    return f"{fence}{pad}{body}{pad}{fence}"


def group_paths(paths: Iterable[str], limit: int = 25) -> list[dict[str, Any]]:
    """Group file paths under their directory, busiest directory first.

    A flat list of twenty-five import paths is the same twenty-five strings a
    reader could get from grep. Grouped, the shape of the dependency shows: of
    the thirty-seven files importing ``fs_walk``, nine are import resolvers.

    Every path is carried through whole, because the page is also the index
    entry: a directory heading with basenames under it would drop the strings
    a question matches on.
    """
    grouped: dict[str, list[str]] = {}
    for path in list(paths)[:limit]:
        directory = path.rsplit("/", 1)[0] if "/" in path else "."
        grouped.setdefault(directory, []).append(path)
    return sorted(
        ({"directory": d, "paths": p} for d, p in grouped.items()),
        key=lambda g: (-len(g["paths"]), g["directory"]),
    )


def register_filters(env: Any) -> None:
    """Register every filter the deterministic templates use on *env*.

    One function rather than a list repeated at each call site: a template
    that reaches for a filter the caller forgot raises at render time, and the
    callers are the generator and four test fixtures.
    """
    for name, fn in (
        ("oneline", oneline),
        ("as_markdown", as_markdown),
        ("signature", signature),
        # Page shaping the templates cannot express: the split between
        # declared API and module bookkeeping, the grouping of a dependency
        # list, and a date out of a git metadata value.
        ("api_symbols", api_symbols),
        ("internal_symbols", internal_symbols),
        ("declarations", declarations),
        ("code_span", code_span),
        ("group_paths", group_paths),
        ("datestamp", datestamp),
    ):
        env.filters.setdefault(name, fn)


_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

FILE_PAGE_TEMPLATE = "file_page.j2"
SYMBOL_SPOTLIGHT_TEMPLATE = "symbol_spotlight.j2"

# Metadata key holding a structural page's render fingerprint. It lives in
# metadata rather than in a column of its own because ``GeneratedPage`` already
# has a ``content_hash`` field that is never persisted (nothing populates it
# when prior pages are loaded), and a second unpersisted hash would be worse
# than none. metadata_json is written and read back on every page already.
RENDER_KEY = "render_key"


def _read_template_source(template: str, template_dir: Path | None) -> str:
    """The bytes a render will use for *template*, override before built-in.

    A custom style ships its own ``templates/`` directory that the generator's
    Jinja loader resolves first (a ChoiceLoader over the style dir then the
    built-in one). The update path has no generator, so it mirrors that order
    by hand: a style's own ``file_page.j2`` must be what its fingerprint hashes,
    or every page a custom style rendered looks stale against the base template
    forever.
    """
    if template_dir is not None:
        override = template_dir / template
        if override.exists():
            try:
                return override.read_text(encoding="utf-8")
            except OSError:
                log.warning("structural.style_template_unreadable", template=template)
    try:
        return (_TEMPLATES_DIR / template).read_text(encoding="utf-8")
    except OSError:
        # An unreadable template would otherwise hash to a constant and pin
        # every page of this type to a stale fingerprint forever.
        log.warning("structural.template_source_unreadable", template=template)
        return ""


def structural_fingerprint(
    template: str,
    *,
    language: str = "en",
    style_fingerprint: str = "",
    source: str | None = None,
    template_dir: Path | None = None,
) -> str:
    """Hash the inputs that shape a structural page besides its subject.

    The template source is the substantive one: a release that improves a
    template has to reach wikis that already exist. Language and style are
    folded for the same reason the model path folds them, and the hand-bumped
    version constant covers renderer changes that leave the template bytes
    alone.

    ``source`` is passed in when the caller already holds the resolved template
    bytes (the generator, whose Jinja loader applied any style override). When
    it is not, ``template_dir`` is the style's own template directory, tried
    before the built-in so an update run reproduces the loader's resolution
    order without building a generator.
    """
    if source is None:
        source = _read_template_source(template, template_dir)
    raw = "\x00".join(
        [STRUCTURAL_GENERATION_VERSION, template, source, language or "en", style_fingerprint]
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def structural_content_hash(subject_hash: str, fingerprint: str) -> str:
    """Reuse key for a structural page: its subject folded with the salt.

    Empty when the subject has no stable hash of its own (a layer, a cycle).
    Those pages are repo-wide, so an update leaves them to a full run rather
    than refreshing them per file, and an empty hash keeps them out of the
    fingerprint-staleness sweep instead of re-rendering every one of them on
    every run.
    """
    if not subject_hash:
        return ""
    return hashlib.sha256(f"{subject_hash}:{fingerprint}".encode()).hexdigest()


def stale_file_page_paths(
    stored_hashes: Mapping[str, str],
    parsed_files: Iterable[Any],
    *,
    language: str = "en",
    style_fingerprint: str = "",
    template_dir: Path | None = None,
) -> list[str]:
    """Paths whose stored file page came from an older renderer.

    This is what makes the salt do anything. ``update`` re-renders the pages of
    files that changed, so a file nobody touched keeps whatever its page said
    when it was written, and no model will ever come along and improve it. A
    page whose stored hash disagrees with the one this release would produce is
    therefore the only signal that a template improvement has not landed yet.

    A page with no stored row is absent rather than stale and is left to the
    normal path. A page storing an empty hash predates the salt, so it counts
    as stale and gets one regeneration, which is exactly the behaviour an
    existing wiki wants on the release that introduces this.
    """
    fingerprint = structural_fingerprint(
        FILE_PAGE_TEMPLATE,
        language=language,
        style_fingerprint=style_fingerprint,
        template_dir=template_dir,
    )
    stale: list[str] = []
    for parsed in parsed_files:
        subject_hash = getattr(parsed, "content_hash", "") or ""
        if not subject_hash:
            # No stable subject hash means no stable expectation to compare
            # against; treating that as stale would re-render it every run.
            continue
        path = parsed.file_info.path
        stored = stored_hashes.get(compute_page_id("file_page", path))
        if stored is None:
            continue
        if stored != structural_content_hash(subject_hash, fingerprint):
            stale.append(path)
    return stale


def stale_spotlight_paths(
    stored_keys_by_path: Mapping[str, Iterable[str]],
    parsed_files: Iterable[Any],
    *,
    language: str = "en",
    style_fingerprint: str = "",
    template_dir: Path | None = None,
) -> list[str]:
    """Files whose stored symbol spotlights came from an older renderer.

    The same idea as :func:`stale_file_page_paths` and deliberately a separate
    function, because the two are keyed differently. A file page is one page
    per path, so it can be looked up by page id. A file has many spotlights,
    one per selected symbol, and which symbols were selected on the run that
    wrote them is not reconstructible here — so the caller groups the stored
    keys by defining file and this compares against the set.

    Returns **file paths**, not page ids, because regeneration is driven by
    file: ``update`` re-parses a path and re-renders every page derived from
    it. One stale spotlight therefore makes its defining file the unit of work.
    """
    fingerprint = structural_fingerprint(
        SYMBOL_SPOTLIGHT_TEMPLATE,
        language=language,
        style_fingerprint=style_fingerprint,
        template_dir=template_dir,
    )
    stale: list[str] = []
    for parsed in parsed_files:
        subject_hash = getattr(parsed, "content_hash", "") or ""
        if not subject_hash:
            # Same reasoning as the file-page sweep: no stable subject means no
            # stable expectation, and treating it as stale re-renders forever.
            continue
        stored = stored_keys_by_path.get(parsed.file_info.path)
        if not stored:
            # No spotlight stored for this file is absent, not stale.
            continue
        expected = structural_content_hash(subject_hash, fingerprint)
        if any(key != expected for key in stored):
            stale.append(parsed.file_info.path)
    return stale


class StructuralRenderMixin:
    """Template-only renderers, mixed into PageGenerator.

    Requires the host to provide ``_render``, ``_provider`` and ``_config``.
    """

    def _render_page(
        self,
        *,
        page_type: str,
        target_path: str,
        title: str,
        template: str,
        **render_kwargs: Any,
    ) -> GeneratedPage:
        """Render one template page and wrap it as a GeneratedPage.

        The mirror of ``_build_generated_page`` for the no-model path: same
        fields, zero tokens, ``provider_name="template"``.

        Confidence is a constant here, not a parameter. It used to be
        overridable so a stub could claim less than a sole renderer's page,
        on the reasoning that a reader had to be told which they were looking
        at. Every page this renders makes the same claim, whichever caller
        asked for it: the statements came from the index and no model saw
        them. The only page that claims less is one whose provider call
        failed, and ``_stub_fallback`` lowers that one after the fact, because
        the failure is not knowable here.
        """
        content = self._render(template, style_prefix=False, **render_kwargs)
        now = _now_iso()
        return GeneratedPage(
            page_id=compute_page_id(page_type, target_path),
            page_type=page_type,
            title=title,
            content=content,
            summary=_extract_summary(content, skip_metadata=True),
            source_hash=compute_source_hash(content),
            model_name=self._provider.model_name,
            provider_name="template",
            input_tokens=0,
            output_tokens=0,
            cached_tokens=0,
            generation_level=GENERATION_LEVELS[page_type],
            target_path=target_path,
            created_at=now,
            updated_at=now,
            confidence=TEMPLATE_PAGE_CONFIDENCE,
        )

    def _structural_page(
        self,
        *,
        page_type: str,
        target_path: str,
        title: str,
        template: str,
        subject_hash: str = "",
        **render_kwargs: Any,
    ) -> GeneratedPage:
        """Render a page whose only renderer this is.

        The page carries the generation-version salt in its metadata: the
        subject's own hash folded with a fingerprint of the renderer. Nothing
        else refreshes these pages. ``update`` re-renders a page when its file changes, so a
        template improvement would otherwise never reach a repository whose
        files happen not to change, and no model will ever touch these pages to
        fix that. Comparing the stored hash against a freshly computed one is
        what turns a released template change into exactly one regeneration.
        """
        page = self._render_page(
            page_type=page_type,
            target_path=target_path,
            title=title,
            template=template,
            **render_kwargs,
        )
        render_key = self._structural_content_hash(template, subject_hash)
        if render_key:
            page.metadata[RENDER_KEY] = render_key
        return page

    def _stub_page(
        self,
        *,
        page_type: str,
        target_path: str,
        title: str,
        template: str,
        **render_kwargs: Any,
    ) -> GeneratedPage:
        """Render the keyless stub for a page a model writes when one is present.

        No render key: a stub is not what the page is meant to be, so there is
        nothing to keep fresh. The moment a key shows up the page is rewritten
        wholesale, and until then rebuilding it costs nothing.

        Confidence stays at the template default. Every statement this renders
        came from the index and no model saw it, which is exactly the claim
        :data:`TEMPLATE_PAGE_CONFIDENCE` makes. It is the same argument
        ``_model_free_onboarding_page`` already makes for the subkinds it
        finishes without a model. That the page is *thin* is a different
        question with its own carrier: ``provider_name='template'`` is what the
        tree's "not written yet" marker and the reader's upgrade affordance
        read, and neither consults confidence.

        The provider-outage path stamps :data:`STUB_PAGE_CONFIDENCE` over this
        afterwards (see ``_stub_fallback``), because there the page stands in
        for prose the run tried and failed to write, which is the one thing a
        reader cannot tell from the page itself.
        """
        return self._render_page(
            page_type=page_type,
            target_path=target_path,
            title=title,
            template=f"{_STUB_PREFIX}/{template}",
            **render_kwargs,
        )

    def _structural_fingerprint(self, template: str) -> str:
        """This generator's fingerprint for *template*, cached per template.

        Reads the source through the Jinja environment rather than off disk so
        a style that overrides a template fingerprints its own version. Cached
        because a full index renders thousands of pages.
        """
        cached = self._structural_fingerprints.get(template)
        if cached is not None:
            return cached
        try:
            source: str | None = self._jinja_env.loader.get_source(self._jinja_env, template)[0]
        except Exception:
            source = None  # fall back to the built-in template on disk
        digest = structural_fingerprint(
            template,
            language=self._language or "en",
            style_fingerprint=self._style.fingerprint,
            source=source,
        )
        self._structural_fingerprints[template] = digest
        return digest

    def _structural_content_hash(self, template: str, subject_hash: str) -> str:
        return structural_content_hash(subject_hash, self._structural_fingerprint(template))

    # ------------------------------------------------------------------
    # Sole renderers. These page types have no model path.
    # ------------------------------------------------------------------

    def _structural_symbol_spotlight(
        self, ctx: Any, target_path: str, title: str, subject_hash: str = ""
    ) -> GeneratedPage:
        return self._structural_page(
            page_type="symbol_spotlight",
            target_path=target_path,
            title=title,
            template=SYMBOL_SPOTLIGHT_TEMPLATE,
            subject_hash=subject_hash,
            ctx=ctx,
        )

    def _structural_scc_page(self, ctx: Any, scc_id: str, title: str) -> GeneratedPage:
        return self._structural_page(
            page_type="scc_page",
            target_path=scc_id,
            title=title,
            template="scc_page.j2",
            ctx=ctx,
            # The heading says where the cycle is. The page id stays the hash
            # and the page still prints it, so a log line or a link naming one
            # still resolves.
            page_title=title,
            # Ranked by how many cross-edges each file carries: the highest
            # is the cheapest place to break the cycle. Computed here rather
            # than in Jinja because the template language makes grouping
            # painful and this is the one genuinely useful thing the page can
            # say that the LLM page says in prose.
            decouple_ranking=_rank_cycle_participants(ctx),
        )

    def _structural_api_contract(
        self, ctx: Any, file_path: str, title: str, subject_hash: str = ""
    ) -> GeneratedPage:
        return self._structural_page(
            page_type="api_contract",
            target_path=file_path,
            title=title,
            template="api_contract.j2",
            subject_hash=subject_hash,
            ctx=ctx,
        )

    def _structural_infra_page(
        self, ctx: Any, file_path: str, title: str, subject_hash: str = ""
    ) -> GeneratedPage:
        return self._structural_page(
            page_type="infra_page",
            target_path=file_path,
            title=title,
            template="infra_page.j2",
            subject_hash=subject_hash,
            ctx=ctx,
        )

    # ------------------------------------------------------------------
    # Keyless stubs. These page types keep a model path (D5's upgrade axis).
    # ------------------------------------------------------------------

    def _stub_module_page(
        self,
        ctx: Any,
        target_path: str,
        title: str,
        module_git_summary: dict | None,
    ) -> GeneratedPage:
        return self._stub_page(
            page_type="module_page",
            target_path=target_path,
            title=title,
            template="module_page.j2",
            ctx=ctx,
            module_git_summary=module_git_summary,
        )

    def _stub_repo_overview(
        self, ctx: Any, repo_name: str, title: str, repo_git_summary: dict | None
    ) -> GeneratedPage:
        return self._stub_page(
            page_type="repo_overview",
            target_path=repo_name,
            title=title,
            template="repo_overview.j2",
            ctx=ctx,
            repo_git_summary=repo_git_summary,
        )

    def _stub_architecture_diagram(
        self, ctx: Any, repo_name: str, title: str, overview_mermaid: str | None
    ) -> GeneratedPage:
        return self._stub_page(
            page_type="architecture_diagram",
            target_path=repo_name,
            title=title,
            template="architecture_diagram.j2",
            ctx=ctx,
            # Structural on the model path too, where it overwrites whatever
            # diagram the model drew. Here it is simply the diagram.
            overview_mermaid=overview_mermaid or "",
        )

    def _stub_onboarding_page(self, spec: Any, ctx: Any, target_path: str) -> GeneratedPage:
        page = self._stub_page(
            page_type="onboarding",
            target_path=target_path,
            title=spec.title,
            template=f"onboarding/{spec.template}",
            ctx=ctx,
            slot=spec.slot,
        )
        page.metadata["subkind"] = spec.slot
        page.metadata["onboarding_slot"] = spec.slot
        return page

    def _model_free_onboarding_page(self, spec: Any, ctx: Any, target_path: str) -> GeneratedPage:
        """Render a subkind that is finished without a model, not stubbed.

        The same template, and a different claim about it. A stub is real
        material with the prose missing, so it sits below the reader UI's
        banner threshold and the tree offers to have a model write it. A
        ``deterministic`` subkind is already everything its page is meant to
        be — every statement on it came from the index and no model saw it —
        which is exactly the claim :data:`TEMPLATE_PAGE_CONFIDENCE` makes.

        ``model_free`` is stamped for the consumers that would otherwise read
        ``provider_name='template'`` as "unwritten": scope resolution would
        offer this page to ``generate --unwritten`` forever, and the web reader
        would mark it "a model has not written this page yet" — on a page no
        model is ever going to write.
        """
        page = self._render_page(
            page_type="onboarding",
            target_path=target_path,
            title=spec.title,
            template=f"{_STUB_PREFIX}/onboarding/{spec.template}",
            ctx=ctx,
            slot=spec.slot,
        )
        page.metadata["subkind"] = spec.slot
        page.metadata["onboarding_slot"] = spec.slot
        page.metadata["model_free"] = True
        return page


def _rank_cycle_participants(ctx: Any) -> list[dict]:
    """Order a cycle's files by how many of its edges they carry.

    A file that both imports and is imported by the rest of the cycle is
    where the loop is tightest, so it is the first place to look when
    breaking it. Returns ``[{"path", "out", "in", "total"}]``, highest first.
    """
    counts: dict[str, dict[str, int]] = {f: {"out": 0, "in": 0} for f in ctx.files}
    for edge in ctx.cross_imports:
        src, dst = edge.get("from", ""), edge.get("to", "")
        if src in counts:
            counts[src]["out"] += 1
        if dst in counts:
            counts[dst]["in"] += 1
    ranked = [
        {"path": path, "out": c["out"], "in": c["in"], "total": c["out"] + c["in"]}
        for path, c in counts.items()
    ]
    ranked.sort(key=lambda r: (-r["total"], r["path"]))
    return ranked
