"""Unit tests for the mermaid safety / auto-fix pass."""

from __future__ import annotations

import re

from repowise.core.generation.mermaid_safety import (
    sanitize_mermaid,
    sanitize_pages,
    strip_leading_preamble,
)


def _block(body: str) -> str:
    return f"intro\n\n```mermaid\n{body}\n```\n\noutro"


def test_no_mermaid_is_noop():
    md = "# Title\n\nSome `code` and prose, no diagrams.\n"
    assert sanitize_mermaid(md) == md


def test_path_node_ids_are_slugged_consistently():
    md = _block("graph TD\n  pkg/foo.py[Foo] --> pkg/bar.py[Bar]")
    out = sanitize_mermaid(md)
    # Illegal path IDs gone; replaced by the same slug on both sides of the edge.
    assert "pkg/foo.py" not in out
    assert "pkg/bar.py" not in out
    assert "pkg_foo_py" in out
    assert "pkg_bar_py" in out
    # The edge is preserved.
    assert "-->" in out


def test_dotted_ids_slugged():
    md = _block("flowchart LR\n  app.main --> app.db")
    out = sanitize_mermaid(md)
    assert "app.main" not in out
    assert "app_main" in out
    assert "app_db" in out


def test_unquoted_label_with_parens_is_quoted():
    md = _block("graph TD\n  A[run() -> None] --> B[ok]")
    out = sanitize_mermaid(md)
    assert '"run() -> None"' in out
    # A simple label with no special chars is left alone.
    assert "B[ok]" in out


def test_already_quoted_label_untouched():
    md = _block('graph TD\n  A["already (quoted)"] --> B')
    out = sanitize_mermaid(md)
    assert out.count('"already (quoted)"') == 1


def test_inner_quotes_escaped():
    md = _block('graph TD\n  A[say "hi" (x)]')
    out = sanitize_mermaid(md)
    assert "&quot;hi&quot;" in out


def test_non_graph_diagram_left_alone():
    body = "sequenceDiagram\n  Alice->>John: Hello John, how are you?"
    md = _block(body)
    out = sanitize_mermaid(md)
    # Sequence diagrams use a different grammar — we must not touch them.
    assert body in out


def test_collision_produces_unique_slugs():
    # Two distinct paths that slugify to the same base must stay distinct.
    md = _block("graph TD\n  a/b[X] --> a.b[Y]")
    out = sanitize_mermaid(md)
    assert "a_b" in out
    assert "a_b_2" in out


def test_sanitize_pages_mutates_and_counts():
    class _P:
        def __init__(self, content):
            self.content = content

    changed = _P(_block("graph TD\n  pkg/x.py[X]"))
    unchanged = _P("# clean\n\nno diagram here")
    n = sanitize_pages([changed, unchanged])
    assert n == 1
    assert "pkg/x.py" not in changed.content
    assert unchanged.content == "# clean\n\nno diagram here"


# ---------------------------------------------------------------------------
# Label preservation, nesting, and stable slugs
# ---------------------------------------------------------------------------


def test_a_path_inside_a_label_survives_the_id_pass() -> None:
    """The label is prose. Slugging it produced a legal but unreadable diagram."""
    out = sanitize_mermaid(_block("graph TD\n  A[src/main.py] --> B[app.run]"))
    assert '"src/main.py"' in out
    assert "src_main_py" not in out


def test_an_already_quoted_label_is_left_alone_by_the_id_pass() -> None:
    out = sanitize_mermaid(_block('graph TD\n  A["pkg/foo.py handles auth"] --> B'))
    assert '"pkg/foo.py handles auth"' in out


def test_a_bare_path_used_as_a_node_id_is_still_slugged() -> None:
    """The point of the pass: an unquoted path id breaks the whole diagram."""
    out = sanitize_mermaid(_block("graph TD\n  pkg/foo.py --> pkg/bar.py"))
    assert "pkg/foo.py -->" not in out
    assert "pkg_foo_py" in out
    assert "pkg_bar_py" in out


def test_a_label_containing_its_own_bracket_ends_where_it_really_ends() -> None:
    out = sanitize_mermaid(_block("graph TD\n  A[run(x[0])] --> B"))
    # The whole label is captured and quoted, not cut at the inner bracket.
    assert '"run(x[0])"' in out
    assert out.count("-->") == 1


def test_colliding_slugs_do_not_depend_on_document_order() -> None:
    """A counter would renumber everything after an unrelated insertion."""
    first = sanitize_mermaid(_block("graph TD\n  a/b.py --> a.b.py"))
    second = sanitize_mermaid(_block("graph TD\n  a.b.py --> a/b.py"))
    ids_first = set(re.findall(r"a_b_py\w*", first))
    ids_second = set(re.findall(r"a_b_py\w*", second))
    assert ids_first == ids_second
    assert len(ids_first) == 2


def test_output_is_stable_across_runs() -> None:
    body = _block("graph TD\n  src/a.py --> src/b.py\n  C[run() -> None] --> src/a.py")
    assert sanitize_mermaid(body) == sanitize_mermaid(body)


def test_a_non_graph_diagram_is_still_left_untouched() -> None:
    body = _block("sequenceDiagram\n  A->>B: calls src/main.py")
    assert sanitize_mermaid(body) == body


class TestThePassNeverMakesADiagramWorse:
    """The module promises it can only improve a block. Three ways it did not."""

    def test_a_pipe_edge_label_is_not_slugged(self) -> None:
        """``-->|yes/no|`` is a label, not a node id.

        Labels between pipes are never quoted, so the pathy-token pass ran over
        them and rewrote ordinary words. ``read/write``, ``true/false`` and
        ``A/B`` are all everyday edge labels.
        """
        md = "```mermaid\ngraph LR\nA -->|yes/no| B\nC -->|read/write| D\n```"
        out = sanitize_mermaid(md)
        assert "|yes/no|" in out
        assert "|read/write|" in out
        assert "yes_no" not in out

    def test_a_pipe_label_does_not_stop_ids_being_slugged(self) -> None:
        """Protecting the label must not protect the endpoints around it."""
        md = "```mermaid\ngraph LR\nsrc/a.py -->|calls| src/b.py\n```"
        out = sanitize_mermaid(md)
        assert "|calls|" in out
        assert "src_a_py" in out
        assert "src_b_py" in out

    def test_an_init_directive_survives(self) -> None:
        """``%%{init: ...}%%`` is a directive, not a rhombus label.

        ``{`` is a shape opener, so the directive body was read as a label and
        its quotes were entity-escaped — turning a diagram that rendered into
        one that does not.

        The directive is placed after the graph line because that is the case
        that corrupts: with it on the first line the whole block fails the
        graph-directive check and is skipped untouched.
        """
        md = '```mermaid\ngraph LR\n%%{init: {"theme":"dark"}}%%\nA --> B\n```'
        out = sanitize_mermaid(md)
        assert '%%{init: {"theme":"dark"}}%%' in out
        assert "&quot;" not in out

    def test_a_comment_line_is_left_alone(self) -> None:
        """``%%`` starts a comment, so nothing in it is diagram syntax."""
        md = "```mermaid\ngraph LR\n%% see src/main.py for the (real) entry point\nA --> B\n```"
        out = sanitize_mermaid(md)
        assert "%% see src/main.py for the (real) entry point" in out

    def test_a_slug_cannot_collide_with_an_id_already_in_the_diagram(self) -> None:
        """Two distinct nodes must not be merged into one.

        Collisions were only resolved among the ids being slugged, so a slug
        landing on an identifier that was already legal silently merged the two
        nodes and turned a real edge into a self-loop.
        """
        md = "```mermaid\ngraph LR\nsrc_main_py[Existing]\nsrc/main.py --> src_main_py\n```"
        out = sanitize_mermaid(md)
        edge = next(line for line in out.split("\n") if "-->" in line)
        source, target = (part.strip() for part in edge.split("-->"))
        assert source != target, edge
        assert "src_main_py[Existing]" in out




def test_strip_leading_preamble_removes_narration():
    md = (
        "I'll examine the actual source file.\n\n"
        "I'm using the repository's Beads workflow.\n\n"
        "# foo.py\n\n## Overview\n"
    )
    assert strip_leading_preamble(md) == "# foo.py\n\n## Overview\n"


def test_strip_leading_preamble_noop_when_starts_with_heading():
    md = "# foo.py\n\n## Overview\n"
    assert strip_leading_preamble(md) == md


def test_strip_leading_preamble_noop_without_heading():
    md = "Just some prose with no markdown heading at all.\n"
    assert strip_leading_preamble(md) == md


def test_sanitize_pages_strips_preamble():
    class _P:
        def __init__(self, content):
            self.content = content

    page = _P("Narration first.\n\n# Title\n\nbody\n")
    n = sanitize_pages([page])
    assert n == 1
    assert page.content.startswith("# Title")