"""Astro end-to-end extraction: symbols, imports, calls, health, dead code."""

from __future__ import annotations

import time
from datetime import datetime

import pytest

from repowise.core.ingestion.models import EXTENSION_TO_LANGUAGE, FileInfo
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.sfc_source import prepare_source, scan

_PAGE = b"""---
import Header from '../components/Header.astro';
import { formatDate } from '../lib/format';

export const prerender = true;

export async function getStaticPaths() {
  return [{ params: { slug: 'a' } }];
}

const title = formatDate(new Date());
---
<html>
  <body>
    <Header title={title} />
    <p>{title}</p>
  </body>
</html>
<script>
  function toggleMenu() {
    document.body.classList.toggle('open');
  }
</script>
<style>
  .open { color: red; }
</style>
"""


def _file(
    path: str = "src/pages/index.astro", size: int = len(_PAGE), language: str = "astro"
) -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=f"/repo/{path}",
        language=language,
        size_bytes=size,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


@pytest.fixture(scope="module")
def parser() -> ASTParser:
    return ASTParser()


@pytest.fixture(scope="module")
def parsed(parser: ASTParser):
    return parser.parse_file(_file(), _PAGE)


def test_astro_extension_is_indexed() -> None:
    assert EXTENSION_TO_LANGUAGE[".astro"] == "astro"


class TestProjection:
    def test_markup_and_style_are_blanked(self) -> None:
        prepared = prepare_source("astro", _PAGE)
        assert b"<html>" not in prepared
        assert b"color: red" not in prepared
        assert b"---" not in prepared

    @pytest.mark.parametrize(
        "opener",
        [
            b"<script is:inline>",
            b'<script type="module">',
            b"<script define:vars={{ a }}>",
            b'<script data-type="x">',
            b"<script define:vars={{ dark: theme.type === 'dark' }}>",
        ],
    )
    def test_every_js_script_variant_is_kept(self, opener: bytes) -> None:
        src = opener + b"\n  const kept = 1;\n</script>\n"
        assert b"const kept = 1;" in prepare_source("astro", src)

    def test_data_and_commented_scripts_are_blanked(self) -> None:
        src = (
            b'<script type="application/ld+json">{"@type": "Thing"}</script>\n'
            b"<!-- <script>const dead = 1;</script> -->\n"
            b"{/* the emitted <script> sits here */}\n"
            b"<style>/* <script>const css = 1;</script> */</style>\n"
        )
        assert prepare_source("astro", src).strip() == b""

    def test_a_markup_glob_or_self_closing_style_hides_no_script(self) -> None:
        src = (
            b'<input accept="audio/*" />\n<style is:global />\n'
            b"<script>\n  function later() {}\n</script>\n"
        )
        assert b"function later() {}" in prepare_source("astro", src)

    @pytest.mark.parametrize(
        "tag",
        [
            b'<div title={"<script>const fake = 1;</script>"} />',
            b'<div title="<script>const fake = 1;</script>">',
            b'<Code code={`<script>const fake = 1;</script>`} lang="html" />',
        ],
    )
    def test_a_script_inside_an_attribute_value_is_not_an_opener(self, tag: bytes) -> None:
        assert b"fake" not in prepare_source("astro", tag)

    def test_a_script_element_inside_a_markup_expression_still_opens(self) -> None:
        src = b"{cond && <script>function g(){}</script>}"
        assert b"function g(){}" in prepare_source("astro", src)


_CARD = b"import Card from './Card.astro';"


# Expected imports and tags are what Astro's compiler (@astrojs/compiler-rs
# 0.4.1) reads from the same bytes; a dotted tag keeps its last segment.
@pytest.mark.parametrize(
    ("src", "imports", "tags"),
    [
        pytest.param(b"---\n" + _CARD + b"\n---\n<Card />", ["./Card.astro"], ["Card"], id="plain"),
        pytest.param(
            b"---\n" + _CARD + b"---\n<Card />", ["./Card.astro"], ["Card"], id="same-line"
        ),
        pytest.param(
            b"---\n" + _CARD + b"\n  ---\n<Card />", ["./Card.astro"], ["Card"], id="indented"
        ),
        pytest.param(
            b"\xef\xbb\xbf---\n" + _CARD + b"\n---\n<Card />", ["./Card.astro"], ["Card"], id="bom"
        ),
        pytest.param(
            b"\n---\n" + _CARD + b"\n---\n<Card />", ["./Card.astro"], ["Card"], id="leading-blank"
        ),
        pytest.param(
            b"---\r\n"
            + _CARD
            + b"\r\n---\r\n<Card />\r\n<script>\r\nconst x = 1;\r\n</script>\r\n",
            ["./Card.astro"],
            ["Card"],
            id="crlf",
        ),
        pytest.param(b"---\n---\n<Card />", [], ["Card"], id="empty"),
        pytest.param(
            b"---\n// ----------\n" + _CARD + b"\n---\n<Card />",
            ["./Card.astro"],
            ["Card"],
            id="divider-comment",
        ),
        pytest.param(
            b"---\n" + _CARD + b"\nconst p: Promise<Post[]> = f();\n---\n<Card />",
            ["./Card.astro"],
            ["Card"],
            id="frontmatter-generic",
        ),
        pytest.param(
            b'<Script src="x"><Card /></Script>', [], ["Script", "Card"], id="pascal-script"
        ),
        pytest.param(b"<script-loader><Card /></script-loader>", [], ["Card"], id="script-dash"),
        pytest.param(
            b'<script type="application/ld+json" set:html={JSON.stringify(xs.map((i) => ({ n: i })))} />'
            b"\n<Card />",
            [],
            ["Card"],
            id="arrow-in-attr",
        ),
        pytest.param(b"<!-- <Card /> -->{/* <Other /> */}<Real />", [], ["Real"], id="comments"),
        pytest.param(b"<Icons.Star />", [], ["Star"], id="dotted"),
        pytest.param(b"<Fragment><Card /></Fragment>", [], ["Card"], id="fragment"),
    ],
)
def test_matches_the_astro_compiler(parser, src: bytes, imports: list, tags: list) -> None:
    result = parser.parse_file(_file(size=len(src)), src)
    assert [i.module_path for i in result.imports] == imports
    assert [name for name, _line in scan("astro", src).component_tags] == tags
    assert result.parse_errors == []
    prepared = prepare_source("astro", src)
    assert (len(prepared), prepared.count(b"\n")) == (len(src), src.count(b"\n"))


# Not in the compiler table above: not yet checked against @astrojs/compiler-rs.
@pytest.mark.parametrize(
    ("src", "tags"),
    [
        pytest.param(b"<Foo-Bar />", ["Foo-Bar"], id="hyphenated"),
        pytest.param(b'<div title="<Card />"></div>', [], id="tag-in-quoted-value"),
    ],
)
def test_a_tag_name_runs_to_its_end_and_a_quoted_value_holds_no_tag(src: bytes, tags: list) -> None:
    assert [name for name, _line in scan("astro", src).component_tags] == tags


def test_an_unterminated_script_runs_to_eof() -> None:
    src = b"<script>\nconst a = 1;\n<Card />"
    assert scan("astro", src).component_tags == ()
    assert b"const a = 1;" in prepare_source("astro", src)


@pytest.mark.parametrize(
    "prefix",
    [
        pytest.param(b'<p>{"<style>"}</p>', id="style-in-expression-string"),
        pytest.param(b'<p>{"<!--"}</p>', id="comment-in-expression-string"),
        pytest.param(b'<a title="<!--" />', id="comment-in-attribute-value"),
        pytest.param(b"{cond && <p>Don't</p>}", id="apostrophe-in-expression-markup"),
        pytest.param(b"{cond && <>Don't</>}", id="apostrophe-in-expression-fragment"),
        pytest.param(b"{cond && <p><>a</> Don't</p>}", id="fragment-in-expression-markup"),
        pytest.param(b'{a ? <p>x</p> : "<style>"}', id="end-tag-returns-to-the-expression"),
        pytest.param(b"{cond && <br>} it's ", id="brace-closes-an-element-left-open"),
        pytest.param(b"{x ? 'a\n", id="stray-quote-ends-at-its-line"),
        pytest.param(b"{a </* <Card /> */ b}", id="comment-after-a-less-than"),
        pytest.param(b'<div title={"}"}>', id="brace-in-an-attribute-string"),
        pytest.param(b"<div a={x /* it's */} b='y'>", id="apostrophe-in-an-attribute-comment"),
    ],
)
def test_an_opener_lookalike_hides_nothing_after_it(prefix: bytes) -> None:
    # On the lookalike's own line: a stray JS quote hides only to its line end.
    src = prefix + b"<Card /><script>function f(){}</script>"
    assert scan("astro", src).component_tags == (("Card", prefix.count(b"\n") + 1),)
    assert b"function f(){}" in prepare_source("astro", src)


def test_an_is_raw_body_is_text_to_its_full_end_tag() -> None:
    src = b"<M.Katex is:raw>\\{ x</M.Katex> and it's <Card />"
    assert [name for name, _line in scan("astro", src).component_tags] == ["Katex", "Card"]


def test_is_raw_in_an_attribute_value_keeps_the_body_markup() -> None:
    src = b'<section title="no is:raw here">\n<Card />\n</section>\n<Other />\n'
    assert scan("astro", src).component_tags == (("Card", 2), ("Other", 4))


def test_an_unclosed_quote_scans_in_linear_time() -> None:
    start = time.perf_counter()
    prepare_source("astro", b'<a "' * 20_000)
    assert time.perf_counter() - start < 1


@pytest.mark.parametrize(
    "src",
    [
        pytest.param(b"<b " * 20_000 + b"{{{", id="unbalanced-brace"),
        pytest.param(b"{/* c */}<Card />\n" * 10_000, id="many-hidden-ranges"),
        pytest.param(b"<Card />\n" * 40_000, id="many-tags"),
        pytest.param(
            b'<script src="./x.ts"' + b" " * 20_000 + b"defer></script>",
            id="whitespace-after-a-lone-src",
        ),
    ],
)
def test_a_large_page_scans_in_linear_time(src: bytes) -> None:
    start = time.perf_counter()
    prepare_source("astro", src)
    assert time.perf_counter() - start < 1


def test_a_script_does_not_run_into_the_frontmatter(parser) -> None:
    # No ``;`` after ``Astro.props``: unfenced, the IIFE reads as a call on it.
    src = b"---\nconst { t } = Astro.props\n---\n<script>(function () { function setTheme() {} })()</script>"
    result = parser.parse_file(_file(size=len(src)), src)
    assert ({s.name for s in result.symbols}, result.calls) == ({"setTheme", "Pages"}, [])


class TestSymbols:
    def test_frontmatter_and_script_symbols_at_their_source_lines(self, parsed) -> None:
        lines = {s.name: s.start_line for s in parsed.symbols if s.name != "Pages"}
        assert lines == {"prerender": 5, "getStaticPaths": 7, "title": 11, "toggleMenu": 20}

    def test_the_file_itself_becomes_a_component_symbol(self, parsed) -> None:
        # ``index`` defers to its directory, as for Vue.
        page = [s for s in parsed.symbols if s.name == "Pages"]
        assert len(page) == 1
        assert page[0].kind == "class"
        assert page[0].start_line == 1

    def test_a_kebab_file_is_named_as_its_tag(self, parser) -> None:
        src = b"<button>Top</button>\n"
        result = parser.parse_file(_file("src/components/back-to-top.astro", len(src)), src)
        assert [s.name for s in result.symbols] == ["BackToTop"]

    def test_frontmatter_exports(self, parsed) -> None:
        assert set(parsed.exports) == {"prerender", "getStaticPaths", "Pages"}

    def test_no_parse_errors_on_a_well_formed_page(self, parsed) -> None:
        assert parsed.parse_errors == []


class TestImports:
    def test_named_bindings_are_extracted(self, parsed) -> None:
        fmt = next(i for i in parsed.imports if i.module_path == "../lib/format")
        assert [b.local_name for b in fmt.bindings] == ["formatDate"]

    def test_markup_script_src_and_link_href_are_extracted(self, parser) -> None:
        src = (
            b'---\nconst title = "Home";\n---\n'
            b'<script src="./analytics.ts"></script>\n'
            b'<script src="./self-closing.ts" />\n'
            b'<link rel="stylesheet" href="../styles/site.css">\n'
            b'<!-- <script src="./ignored-comment.js"></script> -->\n'
            b'{/* <script src="./ignored-jsx-comment.js"></script> */}\n'
            b'<script src="https://cdn.example.com/lib.js"></script>\n'
            b'<Script src="./NotHtmlScript.astro" />\n'
        )
        file_info = _file("src/pages/index.astro", len(src))
        parsed = parser.parse_file(file_info, src)
        module_paths = [i.module_path for i in parsed.imports]
        assert "./analytics.ts" in module_paths
        assert "./self-closing.ts" in module_paths
        assert "../styles/site.css" in module_paths
        assert "./ignored-comment.js" not in module_paths
        assert "./ignored-jsx-comment.js" not in module_paths
        assert "https://cdn.example.com/lib.js" not in module_paths
        assert "./NotHtmlScript.astro" not in module_paths

        # Also verify parse_imports directly matches
        imported = [i.module_path for i in parser.parse_imports(file_info, src)]
        assert "./analytics.ts" in imported
        assert "./self-closing.ts" in imported
        assert "../styles/site.css" in imported

    def test_a_markup_script_makes_a_type_import_edge_a_runtime_one(self, parser) -> None:
        from repowise.core.ingestion import GraphBuilder

        files = {
            "src/pages/index.astro": (
                b'---\nimport type { Theme } from "../scripts/theme.ts";\n---\n'
                b'<script src="../scripts/theme.ts"></script>\n',
                "astro",
            ),
            "src/scripts/theme.ts": (b"export type Theme = 'dark';\n", "typescript"),
        }
        builder = GraphBuilder()
        for path, (src, language) in files.items():
            builder.add_file(parser.parse_file(_file(path, len(src), language), src))
        edge = builder.build()["src/pages/index.astro"]["src/scripts/theme.ts"]
        assert edge["type_only"] is False

    def test_markup_script_extracted_when_frontmatter_and_inline_scripts_contain_comment_or_style(
        self, parser
    ) -> None:
        src = (
            b'---\n'
            b'const note = "<!-- in frontmatter string";\n'
            b'const style = "<style>body{color:red}</style>";\n'
            b'---\n'
            b'<script>\n'
            b'  // Inline script containing tricky comment/style strings\n'
            b'  const x = "<!-- unclosed comment in script";\n'
            b'  const y = "<style>p { font-size: 14px; }</style>";\n'
            b'</script>\n'
            b'<script src="./real-after-inline.ts"></script>\n'
            b'<link rel="stylesheet" href="../styles/site.css">\n'
        )
        file_info = _file("src/pages/index.astro", len(src))
        parsed = parser.parse_file(file_info, src)
        module_paths = [i.module_path for i in parsed.imports]
        assert "./real-after-inline.ts" in module_paths
        assert "../styles/site.css" in module_paths

    def test_multiline_attributes_and_reordered_link_attrs(self, parser) -> None:
        src = (
            b"---\nconst ready = true;\n---\n"
            b"<Header />\n"
            b"<script defer\n"
            b'  src="./analytics.ts"></script>\n'
            b"<link href='../styles/site.css' rel=\"stylesheet\">\n"
            b'<script type="module" src="./module.ts" />\n'
        )
        file_info = _file("src/pages/index.astro", len(src))
        module_paths = [i.module_path for i in parser.parse_file(file_info, src).imports]
        assert module_paths == ["./analytics.ts", "../styles/site.css", "./module.ts"]
        assert [i.module_path for i in parser.parse_imports(file_info, src)] == module_paths

    def test_unterminated_script_keeps_its_src_and_hides_the_rest(self, parser) -> None:
        # An unclosed script body runs to EOF, so a later link is not a markup import.
        src = b'<script src="./kept.ts">\n<link href="./hidden.css">\n<Card />'
        file_info = _file("src/pages/index.astro", len(src))
        module_paths = [i.module_path for i in parser.parse_imports(file_info, src)]
        assert module_paths == ["./kept.ts"]
        assert scan("astro", src).component_tags == ()

    def test_unterminated_style_hides_a_following_script(self, parser) -> None:
        src = b"<style>\nbody { color: red; }\n<script src=\"./hidden.ts\"></script>\n"
        file_info = _file("src/pages/index.astro", len(src))
        assert [i.module_path for i in parser.parse_imports(file_info, src)] == []

    def test_unclosed_attribute_quote_still_reads_the_previous_tag(self, parser) -> None:
        src = b'<script src="./ok.ts"></script>\n<script src="unterminated>\n'
        file_info = _file("src/pages/index.astro", len(src))
        assert [i.module_path for i in parser.parse_imports(file_info, src)] == ["./ok.ts"]

    @pytest.mark.parametrize(
        "markup",
        [
            b"<script src={url}></script>",
            b'<link rel="canonical" href={canonicalURL} />',
            b'<link rel="alternate" href={new URL("rss.xml", Astro.site)} />',
            b"{items.map((i) => <script src={i.src}></script>)}",
            b'<div set:html={`<script src="./c3.ts"></script>`} />',
            b"{show ? \"<script src='./c4.ts'></script>\" : null}",
            b"{\"<link href='./fake.css'>\"}",
            b'{ /* <script src="./c1.ts"></script> */ }',
            b'{\n // <script src="./c2.ts"></script>\n}',
            b'{`<script src="./t.ts"></script>`}',
            b"{'<script src=\"./s.ts\"></script>'}",
            b"{x && <!-- <script src='./h.ts'></script> -->}",
            b'<script data-x={ src = "./no.ts" }></script>',
        ],
    )
    def test_an_expression_value_is_not_an_import(self, parser, markup: bytes) -> None:
        assert parser.parse_imports(_file(size=len(markup)), markup) == []

    @pytest.mark.parametrize(
        ("markup", "path"),
        [
            (b'<script data-x={a > b} src="./a.ts"></script>', "./a.ts"),
            (b'<link data-x={a > b} href="./c.css">', "./c.css"),
            (b'<script define:vars={{ x: a > b }} src="./d.ts"></script>', "./d.ts"),
            (b'<script data-cfg={f({ a: { b: x > 1 } })} src="./e.ts"></script>', "./e.ts"),
        ],
    )
    def test_a_gt_inside_an_earlier_brace_value_keeps_the_path(
        self, parser, markup: bytes, path: str
    ) -> None:
        assert [i.module_path for i in parser.parse_imports(_file(size=len(markup)), markup)] == [
            path
        ]

    def test_src_inside_another_attribute_value_is_not_read(self, parser) -> None:
        src = b'<script title="src=./trap.ts" data-src="./no.ts" src="./yes.ts"></script>'
        assert [i.module_path for i in parser.parse_imports(_file(size=len(src)), src)] == [
            "./yes.ts"
        ]

    def test_cdn_sources_are_not_imports(self, parser) -> None:
        src = b'<script src="https://cdn.example.com/a.js"></script>\n<script src="//cdn.x/b.js" />'
        assert parser.parse_imports(_file(size=len(src)), src) == []

    def test_an_inline_root_script_is_a_non_relative_import(self, parser) -> None:
        src = b'<script is:inline src="/js/a.js"></script>'
        imports = parser.parse_file(_file(size=len(src)), src).imports
        assert [(i.module_path, i.is_relative) for i in imports] == [("/js/a.js", False)]

    def test_deep_nesting_keeps_the_frontmatter_imports(self, parser) -> None:
        src = (
            b'---\nimport A from "./A.astro";\n---\n'
            + b"<div>" * 1000
            + b'<script src="./deep.ts"></script>'
            + b"</div>" * 1000
        )
        parsed = parser.parse_file(_file(size=len(src)), src)
        assert [i.module_path for i in parsed.imports] == ["./A.astro", "./deep.ts"]

    def test_a_bare_markup_value_is_page_relative_and_a_bare_import_a_package(
        self, parser
    ) -> None:
        src = (
            b"---\nimport 'react';\nimport 'normalize.css';\n---\n"
            b'<link rel="icon" href="favicon.ico">\n'
            b'<link rel="manifest" href="site.webmanifest">\n'
            b'<script is:inline src="app.js"></script>\n'
            b'<script src="./app.js"></script>\n'
            b"<script>import 'nprogress';</script>\n"
        )
        expected = [
            ("react", False),
            ("normalize.css", False),
            ("nprogress", False),
            ("./favicon.ico", True),
            ("./site.webmanifest", True),
            ("./app.js", True),
            ("./app.js", True),
        ]
        file_info = _file(size=len(src))
        parsed = parser.parse_file(file_info, src).imports
        assert [(i.module_path, i.is_relative) for i in parsed] == expected
        imported = parser.parse_imports(file_info, src)
        assert [(i.module_path, i.is_relative) for i in imported] == expected

    @pytest.mark.parametrize(
        ("markup", "path"),
        [
            (b'<script src="@/a.ts"></script>', "@/a.ts"),
            (b"<script src='@/a.ts' />", "@/a.ts"),
            (b"<script\n  src=@/a.ts></script>", "@/a.ts"),
            (b'<script is:inline src="a.ts"></script>', "./a.ts"),
            (b'<script type="module" src="a.ts"></script>', "./a.ts"),
            (b'<script src="a.ts" defer></script>', "./a.ts"),
        ],
    )
    def test_only_a_script_with_a_lone_src_keeps_its_value(
        self, parser, markup: bytes, path: str
    ) -> None:
        # Astro bundles it as ``import "<src>"``; any other attribute leaves a page URL.
        imports = parser.parse_imports(_file(size=len(markup)), markup)
        assert [i.module_path for i in imports] == [path]

    @pytest.mark.parametrize(
        "page",
        [
            # Astro bundles a ``<script>`` with only ``src`` as ``import "<src>"``.
            pytest.param(b'<script src="@/scripts/menu.ts"></script>\n', id="script-src"),
            # Not under ``public/``, so tsconfig is asked, as for a ``.ts`` file.
            pytest.param(b'---\nimport "/@/scripts/menu.ts";\n---\n', id="root-alias"),
        ],
    )
    def test_an_import_resolves_through_a_tsconfig_alias(self, parser, tmp_path, page) -> None:
        from repowise.core.ingestion import GraphBuilder, wire_tsconfig_resolver

        (tmp_path / "tsconfig.json").write_text(
            '{"compilerOptions": {"baseUrl": ".", '
            '"paths": {"@/*": ["./src/*"], "/@/*": ["./src/*"]}}}'
        )
        files = {
            "src/layouts/Base.astro": (page, "astro"),
            "src/scripts/menu.ts": (b"document.title = 'menu';\n", "typescript"),
        }
        builder = GraphBuilder(repo_path=tmp_path)
        for path, (src, language) in files.items():
            builder.add_file(parser.parse_file(_file(path, len(src), language), src))
        wire_tsconfig_resolver(
            builder, tmp_path, include_submodules=False, include_nested_repos=False
        )
        graph = builder.build()
        assert graph.has_edge("src/layouts/Base.astro", "src/scripts/menu.ts")
        assert [n for n in graph if n.startswith("external:")] == []


class TestCalls:
    def test_frontmatter_calls_are_extracted(self, parsed) -> None:
        assert "formatDate" in {c.target_name for c in parsed.calls}

    def test_markup_component_tags_become_calls(self, parsed) -> None:
        header = [c for c in parsed.calls if c.target_name == "Header"]
        assert [c.line for c in header] == [15]


class TestCodeHealth:
    def test_complexity_is_measured_on_the_frontmatter(self) -> None:
        from repowise.core.analysis.health.complexity.walker import walk_file

        result = walk_file("/repo/src/pages/index.astro", "astro", _PAGE)
        assert next(f for f in result.functions if f.name == "getStaticPaths").start_line == 7

    def test_perf_and_dataflow_dialects_are_registered(self) -> None:
        from repowise.core.analysis.health.dataflow.dialects import DEFUSE_DIALECTS
        from repowise.core.analysis.health.perf.dialects import PERF_DIALECTS

        assert "astro" in PERF_DIALECTS
        assert "astro" in DEFUSE_DIALECTS


class TestDeadCode:
    def test_framework_exports_are_never_flagged_as_unused(self) -> None:
        # `prerender` / `getStaticPaths` are read by Astro, never imported by name.
        from repowise.core.analysis.dead_code.analyzer import _non_importable_kinds

        assert {"constant", "variable", "function", "class"} <= _non_importable_kinds("astro")

    def test_scripts_loaded_only_by_markup_are_reachable(self, parser) -> None:
        from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
        from repowise.core.analysis.dead_code.file_reachability import has_dependency_importer
        from repowise.core.ingestion import GraphBuilder

        page = (
            b"---\nimport 'normalize.css';\nconst url = '/x.js';\n---\n"
            b'<link rel="icon" href="/favicon.svg" />\n'
            b'<link rel="icon" href="favicon.ico" />\n'
            b'<script src="../scripts/menu.ts"></script>\n'
            b'<script is:inline src="/js/legacy.js"></script>\n'
            b'<script is:inline src="app.js"></script>\n'
            b"<script src={url}></script>\n"
        )
        files = {
            "src/pages/index.astro": (page, "astro"),
            "src/pages/app.js": (b"window.app = true;\n", "javascript"),
            "src/scripts/menu.ts": (b"document.body.dataset.menu = 'ready';\n", "typescript"),
            "public/js/legacy.js": (b"window.legacy = true;\n", "javascript"),
        }
        builder = GraphBuilder()
        for path, (src, language) in files.items():
            builder.add_file(parser.parse_file(_file(path, len(src), language), src))
        graph = builder.build()

        scripts = {"src/scripts/menu.ts", "public/js/legacy.js", "src/pages/app.js"}
        # Only the frontmatter's bare import is a package here. A bundled markup
        # ``<script src>`` could name one too; ``app.js`` is ``is:inline``, page-relative.
        assert [n for n in graph if n.startswith("external:")] == ["external:normalize.css"]
        assert all(has_dependency_importer(graph, path) for path in scripts)
        report = DeadCodeAnalyzer(graph, git_meta_map={}).analyze(
            {"detect_unused_exports": False, "detect_zombie_packages": False}
        )
        unreachable = {
            f.file_path for f in report.findings if f.kind == DeadCodeKind.UNREACHABLE_FILE
        }
        assert not unreachable & scripts
