"""The shared CODEOWNERS parser and matcher: GitHub and GitLab semantics."""

from __future__ import annotations

from repowise.core.ownership.codeowners import (
    CodeownersMatcher,
    CodeownersRule,
    parse_codeowners,
    read_codeowners,
)

_GITLAB = """\
# Default section: rules before any header.
* @acme/platform

[Docs] @acme/writers
/docs/
/docs/api/ @acme/api-docs

^[Backend][2] @acme/backend
/src/
/src/billing/ @acme/billing
/src/vendor/

[frontend]
/web/ @acme/web

[FRONTEND] @acme/design
/web/styles/
"""


def test_gitlab_sections_parse_with_defaults_optional_and_counts() -> None:
    rules = parse_codeowners(_GITLAB)
    assert rules[0] == CodeownersRule("*", ("@acme/platform",), None)
    assert CodeownersRule("/docs/", ("@acme/writers",), "docs") in rules
    assert CodeownersRule("/src/", ("@acme/backend",), "backend") in rules
    # An owner-less line takes the section default; ``^`` and ``[2]`` are accepted.
    assert CodeownersRule("/src/vendor/", ("@acme/backend",), "backend") in rules
    # Section names are one section whatever their case; each header keeps its defaults.
    assert CodeownersRule("/web/styles/", ("@acme/design",), "frontend") in rules


def test_gitlab_owners_are_the_union_of_each_sections_last_match() -> None:
    match = CodeownersMatcher(parse_codeowners(_GITLAB))
    assert match.owners("docs/api/ref.md") == ("@acme/platform", "@acme/api-docs")
    assert match.owners("src/billing/pay.py") == ("@acme/platform", "@acme/billing")
    assert match.owners("src/core.py") == ("@acme/platform", "@acme/backend")
    # Two headers with one name are one section: the later rule wins inside it.
    assert match.owners("web/styles/a.css") == ("@acme/platform", "@acme/design")
    assert match.owners("web/app.ts") == ("@acme/platform", "@acme/web")
    assert match.owners("README.md") == ("@acme/platform",)


def test_github_last_match_wins_and_ownerless_line_clears() -> None:
    text = "* @org/all\n/pkg/ @org/pkg  # pkg team\n/pkg/vendor/\n*.md docs@example.com\n"
    match = CodeownersMatcher(parse_codeowners(text))
    assert match.owners("pkg/a.py") == ("@org/pkg",)
    assert match.owners("pkg/vendor/x.py") == ()
    assert match.owners("pkg/README.md") == ("docs@example.com",)
    assert match.owners("lib/b.py") == ("@org/all",)


def test_read_codeowners_takes_the_first_location(tmp_path) -> None:
    assert read_codeowners(tmp_path) is None
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "CODEOWNERS").write_text("* @b\n", encoding="utf-8")
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "CODEOWNERS").write_text("* @a\n", encoding="utf-8")
    assert read_codeowners(tmp_path) == (".github/CODEOWNERS", "* @a\n")
