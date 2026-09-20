"""Which renderer turns a given document's headings into anchors.

Anchor checking is the one class that does not generalize. A heading's anchor
is whatever the *renderer* decides it is, and renderers disagree. Phase 1 ran a
GitHub slug algorithm over fastapi and produced 93 anchor findings, every one
of them false: fastapi's docs are MkDocs, which slugs differently. On this
repository the same algorithm was 3/3 correct, because GitHub is what actually
renders those files.

So the class is gated. If a document is published through a renderer whose slug
algorithm this module does not implement, the anchor class stands down for that
document and reports ``uncheckable`` rather than guessing.

The gate is **per subtree, not per repository.** This repository is why: it
ships a Jekyll site at ``website/_config.yml`` that publishes the fifteen
documents under ``website/`` and nothing else. The ``docs/`` tree and
``.github/CONTRIBUTING.md`` beside it are read on GitHub. A repo-global gate
reads one marker file and stands the whole class down, losing every real
finding outside the site directory --- which is exactly what happened the first
time this ran. A marker governs the directory that contains it, and documents
outside every marked subtree are rendered by the forge.

Adding MkDocs or Docusaurus support later means implementing its slug function
here and dropping its tag from :data:`UNSUPPORTED_RENDERERS`. No other module
changes.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

#: Renderer whose slug algorithm is implemented and measured.
GITHUB = "github"

#: Renderers this module can *detect* but cannot slug for. Detecting one is
#: what makes the anchor class stand down for the documents it governs; the
#: alternative is fastapi's 93 false findings.
UNSUPPORTED_RENDERERS: frozenset[str] = frozenset({"mkdocs", "docusaurus", "jekyll"})

#: Marker filename -> renderer tag. A marker governs the directory it sits in
#: and everything beneath it.
_MARKERS: dict[str, str] = {
    "mkdocs.yml": "mkdocs",
    "mkdocs.yaml": "mkdocs",
    "docusaurus.config.js": "docusaurus",
    "docusaurus.config.ts": "docusaurus",
    "docusaurus.config.mjs": "docusaurus",
    "_config.yml": "jekyll",
}


class RendererMap:
    """Which renderer publishes each document.

    Built once per run from the tracked paths; queried per document.
    """

    __slots__ = ("_zones",)

    def __init__(self, zones: list[tuple[str, str]]) -> None:
        #: ``(prefix, renderer)`` ordered longest-prefix first, so a site
        #: nested inside another site wins for its own documents.
        self._zones = zones

    @classmethod
    def detect(cls, tracked_paths: Iterable[str]) -> RendererMap:
        """Find every renderer marker in the tree and the subtree it governs.

        A marker buried in ``node_modules`` or a test fixture says nothing
        about how this project publishes its own prose, so only markers
        outside those trees count. Import is local to avoid a cycle:
        :mod:`.constants` does not depend on this module.
        """
        from repowise.core.test_paths import is_test_related_path

        from .constants import VENDORED_DIR_PARTS

        zones: list[tuple[str, str]] = []
        for path in tracked_paths:
            prefix, _, name = path.rpartition("/")
            renderer = _MARKERS.get(name)
            if renderer is None:
                continue
            if prefix and set(prefix.split("/")) & VENDORED_DIR_PARTS:
                continue
            if is_test_related_path(path):
                continue
            zones.append((f"{prefix}/" if prefix else "", renderer))
        # Longest prefix first. The repo root ("") sorts last and so acts as
        # the fallback zone when a site is configured at the root.
        zones.sort(key=lambda z: len(z[0]), reverse=True)
        return cls(zones)

    def for_document(self, doc_path: str) -> str:
        """Name the renderer that publishes *doc_path*.

        Defaults to :data:`GITHUB`, which is the honest answer for a document
        no site generator picks up: it is read on the forge.
        """
        for prefix, renderer in self._zones:
            if not prefix or doc_path.startswith(prefix):
                return renderer
        return GITHUB

    def checkable(self, doc_path: str) -> bool:
        """Whether the anchor class may produce findings about *doc_path*."""
        return self.for_document(doc_path) == GITHUB

    def summary(self) -> str:
        """A short label for the report, naming what was detected.

        ``"github"`` when nothing else was found, otherwise the detected
        renderers and the subtrees they cover, so a reader can see why an
        anchor finding they expected is absent.
        """
        if not self._zones:
            return GITHUB
        return ",".join(f"{r}:{p or '/'}" for p, r in self._zones)


# ---------------------------------------------------------------------------
# GitHub's slug algorithm
# ---------------------------------------------------------------------------

_MARKUP_RE = re.compile(r"[`*~\[\]()]")
_NON_WORD_RE = re.compile(r"[^\w\s-]")
_WHITESPACE_RE = re.compile(r"\s")

# A heading is slugged from its RENDERED text, so these have to be reduced to
# their visible part before anything else runs. Stripping only the punctuation
# keeps the invisible half: ``## [Contributing](CONTRIBUTING.md)`` slugged to
# ``contributingcontributingmd`` where GitHub produces ``contributing``, and
# ``## <code>foo</code>`` to ``codefoocode`` rather than ``foo``. Both shapes
# are ordinary in a README, and both produced findings at the highest
# confidence this detector issues.
_MD_LINK_TEXT_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_HTML_TAG_RE = re.compile(r"<[^>]+>")

#: Splits a heading on inline code spans, so their contents land on the odd
#: indices and can be passed through untouched.
_CODE_SPAN_RE = re.compile(r"`([^`]*)`")


def github_slug(heading: str) -> str:
    """GitHub's heading-anchor slug for *heading*.

    Each whitespace character becomes its own dash. Collapsing runs instead
    turns ``SQL + dbt`` into ``sql-dbt`` where GitHub produces ``sql--dbt``,
    because the ``+`` is stripped and the two spaces around it each survive as
    a dash. That one character of regex --- ``\\s`` rather than ``\\s+`` ---
    accounted for 70% of this class's findings in Phase 1's first run.
    """
    # Rendered text first, but ONLY outside inline code spans. Inside a span
    # the angle brackets are literal: ``### `repowise distill <command>` ``
    # renders the word "command" and GitHub slugs it in, so treating it as a
    # tag to strip invents a broken link out of a correct one. Four of this
    # repository's own CLI-reference links were reported that way.
    parts = _CODE_SPAN_RE.split(heading.strip())
    rendered: list[str] = []
    for i, part in enumerate(parts):
        if i % 2:  # odd indices are the contents of a code span
            rendered.append(part)
            continue
        part = _MD_IMAGE_RE.sub("", part)
        part = _MD_LINK_TEXT_RE.sub(r"\1", part)
        rendered.append(_HTML_TAG_RE.sub("", part))
    s = "".join(rendered).lower()
    s = _MARKUP_RE.sub("", s)
    s = _NON_WORD_RE.sub("", s)
    return _WHITESPACE_RE.sub("-", s).strip("-")
