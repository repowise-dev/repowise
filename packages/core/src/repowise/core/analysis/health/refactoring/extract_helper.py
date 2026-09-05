"""Extract Helper detector — clone dedup as a shared helper (Phase 2).

When the same block of code is duplicated across two or more sites, the
fix is to extract it once into a shared helper. The duplication is not a
heuristic: it *is* the verified clone pairs the health pass already
computed (``duplication.detect_clones`` → ``ctx.clones``), so the
suggestion is deterministic and matches the ``dry_violation`` biomarker.

The detector runs in the existing per-file pass, but a clone block spans
files, so two safeguards keep one block from producing one nag per site:

- **Clone-set clustering.** A file's clone pairs are grouped by the block
  they touch (overlapping line ranges on this file's side), and each
  group's *occurrences* are the block's region here plus every partner's
  region. Because the duplication detector emits a clone bucket pairwise,
  the lexicographically-smallest member of a clone set pairs with every
  other member, so that one anchor file sees the whole set (A↔B, A↔C ⇒
  the set ``{A, B, C}`` is visible from ``A``).
- **Canonical anchor.** Each block is emitted only from the pass of its
  anchor file — the smallest occurrence path — so ``{A, B, C}`` yields one
  suggestion (from ``A``), never three.

Precision-first, the gate demands a block genuinely worth a helper:

- the shared span is at least ``_MIN_HELPER_LINES`` real lines (a helper,
  not a two-line idiom);
- after dropping test-file and generated occurrences (DB migrations,
  vendored bundles) the block still duplicates across ``>= 2`` sites — test
  fixtures and migration boilerplate duplicate constantly but a shared
  helper is the wrong fix for them;
- the recovered impact is read off the file's ``dry_violation`` finding
  when it overlaps the block, else ``0`` (same posture as Extract Class).

Confidence rides the co-change signal: a clone whose sites are actively
co-modified is real, maintained duplication (``high``); a dormant clone is
still worth extracting but ranks ``medium``.
"""

from __future__ import annotations

import re
from typing import Any

from ....test_paths import is_test_related_path
from .models import RefactoringContext, RefactoringSuggestion
from .registry import RefactoringDetector, effort_bucket, register

# The biomarker this detector answers — the recovered impact is read off it.
_SOURCE_BIOMARKER = "dry_violation"

# Minimum shared-line span for a clone to be worth a helper. The clone
# pipeline already floors regions at 6 lines (``DEFAULT_MIN_LINES``); a
# helper is a real unit of behaviour, so we ask for a little more and reject
# the smallest window-sized matches that read as incidental similarity.
_MIN_HELPER_LINES = 8

# Co-change count at/above which duplication counts as actively maintained
# (mirrors ``dry_violation._ACTIVE_CO_CHANGE``) — the strong, high-confidence
# smell rather than a dormant clone. Public because the opportunity composer
# gates the same threshold when it decides whether a clone instructs a change
# or only evidences one.
ACTIVE_CO_CHANGE = 3

# Slack (lines) for treating two clone regions on this file's side as the
# same block — matches the duplication merger's one-line window slack.
_REGION_SLACK = 1

# Cap on the stored snippet. An XL clone block can be hundreds of lines; the
# snippet is for a glance ("this is the duplicated code"), not the whole thing,
# so it is clipped and the plan flags that it was.
_MAX_SNIPPET_LINES = 40


# Line shapes that declare something rather than do something. A clone made
# entirely of these is not extractable: you cannot lift an import block, a
# parameter list or a run of dataclass fields into a shared helper, because
# there is no behaviour there to share — only a shape that happens to repeat.
_IMPORT_PREFIXES = (
    "import ",
    "from ",
    "using ",
    "#include",
    "export ",
    "require(",
    "const {",
    "@import",
)
# A constant RHS. ``name = compute(x)`` is real code and must stay a candidate;
# ``name = []`` / ``name = "x"`` / ``FOO = 3`` is a declaration.
_CONST_RHS = re.compile(
    r"""^\s*(
        None|True|False|null|true|false      # literals
        | -?\d[\d_.eE+-]*                     # numbers
        | ["'].*["']                          # strings
        | \[\s*\] | \{\s*\} | \(\s*\)          # empty collections
        | (list|dict|set|tuple|str|int|float|bool)\s*\(\s*\)
    )\s*,?\s*$""",
    re.VERBOSE,
)
# The field-declaration constructs a dataclass-style default is allowed to use.
# Deliberately a closed vocabulary rather than "any call": ``items: list[str] =
# field(default_factory=list)`` declares a field, but ``result: int =
# calculate_total(items, rate)`` is behaviour that merely carries an annotation,
# and accepting arbitrary right-hand sides here silently dropped the second —
# a false negative on one of the most common shapes in typed Python and TS.
_FIELD_CTOR = re.compile(
    r"^\s*(dataclasses\.|attr\.|attrs\.|pydantic\.|msgspec\.)?"
    r"(field|Field|ib|attrib|mapped_column|Column|relationship)\s*\(",
)
# Table-level declarative constructs. Same closed-vocabulary posture as
# ``_FIELD_CTOR``: these are schema, never behaviour, and a run of them was the
# other half of the ORM boilerplate that reached the top of the plan board.
_TABLE_DECL = re.compile(
    r"^\s*(UniqueConstraint|Index|CheckConstraint|ForeignKeyConstraint|PrimaryKeyConstraint)\s*\(",
)
# A dunder class attribute (``__table_args__``, ``__tablename__``, ``__all__``)
# configures the class; it does not compute.
_DUNDER_ATTR = re.compile(r"^\s*__\w+__\s*=")
_DECL_LINE = re.compile(
    r"""^\s*(
        [\w.\[\]"']+ \s* : \s* [^=]+            # annotated, no default: name: type
        | [\w]+ \s* ,?                          # bare parameter / enum member
        | [)\]}]\s* [:,]? \s* (->.*)? :? \s*    # a closer, with or without a return type
        | (async\s+)?(def|function|fn|func|class|interface|type|struct)\s+[\w<>]+\s*[(<{:]?\s*
        | (async\s+)?(def|function|fn|func|class|interface|type|struct)\s+[\w<>]+\s*
            \( (?: [^()] | \([^()]*\) )* \)      # its parameter list, and nothing past it
            \s* (->[^:{;]*)? \s* [:{]? \s*     # a signature joined back whole
        | @[\w.]+ \s* \(?                       # decorator / annotation
    )$""",
    re.VERBOSE,
)
_CONTROL_FLOW = re.compile(
    r"\b(if|else|elif|for|while|try|except|catch|finally|return|yield|raise|throw|"
    r"with|switch|case|await|match|break|continue|next|fallthrough|defer|goto|redo|retry)\b"
)


_STRING_LITERAL = re.compile(r"\"([^\"\\]|\\.)*\"|'([^'\\]|\\.)*'")
_TRAILING_COMMENT = re.compile(r"(#|//).*$")


def _logical_lines(lines: list[str]) -> list[str]:
    """Join bracket continuations so one declaration reads as one line.

    ``user_id: Mapped[str] = mapped_column(\n    String(32), nullable=False\n)``
    is one field declaration. Classified line by line, its middle line matches
    no declaration shape and the whole block reads as behaviour -- which is how
    runs of ORM columns reached the top of the plan board.
    """
    out: list[str] = []
    pending = ""
    depth = 0
    for line in lines:
        pending = f"{pending} {line.strip()}".strip() if pending else line.strip()
        depth += _continuation_depth(line)
        if depth <= 0:
            out.append(pending)
            pending = ""
            depth = 0
    if pending:
        out.append(pending)
    return out


def _continuation_depth(line: str) -> int:
    """Net unclosed ``(``/``[`` on *line*, ignoring strings and comments.

    Braces are excluded deliberately: in a brace language ``{`` opens a body,
    not a continuation, and counting it welded whole function bodies into one
    line, which then read as a declaration. A bracket inside a string or a
    trailing comment is text, and counting those desynchronised the depth and
    absorbed the statements that followed.
    """
    bare = _STRING_LITERAL.sub("", line)
    bare = _TRAILING_COMMENT.sub("", bare)
    return sum(bare.count(o) - bare.count(c) for o, c in ("()", "[]"))


def _code_lines(block: list[str]) -> list[str]:
    """*block* minus blanks and whole-line comments.

    A leading ``*`` is a JSDoc/javadoc comment continuation (``* @param x``) —
    but it is also a C/Go pointer dereference (``*out = compute(a, b);``), and
    stripping those left an empty line list, which the caller reads as "nothing
    but declarations". So a ``*`` line only counts as a comment when it carries
    no assignment and no statement terminator.
    """
    out = []
    for raw in block:
        s = raw.strip()
        if not s:
            continue
        if s.startswith(("#", "//", "/*", "*/", '"""', "'''", "<!--")):
            continue
        if s.startswith("*") and "=" not in s and ";" not in s:
            continue
        out.append(s)
    return out


def _is_declaration_only(block: list[str]) -> bool:
    """True when every line of *block* declares rather than executes.

    Three real false positives from this repo's own index motivated this, all
    of them plans proposing to extract something no editor could extract:

    - a 21-line ``from repowise.cli.helpers import (...)`` block, shared with
      ``routers/workspace.py`` because both import the same names;
    - two 20-line ``def update_command(...)`` / ``def run_update(...)``
      *signatures*, paired because their parameter lists agree;
    - runs of ``field(default_factory=list)`` dataclass members across four
      unrelated modules (the case filed as C6, which arrived at the top of the
      payload carrying the highest occurrence count and blast radius, i.e. the
      most-trusted slot).

    A single line with control flow, or an unannotated assignment whose RHS is
    not a constant, is enough to keep the block a candidate — the gate has to
    stay conservative, because a false *negative* here silently drops real
    duplication, which is the more expensive mistake.

    Deliberately *not* applied in the duplication detector or the
    ``dry_violation`` biomarker: those feed calibrated scores that are frozen,
    and the defect here is the advice, not the measurement. The block still
    counts as duplication; it just no longer produces a plan to extract it.
    """
    lines = _logical_lines(_code_lines(block))
    if not lines:
        return True
    for line in lines:
        if _CONTROL_FLOW.search(line):
            return False
        if line.startswith(_IMPORT_PREFIXES) or line in ("(", ")", "):", "}", "];", "{"):
            continue
        if _TABLE_DECL.match(line) or _DUNDER_ATTR.match(line):
            continue
        if _DECL_LINE.match(line):
            continue
        # An assignment is a declaration only by what it assigns. A constant
        # RHS is inert either way; an annotated line may additionally use a
        # field-declaration constructor (``field(default_factory=list)``).
        # Anything else computed is behaviour and stays extractable, annotated
        # or not — ``result: int = calculate_total(items, rate)`` is code that
        # happens to carry a type, not a declaration.
        head, sep, rhs = line.partition("=")
        if sep:
            if _CONST_RHS.match(rhs):
                continue
            if ":" in head and _FIELD_CTOR.match(rhs):
                continue
        return False
    return True


def _is_generated_path(path: str) -> bool:
    """Non-refactorable generated/append-only code (DB migrations, vendored
    bundles). Their boilerplate duplicates heavily but extracting a shared
    helper is the wrong advice — a migration must stay self-contained — so
    these occurrences are dropped like test ones (plan's "no generated-file
    noise" gate)."""
    p = path.lower().replace("\\", "/")
    return (
        "/migrations/versions/" in p
        or "/alembic/versions/" in p
        or "/migrations/" in p
        or "/node_modules/" in p
        or "/vendor/" in p
        or "/__generated__/" in p
        or p.endswith(".min.js")
    )


def _is_skippable_occurrence(path: str, language: str | None = None) -> bool:
    """Duplication among test fixtures is common and low value, and a migration
    must stay self-contained, so both are dropped before a suggestion is formed.

    Test support counts as test material here: sharing a helper out of
    ``conftest.py`` is the same bad advice as sharing one out of a test.
    """
    return is_test_related_path(path, language) or _is_generated_path(path)


class _Block:
    """One duplicated code block as seen from the anchor file: the region on
    this file's side plus the geometry needed to gather every occurrence."""

    __slots__ = ("anchor_end", "anchor_start", "co_change", "occurrences", "token_count")

    def __init__(self, start: int, end: int) -> None:
        self.anchor_start = start
        self.anchor_end = end
        # (file, line_start, line_end) — de-duplicated, sorted at emit time.
        self.occurrences: set[tuple[str, int, int]] = set()
        self.token_count = 0
        self.co_change = 0

    def touches(self, start: int, end: int) -> bool:
        """Overlap (within slack) on the anchor side — same physical block."""
        return start <= self.anchor_end + _REGION_SLACK and end >= self.anchor_start - _REGION_SLACK

    def absorb(self, start: int, end: int, pair: Any, *, anchor: str) -> None:
        self.anchor_start = min(self.anchor_start, start)
        self.anchor_end = max(self.anchor_end, end)
        self.token_count = max(self.token_count, int(getattr(pair, "token_count", 0)))
        self.co_change = max(self.co_change, int(getattr(pair, "co_change_count", 0) or 0))
        # The anchor-side region of this pair is always an occurrence; add it
        # plus the partner region(s). Intra-file pairs contribute both regions.
        self.occurrences.add((anchor, start, end))
        if pair.file_a == anchor and pair.file_b == anchor:
            self.occurrences.add((anchor, pair.b_start_line, pair.b_end_line))
        elif pair.file_a == anchor:
            self.occurrences.add((pair.file_b, pair.b_start_line, pair.b_end_line))
        else:
            self.occurrences.add((pair.file_a, pair.a_start_line, pair.a_end_line))


def _symbol_spans(graph: Any, file_path: str, cache: dict[str, list[tuple[int, int]]]) -> list[tuple[int, int]]:
    """Declaration spans defined in *file_path*, via ``defines`` edges.

    Empty when the graph has no node for the file, which the caller reads as
    "no evidence" and abstains on rather than guessing.
    """
    hit = cache.get(file_path)
    if hit is not None:
        return hit
    spans: list[tuple[int, int]] = []
    if graph is not None and file_path in graph:
        for _u, target, data in graph.out_edges(file_path, data=True):
            if data.get("edge_type") != "defines":
                continue
            node = graph.nodes[target]
            if node.get("node_type") != "symbol" or node.get("kind") == "module":
                continue
            start, end = node.get("start_line"), node.get("end_line")
            if isinstance(start, int) and isinstance(end, int) and end >= start:
                spans.append((start, end))
    cache[file_path] = spans
    return spans


def _within_one_declaration(
    occurrence: tuple[str, int, int], graph: Any, cache: dict[str, list[tuple[int, int]]]
) -> bool:
    """True when the occurrence sits inside a single declaration.

    Every symbol whose span it touches must also contain it. That admits a run
    of top-level statements and a block nested in a function or class (a method
    inside its class contains it twice over), and rejects both a span that ends
    part-way into the next declaration and one that swallows whole declarations.
    A helper function cannot be lifted out of either.
    """
    file_path, start, end = occurrence
    spans = _symbol_spans(graph, file_path, cache)
    if not spans:
        return True  # no symbol facts for this file: nothing to gate on
    for sym_start, sym_end in spans:
        if sym_end < start or sym_start > end:
            continue
        if not (sym_start <= start and end <= sym_end):
            return False
    return True


@register
class ExtractHelperDetector(RefactoringDetector):
    name = "extract_helper"

    def detect(self, ctx: RefactoringContext) -> list[RefactoringSuggestion]:
        if not ctx.clones:
            return []

        blocks = self._cluster_blocks(ctx)
        if not blocks:
            return []

        impact_lookup = self._impact_for_dry_violation(ctx)
        spans_cache: dict[str, list[tuple[int, int]]] = {}
        out: list[RefactoringSuggestion] = []
        for block in blocks:
            suggestion = self._build_suggestion(ctx, block, impact_lookup, spans_cache)
            if suggestion is not None:
                out.append(suggestion)

        # Stable order: biggest recovery first, then — because dry_violation
        # deductions are near-uniform, so impact rarely separates clones — the
        # co-change x span priority (actively co-modified, larger
        # blocks first), then target for a fully deterministic tie-break.
        out.sort(
            key=lambda s: (
                -s.impact_delta,
                -int(s.evidence.get("co_change_count", 0)),
                -int(s.evidence.get("duplicated_lines", 0)),
                s.target_symbol,
            )
        )
        return out

    def _cluster_blocks(self, ctx: RefactoringContext) -> list[_Block]:
        """Group this file's clone pairs into blocks by their anchor-side
        region. Pairs are processed in a deterministic order so block
        boundaries (and therefore the output) never depend on dict order."""
        anchor = ctx.file_path

        def _anchor_region(pair: Any) -> tuple[int, int]:
            # For an inter-file pair, the region on *this* file's side; for an
            # intra-file pair, the earlier of the two regions.
            if pair.file_a == anchor and pair.file_b == anchor:
                return min(
                    (pair.a_start_line, pair.a_end_line),
                    (pair.b_start_line, pair.b_end_line),
                )
            if pair.file_a == anchor:
                return pair.a_start_line, pair.a_end_line
            return pair.b_start_line, pair.b_end_line

        ordered = sorted(ctx.clones, key=lambda p: _anchor_region(p))
        blocks: list[_Block] = []
        for pair in ordered:
            start, end = _anchor_region(pair)
            target = next((b for b in blocks if b.touches(start, end)), None)
            if target is None:
                target = _Block(start, end)
                blocks.append(target)
            target.absorb(start, end, pair, anchor=anchor)
        return blocks

    def _build_suggestion(
        self,
        ctx: RefactoringContext,
        block: _Block,
        impact_lookup: list[tuple[int, int, float]],
        spans_cache: dict[str, list[tuple[int, int]]],
    ) -> RefactoringSuggestion | None:
        # Drop test-file occurrences — duplication among fixtures is noise —
        # then coalesce the overlapping windows the clone detector emits for one
        # physical block into a single site per region (without merging, the
        # same import/parse block reads as "5 sites" when it is really one).
        # Clones are detected within a language, so every occurrence in a block
        # shares ``ctx.language`` — which the ambiguous ``spec/`` rule needs.
        kept = [
            o for o in block.occurrences if not _is_skippable_occurrence(o[0], ctx.language)
        ]
        occurrences = [
            o
            for o in _merge_ranges_per_file(kept)
            if _within_one_declaration(o, ctx.graph, spans_cache)
        ]
        if len(occurrences) < 2:
            return None

        occ_files = sorted({o[0] for o in occurrences})
        # Canonical anchor: emit each block exactly once, from its smallest
        # occurrence path. (Also guarantees ``ctx.file_path`` is non-test.)
        if ctx.file_path != occ_files[0]:
            return None

        duplicated_lines = max(end - start + 1 for _f, start, end in occurrences)
        if duplicated_lines < _MIN_HELPER_LINES:
            return None

        # Anchor region = this file's merged region overlapping the block (its
        # largest, deterministically) so the headline line range is the real one.
        anchor_region = next(
            ((s, e) for f, s, e in occurrences if f == ctx.file_path),
            (block.anchor_start, block.anchor_end),
        )

        # Reject blocks with nothing to extract (imports, parameter lists, field
        # runs). Read off the anchor region, the one occurrence whose source this
        # pass holds; the block is identical across sites by definition, so one
        # read decides it for all of them. No source threaded (non-clone file, an
        # unreadable one) means no evidence to reject on, and the gate abstains
        # rather than guessing.
        region_lines = self._region_lines(ctx, anchor_region)
        if region_lines and _is_declaration_only(region_lines):
            return None

        impact = self._impact_for_block(anchor_region, impact_lookup)
        is_intra = len(occ_files) == 1

        suggested_site = self._suggested_site(occ_files)
        snippet, snippet_start, snippet_truncated = self._snippet_for(ctx, anchor_region)
        plan = {
            "occurrences": [{"file": f, "line_start": s, "line_end": e} for f, s, e in occurrences],
            "suggested_site": suggested_site,
            "duplicated_lines": duplicated_lines,
            "snippet": snippet,
            "snippet_start_line": snippet_start,
            "snippet_truncated": snippet_truncated,
            "suggested_name": _suggested_name(suggested_site),
        }
        evidence = {
            "occurrence_count": len(occurrences),
            "duplicated_lines": duplicated_lines,
            "token_count": block.token_count,
            "co_change_count": block.co_change,
            "is_intra_file": is_intra,
        }
        other_files = [f for f in occ_files if f != ctx.file_path]
        blast_radius = {
            "files": other_files,
            "file_count": len(other_files),
            "co_change_count": block.co_change,
        }
        basename = ctx.file_path.replace("\\", "/").rsplit("/", 1)[-1]
        return RefactoringSuggestion(
            refactoring_type=self.name,
            file_path=ctx.file_path,
            target_symbol=f"{basename}:{anchor_region[0]}-{anchor_region[1]}",
            line_start=anchor_region[0],
            line_end=anchor_region[1],
            plan=plan,
            evidence=evidence,
            impact_delta=round(float(impact), 3),
            effort_bucket=effort_bucket(duplicated_lines),
            blast_radius=blast_radius,
            confidence="high" if block.co_change >= ACTIVE_CO_CHANGE else "medium",
            source_biomarker=_SOURCE_BIOMARKER,
        )

    @staticmethod
    def _suggested_site(occ_files: list[str]) -> dict[str, str | None]:
        """Where the shared helper should live: the deepest directory every
        occurrence shares.

        One namespace, and it is the filesystem — because the field's job is to
        name a place a file can go. This used to lead with a graph *community*
        label under the key ``module``, alongside the directory, and that was
        wrong twice over:

        - **A label is not a location.** Censused over the 963 stored
          ``extract_helper`` plans on this repo's own index, 905 carried a
          label and **905 of 905 named a directory that no occurrence lives
          in** — ``module: "ui"`` for a block shared by ``packages/api-client``,
          ``packages/types`` and ``packages/ui``. Acting on it files shared code
          into a package two thirds of its callers are not in.
        - **The namespace depended on the writer.** ``community_label_map`` is populated
          only by the full-index path; the incremental, re-score and
          ``repowise health`` paths leave it empty, so the same clone got
          ``"ui"`` or ``None`` depending on which pass last wrote the row. That
          is exactly the defect the ``module`` column was fixed for; the
          refactoring payload had kept it.

        The honest answer is sometimes a shallow one — a block shared across
        three packages has no home better than ``packages`` — and saying so is
        the point. A nicer-reading label that names the wrong package is worse
        advice, not better.

        Deliberately *not* ``package_roots.module_for``, the shipped definition
        of the ``module`` column: that answers "which bucket does this file
        report under", and for placement the shared directory is strictly more
        specific (for a block confined to ``.../providers/llm`` it is that
        directory, where ``module_for`` would say ``packages/core``). Package
        attribution is a rollup axis; this is a destination.
        """
        return {"directory": _common_directory(occ_files)}

    @staticmethod
    def _region_lines(ctx: RefactoringContext, region: tuple[int, int]) -> list[str]:
        """The anchor file's source for *region*, or ``[]`` when none is
        threaded. Clone ranges are 1-indexed and inclusive; the range is clamped
        to the file because a region can outlive the read that produced it."""
        lines = ctx.source_lines
        if not lines:
            return []
        lo = max(region[0], 1)
        hi = min(region[1], len(lines))
        if hi < lo:
            return []
        return list(lines[lo - 1 : hi])

    @staticmethod
    def _snippet_for(
        ctx: RefactoringContext, anchor_region: tuple[int, int]
    ) -> tuple[str | None, int | None, bool]:
        """The duplicated block's source text, read from the anchor file.

        The block is identical across every site by definition (it *is* the
        clone), so it is stored once, taken from the anchor region on this file
        (the file the suggestion is emitted from). Clipped at
        ``_MAX_SNIPPET_LINES`` with a flag when it overran. Returns
        ``(None, None, False)`` when no source was threaded (non-clone file, or
        an unreadable one), leaving the plan on its line ranges alone.
        """
        block = ExtractHelperDetector._region_lines(ctx, anchor_region)
        if not block:
            return None, None, False
        lo = max(anchor_region[0], 1)
        truncated = len(block) > _MAX_SNIPPET_LINES
        if truncated:
            block = block[:_MAX_SNIPPET_LINES]
        return "\n".join(block), lo, truncated

    @staticmethod
    def _impact_for_dry_violation(ctx: RefactoringContext) -> list[tuple[int, int, float]]:
        """The file's ``dry_violation`` findings as (line_start, line_end,
        impact) so a block can claim the impact of the clone it overlaps."""
        out: list[tuple[int, int, float]] = []
        for f in ctx.findings:
            if getattr(f, "biomarker_type", "") != _SOURCE_BIOMARKER:
                continue
            start = getattr(f, "line_start", None)
            end = getattr(f, "line_end", None)
            impact = float(getattr(f, "health_impact", 0.0) or 0.0)
            if start is None or end is None:
                # No region — attribute to the whole file (matches any block).
                out.append((0, 1_000_000_000, impact))
            else:
                out.append((int(start), int(end), impact))
        return out

    @staticmethod
    def _impact_for_block(
        region: tuple[int, int], impact_lookup: list[tuple[int, int, float]]
    ) -> float:
        """Recovered impact for the block whose anchor region overlaps a
        ``dry_violation`` finding; 0 when none does (precision-first)."""
        start, end = region
        best = 0.0
        for f_start, f_end, impact in impact_lookup:
            if start <= f_end and end >= f_start and impact > best:
                best = impact
        return best


def _merge_ranges_per_file(
    occurrences: list[tuple[str, int, int]],
) -> list[tuple[str, int, int]]:
    """Collapse overlapping/adjacent line ranges within each file into one
    region. The clone detector emits a block as several offset windows
    (8-35, 8-36, 9-36, 22-28 …); left unmerged those read as separate sites,
    so a single duplicated block looks far more spread out than it is.

    Returns the merged ``(file, start, end)`` tuples, sorted — so the output
    is deterministic regardless of the input order.
    """
    by_file: dict[str, list[tuple[int, int]]] = {}
    for f, start, end in occurrences:
        by_file.setdefault(f, []).append((start, end))
    out: list[tuple[str, int, int]] = []
    for f, ranges in by_file.items():
        cur_start, cur_end = None, None
        for start, end in sorted(ranges):
            if cur_start is None:
                cur_start, cur_end = start, end
            elif start <= cur_end + _REGION_SLACK:
                cur_end = max(cur_end, end)
            else:
                out.append((f, cur_start, cur_end))
                cur_start, cur_end = start, end
        if cur_start is not None:
            out.append((f, cur_start, cur_end))
    return sorted(out)


def _suggested_name(suggested_site: dict[str, str | None]) -> str | None:
    """Always ``None``: no fact this detector holds names the block.

    The name used to be the shared directory's leaf plus ``_helper``, which
    says where the helper would live and nothing about what it does, and
    collides by construction -- six plans on this repo's index were all
    ``persistence_helper``. Naming the block needs its semantics, which is an
    opt-in LLM pass, not an index-time guess. Kept as a function, taking the
    site it used to read, so the one call site stays a named decision rather
    than a bare ``None`` literal in the plan dict.
    """
    return None


def _common_directory(paths: list[str]) -> str | None:
    """The directory every occurrence shares *and* at least one lives in.

    A shared prefix alone is not a site: occurrences in ``packages/core/...``
    and ``packages/ui/...`` share ``packages``, a container holding no module,
    and proposing a helper there names a place nothing could go. Requiring one
    occurrence directly in the directory keeps the honest answer (they really
    are siblings) and returns ``None`` for the rest.
    """
    seg_lists = [p.replace("\\", "/").split("/")[:-1] for p in paths]
    if not seg_lists or any(not segs for segs in seg_lists):
        return None
    common: list[str] = []
    for parts in zip(*seg_lists, strict=False):
        if len(set(parts)) == 1:
            common.append(parts[0])
        else:
            break
    shared = "/".join(common)
    if not shared or not any(len(segs) == len(common) for segs in seg_lists):
        return None
    return shared
