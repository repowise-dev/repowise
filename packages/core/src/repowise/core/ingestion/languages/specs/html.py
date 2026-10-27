"""LanguageSpec for html — an import-tier language, deliberately.

HTML has no functions, classes or calls, so there is nothing for a symbol
extractor to find and no ``scm_file`` to point at. What it *does* carry is
file-level dependency edges: ``<script src>`` and ``<link href>``. Those are
attribute reads, so they are extracted by ``lightweight_imports/html.py`` and
resolved by ``resolvers/html.py``; the file lands on ``parse_file``'s
no-``LanguageConfig`` path, which yields an empty symbol list and real
``Import`` entries. That is the whole tier, and ``import_support="partial"``
says so.

No SFC projection. Vue and Svelte earn one because a ``<script>`` block is
where the component lives; a plain ``.html`` file's inline script almost never
carries a module import — 13 of the 6162 ``.html`` files in the validation
corpus (0.2%). Projecting for that would buy a rounding error and would mint
symbols, contradicting the import-only tier. So HTML registers no
``sfc_source`` locator and no synthetic component symbol.

``is_code=False`` is both truthful and load-bearing. HTML is markup, like
``xaml`` and ``markdown`` — and the classification puts it in dead code's
``_NON_CODE_LANGUAGES``, so an ``.html`` file is never *reported* as dead while
its outbound edges still anchor everything it references. That is the correct
semantics twice over: whether a page is reachable is not statically decidable
(a server serves it, a human navigates to it, a build copies it), and checked-in
generated HTML is everywhere — 4204 of the corpus's 6162 files are one
repository's committed Dokka API docs. Exempting the language structurally
beats chasing those with never-flag globs.

Known gap, and it is the big one: template dialects. Django/Jinja, Go
templates, ERB, Handlebars, Blade, Thymeleaf and Angular's ``*ngIf`` are
invisible to an HTML parser — ``{% extends "base.html" %}`` is plain text, so
such a file parses cleanly and yields nothing. That is 44% of the corpus once
the generated Dokka tree is set aside (843 of 1931 files). Covering it needs a
per-dialect regex tier gated on a framework manifest, which is a different
mechanism with its own precision questions.
"""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="html",
    display_name="HTML",
    extensions=frozenset({".html", ".htm"}),
    # Markup, not code: no symbols exist to extract, and this is what makes the
    # language dead-code exempt. See the module docstring.
    is_code=False,
    is_passthrough=True,
    # A dedicated resolver for src/href with one major known gap (template
    # dialects). Not "full" — that would overclaim on 44% of real-world .html.
    import_support="partial",
    # The entry of every Vite/webpack/Parcel SPA: index.html is what references
    # src/main.ts, and nothing imports index.html in turn. Marking it an entry
    # point is what lets that edge anchor the app's reachability.
    entry_point_patterns=("index.html",),
    # Build output that is routinely committed. These carry no hand-written
    # dependency information and would only add noise to the graph.
    blocked_dirs=("node_modules", "dist", "build", "_site", ".next", "htmlcov"),
    color_hex="#E34F26",
)
