"""HTML import extraction and asset resolution.

The tier is deliberately narrow — ``<script src>`` and ``<link href>``, no
symbols — so these tests pin both what it captures and what it refuses to.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import networkx as nx
import pytest

from repowise.core.ingestion.languages.registry import REGISTRY
from repowise.core.ingestion.lightweight_imports import extract_lightweight_imports
from repowise.core.ingestion.lightweight_imports.html import extract_html_imports
from repowise.core.ingestion.models import EXTENSION_TO_LANGUAGE, FileInfo
from repowise.core.ingestion.resolvers import resolve_import
from repowise.core.ingestion.resolvers.context import ResolverContext


def _modules(source: str) -> list[str]:
    return [i.module_path for i in extract_html_imports(source)]


def _ctx(paths: set[str]) -> ResolverContext:
    return ResolverContext(path_set=paths, stem_map={}, graph=nx.DiGraph())


def _resolve(module: str, importer: str, paths: set[str]) -> str | None:
    return resolve_import(module, importer, "html", _ctx(paths))


class TestExtraction:
    def test_script_src_and_link_href(self) -> None:
        assert _modules(
            '<link rel="stylesheet" href="site.css">\n<script src="app.js"></script>'
        ) == ["site.css", "app.js"]

    def test_unquoted_attribute_value(self) -> None:
        """A regex keyed on quotes would miss this; the grammar does not."""
        assert _modules("<script src=app.js></script>") == ["app.js"]

    def test_attribute_split_across_lines(self) -> None:
        assert _modules('<script\n  type="module"\n  src="m.js"></script>') == ["m.js"]

    def test_single_quoted_value(self) -> None:
        assert _modules("<script src='app.js'></script>") == ["app.js"]

    def test_commented_out_reference_is_not_an_import(self) -> None:
        assert _modules('<!-- <script src="old.js"></script> -->') == []

    def test_inline_script_without_src_yields_nothing(self) -> None:
        assert _modules('<script>import x from "./m.js";</script>') == []

    def test_anchor_and_image_are_not_dependencies(self) -> None:
        assert _modules('<a href="p.html">x</a><img src="logo.png">') == []

    def test_duplicate_reference_recorded_once(self) -> None:
        assert _modules('<script src="a.js"></script><script src="a.js"></script>') == ["a.js"]

    def test_empty_value_is_skipped(self) -> None:
        assert _modules('<script src=""></script>') == []

    @pytest.mark.parametrize(
        "ref",
        [
            "//cdn.example.com/x.js",
            "https://example.com/x.js",
            "http://example.com/x.js",
            "data:text/javascript,void%200",
            "#anchor",
            "mailto:a@b.c",
            "javascript:void(0)",
        ],
    )
    def test_external_reference_is_dropped(self, ref: str) -> None:
        assert _modules(f'<script src="{ref}"></script>') == []

    @pytest.mark.parametrize(
        "ref",
        [
            "{{ url_for('static', filename='x.js') }}",
            "{% static 'x.js' %}",
            "<%= asset_path('x.js') %>",
            "${baseUrl}/x.js",
        ],
    )
    def test_template_expression_is_not_a_static_path(self, ref: str) -> None:
        assert _modules(f'<script src="{ref}"></script>') == []

    def test_is_relative_distinguishes_root_from_document(self) -> None:
        imports = extract_html_imports(
            '<script src="/static/a.js"></script><script src="./b.js"></script>'
        )
        assert [(i.module_path, i.is_relative) for i in imports] == [
            ("/static/a.js", False),
            ("./b.js", True),
        ]

    def test_non_ascii_markup_is_safe(self) -> None:
        assert _modules('<p>héllo — ünicode</p><script src="app.js"></script>') == ["app.js"]

    def test_template_dialect_file_degrades_to_nothing(self) -> None:
        """The declared ceiling, pinned so it cannot regress silently."""
        assert _modules('{% extends "base.html" %}\n{% include "nav.html" %}') == []


class TestResolution:
    def test_document_relative(self) -> None:
        assert _resolve("./app.js", "web/index.html", {"web/app.js"}) == "web/app.js"

    def test_bare_relative(self) -> None:
        assert _resolve("app.js", "web/index.html", {"web/app.js"}) == "web/app.js"

    def test_parent_relative(self) -> None:
        assert _resolve("../shared/x.js", "web/p/i.html", {"web/shared/x.js"}) == "web/shared/x.js"

    def test_root_relative_matches_by_unique_suffix(self) -> None:
        """``/static/js/app.js`` is a *web* root, which is not the repo root."""
        assert (
            _resolve("/static/js/app.js", "templates/i.html", {"myapp/static/js/app.js"})
            == "myapp/static/js/app.js"
        )

    def test_root_relative_anchors_at_the_page_directory_first(self) -> None:
        """The Vite/webpack convention: index.html's own dir is the web root."""
        paths = {"web/ui/index.html", "web/ui/src/main.tsx"}
        assert _resolve("/src/main.tsx", "web/ui/index.html", paths) == "web/ui/src/main.tsx"

    def test_page_directory_anchor_wins_over_an_ambiguous_suffix(self) -> None:
        """Nine files end in /src/main.tsx in the validation corpus, so suffix
        matching alone refuses to guess and the real SPA entry edge is lost."""
        paths = {
            "apps/a/index.html",
            "apps/a/src/main.tsx",
            "apps/b/src/main.tsx",
            "apps/c/src/main.tsx",
        }
        assert _resolve("/src/main.tsx", "apps/a/index.html", paths) == "apps/a/src/main.tsx"

    def test_root_relative_falls_through_to_public_dir(self) -> None:
        """Vite/CRA/Vue CLI copy public/ to the web root verbatim."""
        paths = {"ui/index.html", "ui/public/ico/favicon.png"}
        assert _resolve("/ico/favicon.png", "ui/index.html", paths) == "ui/public/ico/favicon.png"

    def test_page_directory_anchor_beats_public_dir(self) -> None:
        paths = {"ui/index.html", "ui/src/main.tsx", "ui/public/src/main.tsx"}
        assert _resolve("/src/main.tsx", "ui/index.html", paths) == "ui/src/main.tsx"

    def test_page_directory_anchor_never_invents_a_file(self) -> None:
        """Anchoring only accepts a path that actually exists."""
        paths = {"web/ui/index.html", "elsewhere/src/main.tsx"}
        assert _resolve("/src/main.tsx", "web/ui/index.html", paths) == "elsewhere/src/main.tsx"

    def test_root_relative_at_repo_root(self) -> None:
        assert _resolve("/app.js", "index.html", {"app.js"}) == "app.js"

    def test_ambiguous_suffix_yields_no_edge(self) -> None:
        paths = {"a/static/app.js", "b/static/app.js"}
        assert _resolve("/static/app.js", "i.html", paths) is None

    def test_query_string_is_stripped(self) -> None:
        assert _resolve("app.js?v=3", "index.html", {"app.js"}) == "app.js"

    def test_fragment_is_stripped(self) -> None:
        assert _resolve("icons.svg#home", "index.html", {"icons.svg"}) == "icons.svg"

    def test_missing_target_yields_no_edge(self) -> None:
        assert _resolve("./nope.js", "index.html", {"app.js"}) is None

    def test_no_extension_inference(self) -> None:
        """HTML fetches paths literally — ``src="./app"`` is not ``app.js``."""
        assert _resolve("./app", "index.html", {"app.js"}) is None

    def test_no_index_lookup(self) -> None:
        assert _resolve("./lib", "index.html", {"lib/index.js"}) is None

    def test_escaping_the_repo_yields_no_edge(self) -> None:
        assert _resolve("../../etc/passwd", "index.html", {"app.js"}) is None

    def test_directory_reference_yields_no_edge(self) -> None:
        assert _resolve("/static/", "index.html", {"static/app.js"}) is None

    def test_single_segment_relative_miss_is_not_guessed(self) -> None:
        """A bare filename that misses locally is too ambiguous to link."""
        assert _resolve("app.js", "web/i.html", {"other/place/app.js"}) is None


class TestRegistryWiring:
    def test_extensions_map_to_html(self) -> None:
        assert EXTENSION_TO_LANGUAGE[".html"] == "html"
        assert EXTENSION_TO_LANGUAGE[".htm"] == "html"

    def test_tier_is_partial_not_full(self) -> None:
        assert REGISTRY.import_support_for("html") == "partial"

    def test_markup_not_code(self) -> None:
        spec = REGISTRY.get("html")
        assert spec is not None
        assert spec.is_code is False
        assert spec.is_passthrough is True

    def test_never_reported_as_dead_code(self) -> None:
        """Reachability of a page is not statically decidable, and committed
        generated HTML is everywhere — so the language is exempt outright."""
        from repowise.core.analysis.dead_code.constants import _NON_CODE_LANGUAGES

        assert "html" in _NON_CODE_LANGUAGES

    def test_index_html_is_an_entry_point(self) -> None:
        assert "index.html" in REGISTRY.entry_point_names()

    def test_no_sfc_locator_registered(self) -> None:
        """HTML earns no projection: it has no component and no symbols."""
        from repowise.core.ingestion.sfc_source import _LOCATORS, prepare_source

        assert "html" not in _LOCATORS
        source = b'<script src="a.js"></script>'
        assert prepare_source("html", source) == source

    def test_reaches_the_parser_via_the_lightweight_path(self) -> None:
        info = FileInfo(
            path="web/index.html",
            abs_path="/repo/web/index.html",
            language="html",
            size_bytes=0,
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=True,
        )
        imports = extract_lightweight_imports(info, b'<script src="app.js"></script>')
        assert [i.module_path for i in imports] == ["app.js"]

    def test_parse_file_yields_imports_and_no_symbols(self, tmp_path: Path) -> None:
        from repowise.core.ingestion.parser import ASTParser

        info = FileInfo(
            path="index.html",
            abs_path=str(tmp_path / "index.html"),
            language="html",
            size_bytes=0,
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=True,
        )
        parsed = ASTParser().parse_file(info, b'<script type="module" src="/src/main.ts"></script>')
        assert parsed.symbols == []
        assert [i.module_path for i in parsed.imports] == ["/src/main.ts"]
