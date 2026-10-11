"""Symbol references: flagged only when git proves the name was a definition."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from repowise.core.analysis.doc_drift import DocDriftAnalyzer
from repowise.core.analysis.doc_drift import symbols as symbols_mod
from repowise.core.analysis.doc_drift.extractor import extract, symbol_name
from repowise.core.analysis.doc_drift.models import DriftKind, DriftVerdict, SymbolScope
from repowise.core.analysis.doc_drift.suggest import NO_SUGGESTION
from repowise.core.analysis.doc_drift.symbols import (
    SymbolOptions,
    SymbolRecheck,
    resolve_symbols,
    rewrite_symbol,
    symbol_recheck,
)

git_required = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


# ---------------------------------------------------------------------------
# Candidate shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("token", "name"),
    [
        ("parse_config()", "parse_config"),
        ("ChangeDetector.detect", "detect"),
        ("my_func", "my_func"),
        ("parseConfig", "parseConfig"),
        ("Graph::addNode", "addNode"),
        ("load()", "load"),
    ],
)
def test_code_shaped_tokens_are_candidates(token: str, name: str):
    assert symbol_name(token) == name


@pytest.mark.parametrize(
    "token",
    ["string", "true", "PATH", "HEAD", "foo", "MAX_DOC_BYTES", "Config", "a.b", "run it", "x()"],
)
def test_words_constants_and_short_names_are_not(token: str):
    assert symbol_name(token) == ""


def test_paths_and_commands_are_never_symbol_candidates():
    text = "See `src/app_main.py`, `config.yaml` and run `make build_all`."
    assert [r.kind for r in extract(text, "docs/x.md")] == [
        DriftKind.PATH,
        DriftKind.PATH,
        DriftKind.COMMAND,
    ]


def test_target_is_the_qualified_token_without_the_call():
    (ref,) = extract("Use `Loader.parse_config()`.", "docs/x.md")
    assert (ref.raw, ref.target) == ("Loader.parse_config()", "Loader.parse_config")


def test_same_name_under_two_qualifiers_stays_two_references():
    refs = extract("`Foo.parse_row` and `Bar.parse_row`", "docs/x.md")
    assert [r.target for r in refs] == ["Foo.parse_row", "Bar.parse_row"]


def test_candidate_records_its_column_on_the_untrimmed_line():
    line = "  - call `parse_config()` first"
    (ref,) = extract(line, "docs/x.md")
    assert line[ref.column : ref.column + len(ref.raw)] == "parse_config()"


def test_rewrite_keeps_qualifier_and_call():
    assert rewrite_symbol("Loader.parse_config()", "parse_config", "parse_conf") == (
        "Loader.parse_conf()"
    )


# ---------------------------------------------------------------------------
# Resolution against a real history
# ---------------------------------------------------------------------------

_APP_BEFORE = '''"""App module."""


class Loader:
    def parse_config(self, path):
        return path


def load_items():
    return []


def keep_me():
    return 1


# fetch_widget is only mentioned here, never defined.
LABEL = "fetch_widget"
'''

_APP_AFTER = '''"""App module."""


class Loader:
    def parse_conf(self, path):
        return path


def keep_me():
    return 1


def helper_fn():
    return 2
'''

_TEST_HELPER = "def make_fixture_repo():\n    return None\n"

_DOC = """# Guide

Call `Loader.parse_config()` first, and `Loader.parse_config()` again.
Then `load_items()`.
Also `keep_me()`.
Never `my_func`.
And `fetch_widget` too.
Finally `helper_fn()`.
Test with `make_fixture_repo()`.
"""


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def history(tmp_path: Path) -> Path:
    for rel, text in {
        "src/app.py": _APP_BEFORE,
        "tests/test_helpers.py": _TEST_HELPER,
        "docs/guide.md": _DOC,
    }.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "write")
    (tmp_path / "src/app.py").write_text(_APP_AFTER, encoding="utf-8")
    (tmp_path / "tests/test_helpers.py").unlink()
    _git(tmp_path, "commit", "-qam", "rename and delete")
    return tmp_path


#: What an index built at HEAD would hold; ``helper_fn`` is missing, as a lagging
#: or narrower index would be.
_INDEXED = frozenset({"parse_conf", "keep_me"})


def _outcomes(root: Path) -> dict[str, object]:
    refs = [r for r in extract(_DOC, "docs/guide.md") if r.kind is DriftKind.SYMBOL]
    outcomes = resolve_symbols(refs, root=root, symbol_names=_INDEXED)
    return {ref.target: outcome for ref, outcome in zip(refs, outcomes, strict=True)}


@git_required
def test_renamed_symbol_is_missing_with_the_rename_suggested(history: Path):
    res, miss = _outcomes(history)["Loader.parse_config"]
    assert res.verdict is DriftVerdict.MISSING
    assert (res.origin, res.detail) == ("symbol_no_definition", "no-definition")
    assert miss.evidence.startswith("defined in src/app.py at ")
    assert miss.defined_in == ("src/app.py",)
    assert miss.suggestion == ("Loader.parse_conf()", "symbol_rename")


@git_required
def test_deleted_symbol_is_missing_without_a_suggestion(history: Path):
    res, miss = _outcomes(history)["load_items"]
    assert res.verdict is DriftVerdict.MISSING
    assert miss.suggestion == NO_SUGGESTION


@git_required
def test_names_that_never_named_a_symbol_are_not_references(history: Path):
    outcomes = _outcomes(history)
    assert outcomes["my_func"] is None
    # Only a comment and a string at the commit: the parser finds no definition.
    assert outcomes["fetch_widget"] is None
    # Defined only in a test file at the commit.
    assert outcomes["make_fixture_repo"] is None
    # Still mentioned in a non-document file, though the index lacks it.
    assert outcomes["helper_fn"] is None


@git_required
def test_indexed_symbol_resolves(history: Path):
    res, miss = _outcomes(history)["keep_me"]
    assert res.verdict is DriftVerdict.RESOLVED
    assert miss is None


@git_required
def test_work_past_a_limit_is_over_budget(history: Path, monkeypatch):
    monkeypatch.setattr(symbols_mod, "_MAX_DOCUMENTS", 0)
    res, _ = _outcomes(history)["load_items"]
    assert (res.verdict, res.detail) == (DriftVerdict.UNCHECKABLE, "over-budget")


@git_required
def test_suggestion_follows_a_moved_defining_file(history: Path):
    (history / "src/core").mkdir()
    _git(history, "mv", "src/app.py", "src/core/app.py")
    _git(history, "commit", "-qm", "move")
    res, miss = _outcomes(history)["Loader.parse_config"]
    assert res.verdict is DriftVerdict.MISSING
    assert miss.suggestion == ("Loader.parse_conf()", "symbol_rename")
    assert miss.defined_in == ("src/app.py", "src/core/app.py")


@git_required
def test_two_equally_close_new_names_are_no_suggestion(history: Path):
    app = history / "src/app.py"
    app.write_text(
        _APP_AFTER.replace("def parse_conf(", "def parse_confa(")
        + "\n\nclass Other:\n    def parse_confb(self):\n        return 0\n",
        encoding="utf-8",
    )
    _git(history, "commit", "-qam", "two candidates")
    _res, miss = _outcomes(history)["Loader.parse_config"]
    assert miss.suggestion == NO_SUGGESTION


@git_required
def test_uncommitted_doc_line_is_uncheckable(history: Path):
    doc = history / "docs/guide.md"
    doc.write_text(_DOC + "New `load_items` line.\n", encoding="utf-8")
    refs = [
        r
        for r in extract(doc.read_text(encoding="utf-8"), "docs/guide.md")
        if r.kind is DriftKind.SYMBOL and r.line == 10
    ]
    ((res, _),) = resolve_symbols(refs, root=history, symbol_names=_INDEXED)
    assert (res.verdict, res.detail) == (DriftVerdict.UNCHECKABLE, "uncommitted")


# ---------------------------------------------------------------------------
# Incremental recheck
# ---------------------------------------------------------------------------


@git_required
def test_recheck_reads_names_from_changed_lines_of_non_documents(history: Path):
    base = _git(history, "rev-list", "--max-parents=0", "HEAD")
    recheck = symbol_recheck(history, base, ["src/app.py"])
    assert {"load_items", "parse_config", "parse_conf"} <= recheck.names
    # A name only a document changed is not a trigger.
    assert "Guide" not in recheck.names
    assert recheck.wants("docs/guide.md", "load_items")
    assert not recheck.wants("docs/other.md", "unrelated_name")


def test_recheck_always_wants_a_changed_document():
    recheck = SymbolRecheck(frozenset({"docs/a.md"}), frozenset())
    assert recheck.whole("docs/a.md")
    assert not recheck.wants("docs/b.md", "x_y")
    assert SymbolRecheck(frozenset(), None).whole("docs/b.md")


@git_required
def test_an_untouched_document_is_not_reresolved(history: Path):
    analyzer = DocDriftAnalyzer(
        source_map={"docs/guide.md": _DOC.encode()},
        tracked_paths={"docs/guide.md", "src/app.py"},
        repo_root=history,
        symbols=SymbolOptions(_INDEXED, SymbolRecheck(frozenset(), frozenset({"unrelated_name"}))),
    )
    report = analyzer.analyze()
    assert report.symbol_scope == SymbolScope()
    assert not [f for f in report.findings if f.kind is DriftKind.SYMBOL]


@git_required
def test_a_removed_line_name_rechecks_only_that_reference(history: Path):
    analyzer = DocDriftAnalyzer(
        source_map={"docs/guide.md": _DOC.encode()},
        tracked_paths={"docs/guide.md", "src/app.py"},
        repo_root=history,
        symbols=SymbolOptions(_INDEXED, SymbolRecheck(frozenset(), frozenset({"load_items"}))),
    )
    report = analyzer.analyze()
    assert report.symbol_scope == SymbolScope(references=frozenset({("docs/guide.md", "load_items")}))
    assert {f.target for f in report.findings if f.kind is DriftKind.SYMBOL} == {"load_items"}


@git_required
def test_a_changed_document_is_rechecked_whole(history: Path):
    analyzer = DocDriftAnalyzer(
        source_map={"docs/guide.md": _DOC.encode()},
        tracked_paths={"docs/guide.md", "src/app.py"},
        repo_root=history,
        symbols=SymbolOptions(_INDEXED, SymbolRecheck(frozenset({"docs/guide.md"}), frozenset())),
    )
    report = analyzer.analyze()
    assert report.symbol_scope == SymbolScope(documents=frozenset({"docs/guide.md"}))
    assert len([f for f in report.findings if f.kind is DriftKind.SYMBOL]) == 2


# ---------------------------------------------------------------------------
# Through the analyzer
# ---------------------------------------------------------------------------


@git_required
def test_analyzer_places_the_suggestion_on_every_copy(history: Path):
    analyzer = DocDriftAnalyzer(
        source_map={"docs/guide.md": _DOC.encode()},
        tracked_paths={"docs/guide.md", "src/app.py"},
        repo_root=history,
        symbols=SymbolOptions(_INDEXED),
    )
    report = analyzer.analyze()
    symbols = {f.target: f for f in report.findings if f.kind is DriftKind.SYMBOL}
    assert set(symbols) == {"Loader.parse_config", "load_items"}
    renamed = symbols["Loader.parse_config"]
    assert renamed.suggestion == "Loader.parse_conf()"
    assert renamed.suggested_line == (
        "Call `Loader.parse_conf()` first, and `Loader.parse_conf()` again."
    )
    # 1-based, end-exclusive: both copies of ``Loader.parse_config()``.
    assert renamed.suggestion_columns == ((7, 28), (42, 63))
    assert any(line.startswith("defined in src/app.py at ") for line in renamed.evidence)
    assert symbols["load_items"].suggested_line == ""
    # Mentioned or never-defined candidates are not references at all.
    assert report.verdict_summary == {
        "resolved": 1,
        "missing": 2,
        "ambiguous": 0,
        "uncheckable": 0,
    }
    assert report.symbol_scope is None


def test_without_symbol_names_the_symbol_kind_does_not_run(tmp_path: Path):
    analyzer = DocDriftAnalyzer(
        source_map={"docs/guide.md": _DOC.encode()},
        tracked_paths={"docs/guide.md"},
        repo_root=tmp_path,
    )
    report = analyzer.analyze()
    assert report.references_checked == 0
    assert report.findings == []


def test_a_call_only_candidate_looks_up_its_name(tmp_path: Path):
    """``load()`` is a candidate only for its ``()``; the lookup must still find ``load``."""
    (ref,) = extract("Call `load()`.", "docs/x.md")
    assert ref.target == "load"
    ((res, _),) = resolve_symbols([ref], root=tmp_path, symbol_names={"load"})
    assert res.verdict is DriftVerdict.RESOLVED
