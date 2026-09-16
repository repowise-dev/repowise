"""Resolution of extracted references against the real repository tree.

Four verdicts, not two. ``UNCHECKABLE`` and ``AMBIGUOUS`` are what separate a
detector from a noise generator, and each exists because Phase 1 measured the
cost of collapsing it into ``MISSING``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from repowise.core.ingestion.special_handlers import iter_make_targets

from .constants import ORIGIN_CONFIDENCE
from .models import DocReference, DriftKind, DriftVerdict
from .renderer import RendererMap, github_slug

_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$")

# Setext headings. ``Title`` over ``=====`` or ``-----`` is a heading, and
# GitHub gives it an anchor exactly as it does an ATX one; recognising only
# ``#`` reports every inbound link to a setext heading as broken.
_SETEXT_UNDERLINE_RE = re.compile(r"^(?:=+|-{2,})\s*$")

# Explicit anchors. A document that writes an <a id="..."> above a section is
# declaring a link target that no heading slug reproduces, and a checker that
# reads only headings reports every one of them as broken. This was the single
# largest false-positive family in Phase 1's first run.
#
# Any element, not just ``<a>``, and an unquoted value too: GitHub honours
# ``<div id="x">`` and ``<a href="..." id="x">`` as link targets, and requiring
# ``id`` to follow ``<a`` immediately missed both.
_HTML_ANCHOR_RE = re.compile(
    r"<[a-z][a-z0-9]*\s[^>]*?\b(?:id|name)\s*=\s*(?:\"([^\"]+)\"|'([^']+)'|([^\s>]+))",
    re.I,
)

_PARENT_SEGMENT_RE = re.compile(r"[^/]+/\.\./")


@dataclass
class Resolution:
    """One reference's verdict, plus why."""

    ref: DocReference
    verdict: DriftVerdict
    detail: str = ""
    origin: str = ""
    """Set only on ``MISSING``; names the strategy that concluded it, which is
    what carries the confidence."""

    @property
    def confidence(self) -> float:
        return ORIGIN_CONFIDENCE.get(self.origin, 0.0)


@dataclass
class RepoIndex:
    """Everything the resolver needs to know about the tree, built once.

    Built from bytes the pipeline already decoded rather than from disk: this
    joins the index pipeline and runs on every ``init`` and every ``update``,
    so a second pass over the tree would be a real regression.
    """

    files: frozenset[str] = frozenset()
    dirs: frozenset[str] = frozenset()
    top_level_dirs: frozenset[str] = frozenset()
    indexed_suffixes: frozenset[str] = frozenset()
    """Every file extension the index actually holds.

    The index is not the repository. The traverser keeps only files it can
    identify, so assets and data files --- ``.png``, ``.css``, ``.txt``,
    ``.lock``, ``.svg`` --- never reach it, and ``--skip-tests`` removes more.
    Resolving a reference to one of those against this index would find nothing
    and call the document wrong, when the truth is that the index cannot speak
    to it. A repository with no file of a given kind cannot be evidence about
    files of that kind.
    """
    by_basename: dict[str, list[str]] = field(default_factory=dict)
    make_targets: frozenset[str] = frozenset()
    npm_scripts: frozenset[str] = frozenset()
    renderers: RendererMap = field(default_factory=lambda: RendererMap([]))
    _doc_anchors: dict[str, frozenset[str]] = field(default_factory=dict, repr=False)
    _doc_text: dict[str, str] = field(default_factory=dict, repr=False)

    @classmethod
    def build(
        cls,
        tracked_paths: frozenset[str] | set[str],
        doc_text: dict[str, str],
        manifest_text: dict[str, str] | None = None,
    ) -> RepoIndex:
        """Compose the lookup tables.

        Args:
            tracked_paths: every repo-relative path the index knows about.
            doc_text: decoded markdown, keyed by path. Anchor targets are read
                from here, so a link into a document outside this map is
                uncheckable rather than missing.
            manifest_text: decoded ``Makefile`` / ``package.json`` contents,
                keyed by path. Absent means the command class stands down.
        """
        files = frozenset(tracked_paths)
        dirs: set[str] = set()
        by_basename: dict[str, list[str]] = {}
        for rel in files:
            by_basename.setdefault(rel.rsplit("/", 1)[-1], []).append(rel)
            parts = rel.split("/")
            for i in range(1, len(parts)):
                dirs.add("/".join(parts[:i]))

        make_targets, npm_scripts = _scan_manifests(manifest_text or {})
        return cls(
            files=files,
            dirs=frozenset(dirs),
            top_level_dirs=frozenset(f.split("/")[0] for f in files if "/" in f),
            indexed_suffixes=frozenset(_suffix(f) for f in files) - {""},
            by_basename=by_basename,
            make_targets=make_targets,
            npm_scripts=npm_scripts,
            renderers=RendererMap.detect(files),
            _doc_text=doc_text,
        )

    def doc_anchors(self, rel: str) -> frozenset[str]:
        """Every anchor *rel* declares: heading slugs plus explicit HTML ids.

        Read through :func:`~.extractor.prose_lines`, the same filter the
        extraction side uses. Scanning the raw text instead made the two halves
        disagree: a ``# Install dependencies`` comment inside a shell fence
        registered as a declared anchor, while a reference on the same line
        would have been skipped. That asymmetry only ever suppresses findings,
        but it suppresses them for a reason no reader could reconstruct.
        """
        cached = self._doc_anchors.get(rel)
        if cached is not None:
            return cached

        from .extractor import prose_lines

        found: set[str] = set()
        previous = ""
        for _lineno, line in prose_lines(self._doc_text.get(rel, "")):
            m = _HEADING_RE.match(line)
            if m:
                found.add(github_slug(m.group(1)))
            elif previous.strip() and _SETEXT_UNDERLINE_RE.match(line):
                found.add(github_slug(previous))
            for groups in _HTML_ANCHOR_RE.findall(line):
                value = next((g for g in groups if g), "")
                found.add(value.strip().lower())
            previous = line

        found.discard("")
        frozen = frozenset(found)
        self._doc_anchors[rel] = frozen
        return frozen


def _suffix(path: str) -> str:
    """The lowercased extension of *path*, or ``""`` when it has none."""
    base = path.rsplit("/", 1)[-1]
    dot = base.rfind(".")
    return base[dot:].lower() if dot > 0 else ""


def _scan_manifests(manifest_text: dict[str, str]) -> tuple[frozenset[str], frozenset[str]]:
    make_targets: set[str] = set()
    npm_scripts: set[str] = set()
    for rel, text in manifest_text.items():
        name = rel.rsplit("/", 1)[-1]
        if name in ("Makefile", "makefile", "GNUmakefile"):
            # The ingestion handler already owns this vocabulary, and its
            # character class admits ``/`` --- so a documented
            # ``make docs/build`` resolves instead of being reported as a
            # missing target at 0.85 confidence.
            make_targets.update(target for target, _ in iter_make_targets(text))
        elif name == "package.json":
            try:
                scripts = json.loads(text).get("scripts") or {}
            except (json.JSONDecodeError, AttributeError, TypeError):
                continue
            if isinstance(scripts, dict):
                npm_scripts.update(scripts)
    return frozenset(make_targets), frozenset(npm_scripts)


# ---------------------------------------------------------------------------
# Per-class resolution
# ---------------------------------------------------------------------------


def _join_relative(doc: str, target: str) -> str:
    """Resolve *target* as written relative to the document that names it."""
    doc_dir = doc.rsplit("/", 1)[0] if "/" in doc else ""
    if not doc_dir:
        return target
    joined = f"{doc_dir}/{target}"
    # Collapse "a/b/../c" without touching the filesystem.
    while True:
        collapsed = _PARENT_SEGMENT_RE.sub("", joined, count=1)
        if collapsed == joined:
            break
        joined = collapsed
    return joined.lstrip("./")


def _exists(idx: RepoIndex, target: str) -> bool:
    """Whether *target* names a file or directory the index holds.

    A trailing slash is how a document says "directory", and it is kept through
    normalization so the reference reads back as written --- but the directory
    set is built from path prefixes, which carry no slash. Comparing the two
    forms directly reported every correctly-written directory reference in this
    repository as drift.
    """
    if target in idx.files or target in idx.dirs:
        return True
    trimmed = target.rstrip("/")
    return bool(trimmed) and (trimmed in idx.files or trimmed in idx.dirs)


def _resolve_path(idx: RepoIndex, ref: DocReference, is_guide: bool) -> Resolution:
    target = ref.target
    if _exists(idx, target):
        return Resolution(ref, DriftVerdict.RESOLVED, "exact")

    relative = _join_relative(ref.doc_path, target)
    if relative != target and _exists(idx, relative):
        return Resolution(ref, DriftVerdict.RESOLVED, "relative-to-doc")

    # Anchoring. A reference is evidence about THIS repository only when it
    # carries a separator and its first segment names a directory the
    # repository actually has. Everything else is a file Repowise writes into
    # the user's tree (CLAUDE.md, config.yaml, state.json), a file belonging to
    # a framework the docs merely describe (routes/web.php, composer.json), or
    # an invented example (login.py). Phase 1's first run flagged all three
    # families; they are 100% false positives, and no amount of resolution
    # effort fixes them, because the target was never supposed to exist here.
    # This single rule took the path class from a 49% flag rate to 1.3%.
    if "/" not in target or target.split("/")[0] not in idx.top_level_dirs:
        return Resolution(ref, DriftVerdict.UNCHECKABLE, "unanchored")

    # A basename match is not a resolution and is not drift either. Phase 1
    # found five of these; reporting them as missing would have been wrong.
    candidates = idx.by_basename.get(target.rsplit("/", 1)[-1], [])
    if len(candidates) == 1:
        return Resolution(ref, DriftVerdict.AMBIGUOUS, f"basename-only:{candidates[0]}")
    if len(candidates) > 1:
        return Resolution(ref, DriftVerdict.AMBIGUOUS, f"basename-multi:{len(candidates)}")

    # The index is not the repository, so its silence is only evidence about
    # kinds of file it actually carries. The traverser drops every extension it
    # cannot identify --- assets, lockfiles, plain text --- and ``--skip-tests``
    # drops more, so a reference to one of those resolves against a tree that
    # structurally could not contain it. Reporting that as drift blames the
    # document for the indexer's scope.
    suffix = _suffix(target)
    if suffix and suffix not in idx.indexed_suffixes:
        return Resolution(ref, DriftVerdict.UNCHECKABLE, f"unindexed-kind:{suffix}")

    origin = "path_no_candidate_in_guide" if is_guide else "path_no_candidate"
    return Resolution(ref, DriftVerdict.MISSING, "no-candidate", origin)


def _resolve_anchor(idx: RepoIndex, ref: DocReference) -> Resolution:
    target, _, frag = ref.target.partition("#")

    # Joined relative to the referring document FIRST, because that is what a
    # markdown link means: every renderer resolves ``[g](guide.md#setup)`` in
    # ``docs/a.md`` as ``docs/guide.md``. Preferring a verbatim root-relative
    # hit read the wrong file whenever two documents shared a basename, and
    # then reported the correct link as a renamed heading at 0.95 --- the
    # highest confidence this detector issues.
    if target:
        relative = _join_relative(ref.doc_path, target)
        if relative in idx.files:
            target = relative

    # Gated on the renderer of the document that DECLARES the anchors, because
    # that is what decides the slug. Never guess an unimplemented renderer's
    # algorithm: fastapi produced 93 findings that way, all false.
    if not idx.renderers.checkable(target or ref.doc_path):
        renderer = idx.renderers.for_document(target or ref.doc_path)
        return Resolution(ref, DriftVerdict.UNCHECKABLE, f"renderer:{renderer}")

    if target not in idx.files:
        # The document itself is missing. The LINK row already says so; saying
        # it twice would double-count one defect.
        return Resolution(ref, DriftVerdict.UNCHECKABLE, "host-doc-unresolved")
    if target not in idx._doc_text:
        return Resolution(ref, DriftVerdict.UNCHECKABLE, "host-doc-unread")
    if github_slug(frag) in idx.doc_anchors(target):
        return Resolution(ref, DriftVerdict.RESOLVED, "heading")
    return Resolution(ref, DriftVerdict.MISSING, "no-heading", "anchor_no_heading")


def _resolve_command(idx: RepoIndex, ref: DocReference) -> Resolution:
    runner, _, name = ref.target.partition(":")
    if runner == "make":
        if not idx.make_targets:
            return Resolution(ref, DriftVerdict.UNCHECKABLE, "no-makefile")
        found = name in idx.make_targets
    else:
        if not idx.npm_scripts:
            return Resolution(ref, DriftVerdict.UNCHECKABLE, "no-package-json")
        found = name in idx.npm_scripts
    if found:
        return Resolution(ref, DriftVerdict.RESOLVED, f"{runner}-target")
    return Resolution(ref, DriftVerdict.MISSING, f"{runner}-target", "command_no_target")


def resolve(idx: RepoIndex, ref: DocReference, *, is_guide: bool = False) -> Resolution:
    """Answer one reference against the tree."""
    if ref.kind in (DriftKind.PATH, DriftKind.LINK):
        return _resolve_path(idx, ref, is_guide)
    if ref.kind is DriftKind.ANCHOR:
        return _resolve_anchor(idx, ref)
    if ref.kind is DriftKind.COMMAND:
        return _resolve_command(idx, ref)
    return Resolution(ref, DriftVerdict.UNCHECKABLE, "unknown-kind")
