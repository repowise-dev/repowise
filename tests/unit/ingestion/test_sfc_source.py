"""Unit tests for the single-file-component source projection.

``sfc_source`` is the seam every Svelte and Vue capability rests on: it blanks
the markup and ``<style>`` so the TypeScript grammar sees valid TS at
byte-identical offsets. The offset invariant is what lets the ingestion parser
and all three code-health walkers share one projection, so it is pinned here
directly rather than only through its consumers.
"""

from __future__ import annotations

import pytest

from repowise.core.ingestion.sfc_source import prepare_source, scan

_COMPONENT = b"""<script lang="ts">
  import Child from './Child.svelte';
  import { fmt } from '$lib/fmt';

  export let count: number = 0;

  function inc() {
    count += 1;
  }
</script>

<button on:click={inc}>{fmt(count)}</button>
<Child {count} />

<style>
  button { color: red; }
</style>
"""


class TestOffsetInvariants:
    """Byte length and line numbering must survive the projection untouched."""

    def test_length_is_preserved(self) -> None:
        assert len(prepare_source("svelte", _COMPONENT)) == len(_COMPONENT)

    def test_line_count_is_preserved(self) -> None:
        prepared = prepare_source("svelte", _COMPONENT)
        assert prepared.count(b"\n") == _COMPONENT.count(b"\n")

    def test_script_lines_are_byte_identical(self) -> None:
        prepared = prepare_source("svelte", _COMPONENT).splitlines()
        original = _COMPONENT.splitlines()
        # The import / export / function lines live inside <script>.
        for index in (1, 2, 4, 6):
            assert prepared[index] == original[index]

    def test_non_ascii_markup_keeps_offsets(self) -> None:
        src = "<script>let a = 1;</script>\n<p>héllo wörld ✨</p>\n".encode()
        prepared = prepare_source("svelte", src)
        assert len(prepared) == len(src)
        assert prepared.count(b"\n") == src.count(b"\n")

    def test_other_languages_pass_through_unchanged(self) -> None:
        src = b"const a = 1;\n"
        assert prepare_source("typescript", src) is src
        assert prepare_source("python", src) is src


class TestBlanking:
    def test_style_block_is_removed(self) -> None:
        prepared = prepare_source("svelte", _COMPONENT)
        assert b"color: red" not in prepared

    def test_markup_tags_are_removed(self) -> None:
        # The closing </script> is itself markup, so slice by line instead.
        tail = b"\n".join(prepare_source("svelte", _COMPONENT).splitlines()[10:])
        assert b"button" not in tail
        assert b"Child" not in tail

    def test_markup_expressions_are_kept(self) -> None:
        # Without these, a handler referenced only from the template
        # (on:click={inc}) would carry no edge and read as dead code.
        tail = b"\n".join(prepare_source("svelte", _COMPONENT).splitlines()[10:])
        assert b"inc" in tail
        assert b"fmt(count)" in tail


class TestExpressionFencing:
    """Kept expressions must not run together into one invalid statement."""

    def test_adjacent_expressions_are_separated(self) -> None:
        src = b"<script>let a=1,b=2;</script>\n<p>{a}{b}</p>\n"
        prepared = prepare_source("svelte", src)
        assert b";a;;b;" in prepared.replace(b" ", b"")

    def test_unterminated_script_statement_does_not_swallow_markup(self) -> None:
        # `const o = { x: 1 }` with no semicolon followed by `{() => f()}`
        # parses as one call expression unless the expression is fenced.
        src = b"<script>const o = { x: 1 }</script>\n<b on:click={() => f()}>x</b>\n"
        prepared = prepare_source("svelte", src)
        line = prepared.splitlines()[1]
        assert line.strip().startswith(b";")
        assert line.rstrip().endswith(b";")

    def test_each_block_head_is_not_kept(self) -> None:
        # `item, i (item.id)` is Svelte block syntax, not a JS expression.
        src = b"<script>let items=[];</script>\n{#each items as item, i (item.id)}<p/>{/each}\n"
        prepared = prepare_source("svelte", src)
        assert b"item, i" not in prepared

    def test_if_block_head_is_kept(self) -> None:
        src = b"<script>let n=0;</script>\n{#if n > 0}<p/>{/if}\n"
        prepared = prepare_source("svelte", src)
        assert b"n > 0" in prepared

    def test_object_literal_attribute_is_dropped(self) -> None:
        # A `{ ... }` body reads as a block at statement position; dropping it
        # is the documented ceiling.
        src = b"<script>let p=1;</script>\n<b use:act={{ a: p }}>x</b>\n"
        prepared = prepare_source("svelte", src)
        assert b"a: p" not in prepared


class TestComponentTags:
    def test_capitalized_tags_are_component_usages(self) -> None:
        names = {name for name, _ in scan("svelte", _COMPONENT).component_tags}
        assert names == {"Child"}

    def test_lowercase_html_elements_are_not_components(self) -> None:
        src = b"<div><span>hi</span></div>\n"
        assert scan("svelte", src).component_tags == ()

    def test_svelte_namespace_directives_are_not_components(self) -> None:
        # <svelte:window> etc. are compiler directives, not user components.
        src = b"<svelte:window onkeydown={h} />\n<svelte:head><title>x</title></svelte:head>\n"
        names = {name for name, _ in scan("svelte", src).component_tags}
        assert names == set()

    def test_tag_line_numbers_point_at_the_original_file(self) -> None:
        tags = dict(scan("svelte", _COMPONENT).component_tags)
        assert tags["Child"] == 13


class TestDegradation:
    @pytest.mark.parametrize(
        "src",
        [
            b"",
            b"<p>markup only, no script</p>\n",
            b"<script>",
            b"<div><span></div>\n",
        ],
    )
    def test_malformed_or_scriptless_input_never_raises(self, src: bytes) -> None:
        prepared = prepare_source("svelte", src)
        assert len(prepared) == len(src)


# ---------------------------------------------------------------------------
# Vue
# ---------------------------------------------------------------------------

_VUE_COMPONENT = b"""<template>
  <div :class="cls" @click="inc(1)" v-if="ready">
    {{ label(item) }} and {{ other }}
  </div>
  <WarningBar :msg="msg" />
  <back-to-top />
  <li v-for="row in rows" :key="row.id">{{ row.n }}</li>
  <KeepAlive><slot /></KeepAlive>
  <component :is="which" />
</template>

<script setup lang="ts">
import WarningBar from '@/components/warningBar.vue';

const cls = 'a';

function inc(n: number) {
  return n + 1;
}
</script>

<style scoped>
  .a { color: red; }
</style>
"""


class TestVueOffsetInvariants:
    """The projection is byte-for-byte positional, exactly as for Svelte."""

    def test_length_is_preserved(self) -> None:
        assert len(prepare_source("vue", _VUE_COMPONENT)) == len(_VUE_COMPONENT)

    def test_newline_count_is_preserved(self) -> None:
        prepared = prepare_source("vue", _VUE_COMPONENT)
        assert prepared.count(b"\n") == _VUE_COMPONENT.count(b"\n")

    def test_non_ascii_markup_keeps_offsets(self) -> None:
        # A multi-byte character in the markup must not shift the script that
        # follows it — the blanking works in bytes, not code points.
        src = (
            "<template><p>日本語のテキスト</p></template>\n<script>const a = 1;</script>\n".encode()
        )
        prepared = prepare_source("vue", src)
        assert len(prepared) == len(src)
        assert prepared.index(b"const a = 1;") == src.index(b"const a = 1;")


class TestVueScriptAndMarkupProjection:
    def test_script_body_survives_verbatim(self) -> None:
        prepared = prepare_source("vue", _VUE_COMPONENT)
        assert b"import WarningBar from '@/components/warningBar.vue';" in prepared
        assert b"function inc(n: number) {" in prepared

    def test_style_and_plain_markup_are_blanked(self) -> None:
        prepared = prepare_source("vue", _VUE_COMPONENT)
        assert b"color: red" not in prepared
        assert b"<template>" not in prepared

    def test_directive_expressions_are_kept_and_fenced(self) -> None:
        # The two quote bytes around each value become ``;`` so neighbouring
        # attributes cannot run together into one statement.
        prepared = prepare_source("vue", _VUE_COMPONENT)
        assert b";cls;" in prepared
        assert b";inc(1);" in prepared
        assert b";ready;" in prepared

    def test_interpolations_are_kept_and_fenced(self) -> None:
        prepared = prepare_source("vue", _VUE_COMPONENT)
        assert b"; label(item) ;" in prepared
        assert b"; other ;" in prepared

    def test_plain_html_attributes_are_not_projected(self) -> None:
        # class="btn primary" is a literal string; projecting it would put two
        # juxtaposed identifiers at statement position and fail to parse.
        src = b'<template><div class="btn primary" id="x">hi</div></template>\n'
        prepared = prepare_source("vue", src)
        assert b"btn primary" not in prepared

    def test_v_for_binding_is_skipped(self) -> None:
        # ``row in rows`` parses as the ``in`` operator but is a binding form.
        prepared = prepare_source("vue", _VUE_COMPONENT)
        assert b"row in rows" not in prepared
        # ...while :key on the same element is still kept.
        assert b";row.id;" in prepared

    def test_slot_props_object_literal_is_skipped(self) -> None:
        src = b'<template><Table #default="{ row }" /></template>\n'
        prepared = prepare_source("vue", src)
        assert b"row" not in prepared

    def test_projection_parses_as_typescript(self) -> None:
        ts = pytest.importorskip("tree_sitter_typescript")
        from tree_sitter import Language, Parser

        prepared = prepare_source("vue", _VUE_COMPONENT)
        tree = Parser(Language(ts.language_typescript())).parse(prepared)
        assert not tree.root_node.has_error


class TestVueComponentTags:
    def test_pascal_case_tag_is_a_component(self) -> None:
        names = {name for name, _ in scan("vue", _VUE_COMPONENT).component_tags}
        assert "WarningBar" in names

    def test_kebab_case_tag_normalises_to_pascal_case(self) -> None:
        # Vue resolves <back-to-top> to the BackToTop component, and the
        # synthetic file symbol is named by the same rule, so they agree.
        names = {name for name, _ in scan("vue", _VUE_COMPONENT).component_tags}
        assert "BackToTop" in names

    def test_native_elements_are_not_components(self) -> None:
        names = {name for name, _ in scan("vue", _VUE_COMPONENT).component_tags}
        assert names.isdisjoint({"div", "li", "Div", "Li", "template", "Template"})

    def test_vue_builtins_never_mint_a_call_edge(self) -> None:
        names = {name for name, _ in scan("vue", _VUE_COMPONENT).component_tags}
        assert names.isdisjoint({"KeepAlive", "Slot", "Component"})

    def test_builtin_filter_applies_to_the_kebab_spelling_too(self) -> None:
        src = b"<template><keep-alive><router-view /></keep-alive></template>\n"
        assert scan("vue", src).component_tags == ()

    def test_tag_line_numbers_point_at_the_original_file(self) -> None:
        tags = dict(scan("vue", _VUE_COMPONENT).component_tags)
        assert tags["WarningBar"] == 5


class TestVueComponentNameFromStem:
    """Filenames and markup tags must normalise through one shared rule."""

    @pytest.mark.parametrize(
        ("stem", "parent", "expected"),
        [
            ("WarningBar", "components", "WarningBar"),
            ("warningBar", "components", "WarningBar"),
            ("back-to-top", "views", "BackToTop"),
            ("index", "Logo", "Logo"),
            ("index", "warning-bar", "WarningBar"),
            ("index", "", "Index"),
        ],
    )
    def test_stem_normalisation(self, stem: str, parent: str, expected: str) -> None:
        from repowise.core.ingestion.sfc_source import vue_component_name_from_stem

        assert vue_component_name_from_stem(stem, parent) == expected


class TestNonSfcLanguagesAreUntouched:
    @pytest.mark.parametrize("language", ["typescript", "javascript", "python", "go"])
    def test_prepare_source_is_identity(self, language: str) -> None:
        src = b"const a = 1;\n"
        assert prepare_source(language, src) is src

    @pytest.mark.parametrize("language", ["typescript", "python"])
    def test_scan_is_empty(self, language: str) -> None:
        result = scan(language, _VUE_COMPONENT)
        assert result.js_spans == ()
        assert result.component_tags == ()


class TestVueDegradation:
    @pytest.mark.parametrize(
        "src",
        [
            b"",
            b"<template><p>markup only</p></template>\n",
            b"<script>",
            b"<template><div><span></div></template>\n",
            b'<template><div :class=""></div></template>\n',
            b"<template>{{ }}</template>\n",
            b"<template>{{ unclosed </template>\n",
        ],
    )
    def test_malformed_or_scriptless_input_never_raises(self, src: bytes) -> None:
        prepared = prepare_source("vue", src)
        assert len(prepared) == len(src)
        assert prepared.count(b"\n") == src.count(b"\n")


# ---------------------------------------------------------------------------
# Razor / Blazor
# ---------------------------------------------------------------------------

_RAZOR_COMPONENT = b"""@page "/orders"
@inject OrderService OrderService

<RadzenAlert AlertStyle="AlertStyle.Warning">
    Some orders require attention.
</RadzenAlert>

<RadzenDataGrid Data="@orders" TItem="Order">
    <Columns>
        <RadzenDataGridColumn TItem="Order" Property="Name" Title="Name" />
    </Columns>
</RadzenDataGrid>

<RadzenButton Text="Save" Click="@SaveOrders" />

@code {
    private List<Order> orders = new();

    private async Task SaveAsync()
    {
        await OrderService.SaveOrdersAsync(orders);
    }
}
"""


class TestRazorOffsetInvariants:
    """Byte length and line numbering must survive the projection untouched."""

    def test_length_is_preserved(self) -> None:
        assert len(prepare_source("razor", _RAZOR_COMPONENT)) == len(_RAZOR_COMPONENT)

    def test_line_count_is_preserved(self) -> None:
        prepared = prepare_source("razor", _RAZOR_COMPONENT)
        assert prepared.count(b"\n") == _RAZOR_COMPONENT.count(b"\n")

    def test_code_block_lines_are_byte_identical(self) -> None:
        prepared = prepare_source("razor", _RAZOR_COMPONENT).splitlines()
        original = _RAZOR_COMPONENT.splitlines()
        # The @code interior (1-indexed lines 17-22) is projected verbatim.
        # The brace lines themselves are fenced to ``;`` by design, so they
        # are excluded: the offset, not the byte, is what must survive there.
        for index in range(16, 22):
            assert prepared[index] == original[index]

    def test_markup_lines_are_blanked(self) -> None:
        prepared = prepare_source("razor", _RAZOR_COMPONENT).splitlines()
        # Directives and markup (lines 1, 2, 4, 8, 14) become whitespace.
        for index in (0, 1, 3, 7, 13):
            assert prepared[index].strip() == b""

    def test_non_ascii_markup_keeps_offsets(self) -> None:
        src = '@code { var s = "héllo wörld 🎉"; }\n<p>résumé</p>\n'.encode()
        prepared = prepare_source("razor", src)
        assert len(prepared) == len(src)
        assert prepared.count(b"\n") == src.count(b"\n")

    def test_cshtml_extension_shares_the_projection(self) -> None:
        # .cshtml files carry the same razor tag, so the locator applies.
        from repowise.core.ingestion.models import EXTENSION_TO_LANGUAGE

        assert EXTENSION_TO_LANGUAGE[".cshtml"] == "razor"
        prepared = prepare_source("razor", _RAZOR_COMPONENT)
        assert len(prepared) == len(_RAZOR_COMPONENT)


class TestRazorBlanking:
    def test_statement_block_is_projected(self) -> None:
        src = b"@{\n    var total = orders.Sum(o => o.Amount);\n}\n"
        prepared = prepare_source("razor", src)
        assert b"total" in prepared
        assert b"Sum" in prepared

    def test_functions_block_is_projected(self) -> None:
        src = b"@functions {\n    public int Count { get; set; }\n}\n"
        prepared = prepare_source("razor", src)
        assert b"Count" in prepared

    def test_markup_and_directives_are_blanked(self) -> None:
        prepared = prepare_source("razor", _RAZOR_COMPONENT)
        assert b"RadzenAlert" not in prepared
        assert b"@page" not in prepared
        assert b"@inject" not in prepared
        assert b"Click" not in prepared
        # Attribute binding values (@orders, @SaveOrders) are not call edges
        # and are blanked with the rest of the markup. ``SaveOrdersAsync``
        # (inside @code) legitimately survives; the bound name without the
        # sigil must not.
        assert b"@SaveOrders" not in prepared
        assert b"SaveOrdersAsync" in prepared

    def test_generics_inside_code_are_not_treated_as_markup(self) -> None:
        # ``List<Order>`` is a generic type argument inside @code, not a
        # component tag. The tag pass must skip C# regions.
        names = {name for name, _ in scan("razor", _RAZOR_COMPONENT).component_tags}
        assert "Order" not in names
        assert "List" not in names

    def test_brace_inside_a_string_does_not_break_matching(self) -> None:
        src = b'@code { var s = "{"; }\n<Live />\n'
        result = scan("razor", src)
        assert len(result.js_spans) == 1
        assert dict(result.component_tags) == {"Live": 2}

    def test_brace_inside_a_verbatim_string_does_not_break_matching(self) -> None:
        src = b'@code { var s = @"{"; }\n<Live />\n'
        result = scan("razor", src)
        assert len(result.js_spans) == 1
        assert dict(result.component_tags) == {"Live": 2}

    def test_brace_inside_a_char_literal_does_not_break_matching(self) -> None:
        src = b"@code { var c = '{'; }\n<Live />\n"
        result = scan("razor", src)
        assert len(result.js_spans) == 1
        assert dict(result.component_tags) == {"Live": 2}

    def test_brace_inside_a_line_comment_does_not_break_matching(self) -> None:
        src = b"@code {\n    // }\n    void Go() { }\n}\n<Live />\n"
        result = scan("razor", src)
        assert len(result.js_spans) == 1
        assert dict(result.component_tags) == {"Live": 5}

    def test_brace_inside_a_block_comment_does_not_break_matching(self) -> None:
        src = b"@code {\n    /* { */\n    void Go() { }\n}\n<Live />\n"
        result = scan("razor", src)
        assert len(result.js_spans) == 1
        assert dict(result.component_tags) == {"Live": 5}

    def test_adjacent_blocks_are_fenced(self) -> None:
        src = b"@{ var a = 1; }\n@{ var b = 2; }\n"
        prepared = prepare_source("razor", src)
        compact = prepared.replace(b" ", b"")
        # Each interior is fenced on both sides; the trailing ``;`` inside
        # the body plus the fenced closing brace give the ``;;`` run.
        assert b";vara=1;;" in compact
        assert b";varb=2;;" in compact


class TestRazorComponentTags:
    def test_capitalized_tags_are_component_usages(self) -> None:
        names = {name for name, _ in scan("razor", _RAZOR_COMPONENT).component_tags}
        assert {
            "RadzenAlert",
            "RadzenDataGrid",
            "RadzenDataGridColumn",
            "RadzenButton",
            "Columns",
        } <= names

    def test_lowercase_html_elements_are_not_components(self) -> None:
        src = b"<div><span>hi</span></div>\n@code { }\n"
        assert scan("razor", src).component_tags == ()

    def test_tag_line_numbers_point_at_the_original_file(self) -> None:
        tags = dict(scan("razor", _RAZOR_COMPONENT).component_tags)
        assert tags["RadzenDataGrid"] == 8

    def test_namespace_qualified_tag_records_the_last_segment(self) -> None:
        names = {name for name, _ in scan("razor", b"<Foo.Bar />\n").component_tags}
        assert names == {"Bar"}

    def test_digits_are_part_of_a_component_name(self) -> None:
        names = {name for name, _ in scan("razor", b"<Grid2 />\n").component_tags}
        assert names == {"Grid2"}

    def test_lowercase_namespace_with_a_pascal_case_component_is_a_tag(self) -> None:
        names = {name for name, _ in scan("razor", b"<foo.Bar />\n").component_tags}
        assert names == {"Bar"}

    def test_lowercase_last_segment_is_not_a_component(self) -> None:
        assert scan("razor", b"<Foo.bar />\n").component_tags == ()

    def test_comparisons_inside_code_are_not_tags(self) -> None:
        src = b"@code { if (a < B) { Go(); } }\n"
        names = {name for name, _ in scan("razor", src).component_tags}
        assert names == set()

    def test_tags_inside_pre_are_still_components(self) -> None:
        # Razor gives <pre> no verbatim semantics: a component inside it
        # renders, so the tag pass reads it like any other markup. A code
        # sample has to be HTML-escaped to show up literally, and an escaped
        # ``&lt;Foo`` opens no tag.
        src = b"<pre><Foo /></pre>\n<pre>&lt;Bar /&gt;</pre>\n"
        names = {name for name, _ in scan("razor", src).component_tags}
        assert names == {"Foo"}


class TestRazorComments:
    """Commented-out code compiles to nothing, so it must mint nothing."""

    def test_razor_comment_hides_code_blocks(self) -> None:
        src = b"@* @code { void Hidden() { Go(); } } *@\n@code { void Live() { } }\n"
        prepared = prepare_source("razor", src)
        assert b"Hidden" not in prepared
        assert b"Go" not in prepared
        assert b"Live" in prepared

    def test_razor_comment_hides_component_tags(self) -> None:
        src = b"@*\n<Hidden />\n*@\n<Live />\n"
        tags = dict(scan("razor", src).component_tags)
        assert tags == {"Live": 4}

    def test_razor_comment_keeps_offsets(self) -> None:
        src = b"@* one\ntwo *@\n@code { var x = 1; }\n"
        prepared = prepare_source("razor", src)
        assert len(prepared) == len(src)
        assert prepared.count(b"\n") == src.count(b"\n")
        assert prepared.index(b"var x") == src.index(b"var x")

    def test_unterminated_razor_comment_hides_the_rest_of_the_file(self) -> None:
        src = b"@* forgot to close\n@code { void Hidden() { } }\n<Hidden />\n"
        result = scan("razor", src)
        assert result.js_spans == ()
        assert result.component_tags == ()

    def test_comment_marker_inside_a_code_block_is_not_a_comment(self) -> None:
        # ``@*`` inside a C# string is C#, not a Razor comment; the block was
        # already jumped over, so the tag after it is still found.
        src = b'@code { var s = "@*"; }\n<Live />\n'
        result = scan("razor", src)
        assert len(result.js_spans) == 1
        assert dict(result.component_tags) == {"Live": 2}

    def test_html_comment_hides_component_tags_but_not_code(self) -> None:
        # A component inside ``<!-- -->`` never renders, but a Razor
        # expression inside one still runs, so only the tag pass skips it.
        src = b"<!-- <Hidden /> @{ Run(); } -->\n<Live />\n"
        result = scan("razor", src)
        assert dict(result.component_tags) == {"Live": 2}
        assert b"Run" in prepare_source("razor", src)

    def test_unterminated_html_comment_hides_the_rest_of_the_tags(self) -> None:
        src = b"<!-- open\n<Hidden />\n"
        assert scan("razor", src).component_tags == ()


class TestRazorDegradation:
    @pytest.mark.parametrize(
        "src",
        [
            b"",
            b"<p>markup only, no code block</p>\n",
            b"@code {",
            b"@code",
            b"@",
            b"@using System.Linq\n",
            b"@@code { this is escaped markup }\n",
            b"<RadzenDataGrid",
        ],
    )
    def test_malformed_or_code_less_input_never_raises(self, src: bytes) -> None:
        prepared = prepare_source("razor", src)
        assert len(prepared) == len(src)
        assert prepared.count(b"\n") == src.count(b"\n")

    def test_unterminated_code_block_degrades_to_no_spans(self) -> None:
        src = b"@code {\n    private int x;\n"
        result = scan("razor", src)
        assert result.js_spans == ()
