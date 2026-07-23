"""Renderers that build a page from structure instead of prompting a model.

Two different jobs share this machinery, and telling them apart matters:

**Sole renderers.** ``file_page``, ``symbol_spotlight``, ``api_contract``,
``infra_page``, ``scc_page`` and ``layer_page`` state facts a parser knows
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

from ..models import GENERATION_LEVELS, GeneratedPage, compute_page_id, compute_source_hash
from .helpers import _extract_summary, _now_iso

log = structlog.get_logger(__name__)

# Bumped by hand when a change to the structural renderers improves their
# output without changing any template's bytes: new context fields, a changed
# helper, a reordered section. Template edits are picked up automatically
# (their source is hashed), so this is only for the cases hashing cannot see.
STRUCTURAL_GENERATION_VERSION = "1"

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
    """Render a symbol signature for a table cell without cutting mid-token.

    Signatures are captured across source lines, so collapsing whitespace is
    needed before anything else or the cell fills with runs of indentation.
    When one is still too long we cut back to the last argument boundary, so
    the reader sees a whole parameter list prefix rather than half an
    identifier.
    """
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    head = text[:limit]
    cut = max(head.rfind(", "), head.rfind("("))
    if cut > limit // 3:
        head = head[: cut + 1]
    return head.rstrip().rstrip(",") + " …"


_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

FILE_PAGE_TEMPLATE = "file_page.j2"

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
            template="symbol_spotlight.j2",
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
            # Ranked by how many cross-edges each file carries: the highest
            # is the cheapest place to break the cycle. Computed here rather
            # than in Jinja because the template language makes grouping
            # painful and this is the one genuinely useful thing the page can
            # say that the LLM page says in prose.
            decouple_ranking=_rank_cycle_participants(ctx),
        )

    def _structural_layer_page(self, ctx: Any, title: str) -> GeneratedPage:
        return self._structural_page(
            page_type="layer_page",
            target_path=ctx.layer_id,
            title=title,
            template="layer_page.j2",
            ctx=ctx,
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
