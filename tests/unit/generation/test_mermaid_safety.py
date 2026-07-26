"""Unit tests for the mermaid safety / auto-fix pass."""

from __future__ import annotations

import re

from repowise.core.generation.mermaid_safety import sanitize_mermaid, sanitize_pages


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
