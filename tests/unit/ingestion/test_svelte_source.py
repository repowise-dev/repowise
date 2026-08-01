"""Unit tests for the Svelte single-file-component source projection.

``svelte_source`` is the seam every Svelte capability rests on: it blanks the
markup and ``<style>`` so the TypeScript grammar sees valid TS at
byte-identical offsets. The offset invariant is what lets the ingestion parser
and all three code-health walkers share one projection, so it is pinned here
directly rather than only through its consumers.
"""

from __future__ import annotations

import pytest

from repowise.core.ingestion.svelte_source import prepare_source, scan

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
        names = {name for name, _ in scan(_COMPONENT).component_tags}
        assert names == {"Child"}

    def test_lowercase_html_elements_are_not_components(self) -> None:
        src = b"<div><span>hi</span></div>\n"
        assert scan(src).component_tags == ()

    def test_svelte_namespace_directives_are_not_components(self) -> None:
        # <svelte:window> etc. are compiler directives, not user components.
        src = b"<svelte:window onkeydown={h} />\n<svelte:head><title>x</title></svelte:head>\n"
        names = {name for name, _ in scan(src).component_tags}
        assert names == set()

    def test_tag_line_numbers_point_at_the_original_file(self) -> None:
        tags = dict(scan(_COMPONENT).component_tags)
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
