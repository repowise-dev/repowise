"""Whether a decision's rule still holds is read from the rule's own anchors.

Real ``git``: the verdict is a reading of diffs and blobs, so a stubbed
subprocess would test only the parsing.
"""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from repowise.core.analysis.decisions.rule_check import (
    RuleClaim,
    check_rules,
    cited_identifiers,
    cited_literals,
    commit_times,
)

_T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
_RECORDED = _T0 + timedelta(hours=1)

_LIMITS = '''\
"""Limits for the ingestion pass."""

MAX_FILE_BYTES = 500_000


def size_limit(has_parser: bool) -> int:
    # Parsed files get four times the room: the parser bounds their cost.
    if has_parser:
        return MAX_FILE_BYTES * 4
    return MAX_FILE_BYTES


def unrelated() -> int:
    return 1
'''

_CALLER = '''\
from limits import size_limit


def read(path, has_parser):
    return size_limit(has_parser)
'''


def _git(repo: Path, *args: str, when: datetime | None = None) -> str:
    env = dict(os.environ)
    if when is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = when.isoformat()
    out = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=True, env=env
    )
    return out.stdout.strip()


def _commit(repo: Path, files: dict[str, str | None], when: datetime) -> str:
    for name, content in files.items():
        if content is None:
            (repo / name).unlink()
        else:
            (repo / name).write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "change", when=when)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "checkout"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _commit(root, {"limits.py": _LIMITS, "caller.py": _CALLER}, _T0)
    return root


def _later(repo: Path, files: dict[str, str | None]) -> None:
    _commit(repo, files, _RECORDED + timedelta(days=1))


def _check(repo: Path, *texts: str, quotes=(), scope=("limits.py",)):
    claim = RuleClaim(texts=texts, quotes=tuple(quotes), scope=tuple(scope), anchor=_RECORDED)
    found = check_rules(repo, {"d": claim})["d"]
    return found.verdict, found.basis


_QUOTE = ("limits.py", "Parsed files get four times the room: the parser bounds their cost.")


def test_a_change_elsewhere_in_the_file_leaves_the_rule_holding(repo: Path) -> None:
    _later(repo, {"limits.py": _LIMITS.replace("return 1", "return 2")})

    assert _check(repo, "`size_limit` gives parsed files four times `MAX_FILE_BYTES`") == (
        "holds",
        "spans_unchanged",
    )


def test_a_line_in_the_named_function_naming_nothing_cited_leaves_the_rule(repo: Path) -> None:
    added = "    print(has_parser)\n    if has_parser:"
    _later(repo, {"limits.py": _LIMITS.replace("    if has_parser:", added)})

    assert _check(repo, "`size_limit` caps unparsed files") == ("holds", "spans_unchanged")


def test_callers_moving_do_not_touch_a_rule_about_the_definition(repo: Path) -> None:
    _later(repo, {"caller.py": _CALLER.replace("return size_limit(has_parser)", "x = size_limit(True)\n    return x")})

    verdict = _check(repo, "`size_limit` caps unparsed files", scope=("limits.py", "caller.py"))
    assert verdict == ("holds", "spans_unchanged")


def test_the_comment_it_was_read_from_going_away_is_drift(repo: Path) -> None:
    _later(repo, {"limits.py": _LIMITS.replace(_QUOTE[1], "Size cap.")})

    assert _check(repo, _QUOTE[1], quotes=[_QUOTE]) == ("drifted", "quote_gone")


def test_a_comment_still_over_its_code_holds_when_that_code_is_edited(repo: Path) -> None:
    _later(repo, {"limits.py": _LIMITS.replace("if has_parser:", "if has_parser and True:")})

    assert _check(repo, _QUOTE[1], quotes=[_QUOTE]) == ("holds", "quote_present")


def test_a_module_docstring_says_nothing_about_the_code_under_it(repo: Path) -> None:
    quote = ("limits.py", "Limits for the ingestion pass.")
    edited = _LIMITS.replace("ingestion pass.", "ingestion pass.\n\nMore notes.")
    _later(repo, {"limits.py": edited})

    assert _check(repo, quote[1], quotes=[quote]) == ("unknown", "spans_touched")


def test_a_cited_name_gone_from_the_whole_tree_is_drift(repo: Path) -> None:
    _later(
        repo,
        {
            "limits.py": _LIMITS.replace("size_limit", "byte_ceiling"),
            "caller.py": _CALLER.replace("size_limit", "byte_ceiling"),
        },
    )

    assert _check(repo, "`size_limit` caps unparsed files") == ("drifted", "symbol_gone:size_limit")


def test_a_cited_name_that_moved_out_of_scope_is_not_called_gone(repo: Path) -> None:
    moved = _LIMITS.replace("def size_limit", "def other_limit")
    _later(repo, {"limits.py": moved, "elsewhere.py": "def size_limit():\n    return 0\n"})

    assert _check(repo, "`size_limit` caps unparsed files") == ("unknown", "moved")


def test_a_stated_literal_removed_from_the_rule_lines_is_drift(repo: Path) -> None:
    _later(repo, {"limits.py": _LIMITS.replace("500_000", "800_000")})

    assert _check(repo, "`MAX_FILE_BYTES` is 500_000 bytes") == ("drifted", "literal_gone:500_000")


def test_a_rule_naming_nothing_checkable_is_unknown(repo: Path) -> None:
    _later(repo, {"limits.py": _LIMITS.replace("return 1", "return 2")})

    assert _check(repo, "keep ingestion fast for large repositories") == ("unknown", "no_anchor")


def test_the_verdict_carries_the_head_it_was_read_at(repo: Path) -> None:
    _later(repo, {"limits.py": _LIMITS.replace("return 1", "return 2")})
    head = _git(repo, "rev-parse", "HEAD")
    claim = RuleClaim(texts=("`size_limit`",), quotes=(), scope=("limits.py",), anchor=_RECORDED)

    assert check_rules(repo, {"d": claim})["d"].commit == head
    assert commit_times(repo, [head[:10]])[head[:10]] == _RECORDED + timedelta(days=1)


def test_cited_names_are_code_shaped_words_and_backticked_spans() -> None:
    names = cited_identifiers(["Use `ASTParser` and parse_file via core.workspace.config, not Rich"])

    assert names == {"ASTParser", "parse_file", "core.workspace.config"}


def test_cited_literals_are_numbers_quoted_values_and_code_spans() -> None:
    literals = cited_literals(['a 30 s timeout, errors="replace", and `max(ccn)` per 5 files'])

    assert literals == {"30", "replace", "max(ccn)"}


@pytest.mark.parametrize("failing", ["head", "base"])
def test_a_failed_blob_read_reads_unread_never_holds(repo: Path, monkeypatch, failing: str) -> None:
    import repowise.core.analysis.decisions.rule_check as rule_check

    _later(repo, {"limits.py": _LIMITS.replace("return 1", "return 2")})
    head = _git(repo, "rev-parse", "HEAD")
    real = rule_check.cat_file_batch

    def flaky(repo_path, specs, **kwargs):
        reads_head = all(spec.startswith(head) for spec in specs)
        return None if reads_head == (failing == "head") else real(repo_path, specs, **kwargs)

    monkeypatch.setattr(rule_check, "cat_file_batch", flaky)
    claim = RuleClaim(texts=("`size_limit`",), quotes=(), scope=("limits.py",), anchor=_RECORDED)

    found = check_rules(repo, {"d": claim})["d"]
    assert (found.verdict, found.basis, found.commit) == ("unknown", "unread", head)
