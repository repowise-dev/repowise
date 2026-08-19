"""Fix-shape classification: which bug-fix diffs actually change production code.

The shape kind gates ``prior_defect_count``, so every rule here is a rule about
whether a commit gets counted as a defect. Cases are drawn from the frozen
240-commit label set in ``local-stash/fix-diff-intelligence/labels`` — each
class of miss that set exposed has a test below.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.changed_lines import parse_unified_diff
from repowise.core.ingestion.git_indexer.fix_shape import (
    classify_fix_shape,
    is_code_path,
    is_test_path,
)


def shape(diff: str) -> str:
    return classify_fix_shape(parse_unified_diff(diff))


def one_file(path: str, *, removed: list[str], added: list[str]) -> str:
    """A minimal single-hunk ``-U0`` diff for *path*."""
    old_count, new_count = len(removed), len(added)
    body = "".join(f"-{line}\n" for line in removed) + "".join(f"+{line}\n" for line in added)
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n+++ b/{path}\n"
        f"@@ -10,{old_count} +10,{new_count} @@\n{body}"
    )


# --- path rules ------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "tests/test_app.py",
        "src/pkg/test_helpers.py",
        "packages/ui/__tests__/panel.test.tsx",
        "internal/server_test.go",
        "spec/models/user_spec.rb",
        "flask/testsuite/basic.py",
        "conftest.py",
    ],
)
def test_test_paths_recognised(path: str) -> None:
    assert is_test_path(path)


@pytest.mark.parametrize(
    "path",
    [
        # Shipped test-*framework* library code, not somebody's test suite. The
        # old ``"test" in path`` substring rule dropped every fix to these from
        # the defect count.
        "django/test/client.py",
        "src/flask/testing.py",
        "packages/core/src/repowise/core/analysis/latest.py",
        # Tooling config that happens to carry a code extension.
        "vitest.config.ts",
    ],
)
def test_production_paths_not_mistaken_for_tests(path: str) -> None:
    assert not is_test_path(path)


def test_config_files_are_not_production_code() -> None:
    assert not is_code_path("vitest.config.ts")
    assert not is_code_path("packages/app/webpack.config.js")
    assert is_code_path("packages/app/src/index.ts")


# --- shape classification --------------------------------------------------


def test_empty_diff() -> None:
    assert shape("") == "empty"


def test_doc_only_includes_mdx() -> None:
    diff = one_file("docs/api.mdx", removed=["old text here"], added=["new text here"])
    assert shape(diff) == "doc_only"


def test_test_only() -> None:
    diff = one_file("tests/test_app.py", removed=["assert a == 1"], added=["assert a == 2"])
    assert shape(diff) == "test_only"


def test_config_other() -> None:
    diff = one_file(".github/workflows/ci.yml", removed=["  node: 18"], added=["  node: 20"])
    assert shape(diff) == "config_other"


def test_code_fix() -> None:
    diff = one_file(
        "src/app.py",
        removed=["    return items[0]"],
        added=["    return items[0] if items else None"],
    )
    assert shape(diff) == "code_fix"


def test_fix_to_shipped_test_framework_counts_as_code() -> None:
    diff = one_file(
        "django/test/client.py",
        removed=["        return self.response.template"],
        added=["        return self.response.templates[0]"],
    )
    assert shape(diff) == "code_fix"


# --- comment_only ----------------------------------------------------------


def test_comment_only_full_line_comments() -> None:
    diff = one_file(
        "src/app.py",
        removed=["    # exception ocurrs and reloads", "    # fidling with dicts"],
        added=["    # exception occurs and reloads", "    # fiddling with dicts"],
    )
    assert shape(diff) == "comment_only"


def test_comment_only_docstring_prose() -> None:
    """Reflowed docstring bodies carry no comment marker of their own."""
    diff = one_file(
        "src/cli.py",
        removed=["    some very advanced usecases for which it makes sense to"],
        added=["    some very advanced use cases for which it makes sense to"],
    )
    assert shape(diff) == "comment_only"


def test_comment_only_trailing_type_ignore_removed() -> None:
    diff = one_file(
        "src/app.py",
        removed=["            rv.headers.update(headers)  # type: ignore[arg-type]"],
        added=["            rv.headers.update(headers)"],
    )
    assert shape(diff) == "comment_only"


def test_added_docstring_block_is_comment_only() -> None:
    diff = one_file(
        "src/app.py",
        removed=[],
        added=[
            "",
            "        .. admonition:: Debug Note",
            "",
            "           In debug mode Flask will not tear down a request",
        ],
    )
    assert shape(diff) == "comment_only"


def test_swapped_statements_are_a_code_fix() -> None:
    """Identical line multisets in a different order change behaviour."""
    diff = one_file(
        "src/ctx.py",
        removed=[
            "    return _cv_app.get(None) is not None",
            "    return _cv_req.get(None) is not None",
        ],
        added=[
            "    return _cv_req.get(None) is not None",
            "    return _cv_app.get(None) is not None",
        ],
    )
    assert shape(diff) == "code_fix"


def test_statement_moved_deeper_is_a_code_fix() -> None:
    """Same statement, new indentation: a real change, not a comment edit."""
    diff = one_file(
        "src/app.py",
        removed=["        ctx.push()"],
        added=["                ctx.push()"],
    )
    assert shape(diff) == "code_fix"


def test_comment_marker_inside_a_string_is_not_stripped() -> None:
    diff = one_file(
        "src/app.py",
        removed=['    url = "http://old.example"'],
        added=['    url = "http://new.example"'],
    )
    assert shape(diff) == "code_fix"


def test_docs_plus_tests_reads_as_test_only() -> None:
    """``doc_only`` needs every file to be docs; either way it is not counted."""
    diff = one_file("README.md", removed=["a b c"], added=["a b d"]) + one_file(
        "tests/test_app.py", removed=["assert x"], added=["assert y"]
    )
    assert shape(diff) == "test_only"


def test_comment_edit_plus_real_change_is_a_code_fix() -> None:
    diff = one_file(
        "src/app.py",
        removed=["    # a typo heer", "    return None"],
        added=["    # a typo here", "    return default"],
    )
    assert shape(diff) == "code_fix"


# --- two-sided diff parsing ------------------------------------------------


def test_parser_returns_old_ranges_and_line_text() -> None:
    diff = one_file("src/app.py", removed=["old"], added=["new"])
    parsed = parse_unified_diff(diff)
    entry = parsed["src/app.py"]
    assert entry.old_ranges == [(10, 10)]
    assert entry.new_lines == {10}
    assert entry.removed == ["old"]
    assert entry.added == ["new"]


def test_parser_keeps_deleted_files_under_their_old_path() -> None:
    diff = "diff --git a/gone.py b/gone.py\n--- a/gone.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-a\n-b\n"
    parsed = parse_unified_diff(diff)
    assert parsed["gone.py"].old_ranges == [(1, 2)]
    assert parsed["gone.py"].new_lines == set()


def test_parser_does_not_mistake_removed_dashes_for_a_header() -> None:
    """A removed line whose text starts with ``--`` renders as ``--- ...``."""
    diff = one_file("src/app.py", removed=["-- a sql comment", "code()"], added=["code2()"])
    parsed = parse_unified_diff(diff)
    assert list(parsed) == ["src/app.py"]
    assert parsed["src/app.py"].removed == ["-- a sql comment", "code()"]
