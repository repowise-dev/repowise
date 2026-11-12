"""The facts other files' resolution reads from a C/C++ file's text, before and after an edit."""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion import ASTParser, FileTraverser
from repowise.core.ingestion.cpp_cross_file_facts import changed_fact_names, cpp_cross_file_facts

_HEADER = """#pragma once
using Count = int;
class Shape { public: virtual int area() const = 0; };
template <typename T>
class Box : public T {
public:
  int area() const { return 1; }
  friend int peek(const Box& b);
};
inline int helper(int x) {
  using Local = long;
  return x + 1;
}
"""


def _facts(root: Path, text: str):
    root.mkdir()
    (root / "shape.h").write_text(text)
    fi = next(iter(FileTraverser(root).traverse()))
    parsed = ASTParser().parse_file(fi, (root / "shape.h").read_bytes())
    return cpp_cross_file_facts(parsed, text)


def _changed(tmp_path: Path, old: str, new: str) -> set[str] | None:
    assert old in _HEADER
    before = _facts(tmp_path / "before", _HEADER)
    after = _facts(tmp_path / "after", _HEADER.replace(old, new))
    return changed_fact_names(before, after)


def test_a_body_edit_changes_no_fact(tmp_path: Path) -> None:
    assert _changed(tmp_path, "return x + 1;", "return x + 2;") == set()


def test_a_body_alias_is_not_a_fact(tmp_path: Path) -> None:
    assert _changed(tmp_path, "using Local = long;", "using Local = short;") == set()


def test_dropping_a_pure_specifier_changes_its_name(tmp_path: Path) -> None:
    assert _changed(tmp_path, "area() const = 0;", "area() const;") == {"area"}


def test_renaming_a_template_parameter_changes_the_class(tmp_path: Path) -> None:
    changed = _changed(tmp_path, "template <typename T>\nclass Box : public T", "template <typename U>\nclass Box : public U")
    assert changed == {"Box"}


def test_retargeting_a_file_scope_alias_changes_its_name(tmp_path: Path) -> None:
    assert _changed(tmp_path, "using Count = int;", "using Count = long;") == {"Count"}


def test_other_languages_have_no_facts(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("def f():\n    return 1\n")
    fi = next(iter(FileTraverser(tmp_path).traverse()))
    parsed = ASTParser().parse_file(fi, (tmp_path / "m.py").read_bytes())
    assert cpp_cross_file_facts(parsed, "def f():\n    return 1\n") is None
    assert changed_fact_names(None, frozenset()) is None
