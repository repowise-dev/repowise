"""search_results filter — grouped-by-file compaction of grep/rg floods."""

from __future__ import annotations

import pytest

from repowise.core.distill import distill_output, estimate_tokens
from repowise.core.distill.filters.search_results import (
    SearchResultsFilter,
    group_search_matches,
    render_search_digest,
)
from repowise.core.distill.router import select_filter


@pytest.fixture(scope="module")
def filt() -> SearchResultsFilter:
    return SearchResultsFilter()


class TestRouting:
    @pytest.mark.parametrize(
        "command",
        ["rg TODO", "grep -rn auth src/", "git grep parse_yaml", "egrep -i err *.txt"],
    )
    def test_command_match(self, command: str) -> None:
        chosen = select_filter(command)
        assert chosen is not None and chosen.name == "search_results"

    @pytest.mark.parametrize("command", ["git status", "ls -la", "pytest -x"])
    def test_other_commands_not_claimed(self, command: str) -> None:
        chosen = select_filter(command)
        assert chosen is None or chosen.name != "search_results"

    def test_content_sniff_on_flood(self, filt, load_fixture) -> None:
        assert filt.matches_content(load_fixture("rg_flood.txt")) is True

    def test_content_sniff_rejects_prose(self, filt) -> None:
        prose = "\n".join(f"line {i} of plain text" for i in range(60))
        assert filt.matches_content(prose) is False


class TestGrouping:
    def test_flat_format(self) -> None:
        out = "src/a.py:10:def foo():\nsrc/a.py:42:def bar():\nsrc/b.py:7:def baz():"
        groups = group_search_matches(out)
        assert groups is not None
        assert list(groups) == ["src/a.py", "src/b.py"]
        assert groups["src/a.py"] == [(10, "def foo():"), (42, "def bar():")]

    def test_headed_rg_format(self) -> None:
        out = "src/a.py\n10:def foo():\n42:def bar():\n\nsrc/b.py\n7:def baz():"
        groups = group_search_matches(out)
        assert groups is not None
        assert groups["src/b.py"] == [(7, "def baz():")]

    def test_windows_drive_paths(self) -> None:
        out = "\n".join(f"C:\\repo\\src\\a.py:{i}:hit {i}" for i in range(1, 11))
        groups = group_search_matches(out)
        assert groups is not None
        assert len(groups["C:\\repo\\src\\a.py"]) == 10

    def test_unparseable_returns_none(self) -> None:
        assert group_search_matches("just some text\nwithout matches") is None
        assert group_search_matches("") is None

    def test_real_fixture_parses(self, load_fixture) -> None:
        groups = group_search_matches(load_fixture("rg_flood.txt"))
        assert groups is not None
        assert len(groups) > 5
        total = sum(len(v) for v in groups.values())
        assert total > 200


class TestRendering:
    def test_digest_has_counts_and_anchors(self, load_fixture) -> None:
        raw = load_fixture("rg_flood.txt")
        groups = group_search_matches(raw)
        digest = render_search_digest(groups)
        head = digest.splitlines()[0]
        assert "matches in" in head and "files:" in head
        # Anchors carry line numbers.
        assert "L" in digest
        # Compaction is real.
        assert estimate_tokens(digest) < estimate_tokens(raw) * 0.5

    def test_file_order_override(self) -> None:
        out = "a.py:1:x\nb.py:1:x\nb.py:2:x\nc.py:1:x"
        groups = group_search_matches(out)
        digest = render_search_digest(groups, file_order=["c.py", "a.py", "b.py"])
        lines = [ln for ln in digest.splitlines() if ln.startswith("  ") and "(" in ln]
        assert lines[0].strip().startswith("c.py")

    def test_hidden_files_footer(self) -> None:
        out = "\n".join(f"f{i}.py:{j}:hit" for i in range(40) for j in range(1, 4))
        groups = group_search_matches(out)
        digest = render_search_digest(groups, max_files=10)
        assert "more files" in digest.splitlines()[-1]


class TestEngine:
    def test_distill_round_trip(self, load_fixture, store) -> None:
        raw = load_fixture("rg_flood.txt")
        result = distill_output(raw, command='rg "def " -n packages', store=store)
        assert result.distilled is True
        assert result.filter_name == "search_results"
        assert result.savings_pct >= 40.0
        assert store.get(result.ref) == raw

    def test_files_only_output_falls_back_raw(self, store) -> None:
        # rg -l style: bare path lines sniff as file_listing territory, and
        # the search filter must raise → engine returns raw via this command.
        raw = "\n".join(f"src/dir{i}/file{i}.py" for i in range(40))
        result = distill_output(raw, command="rg -l TODO", store=store)
        assert result.distilled is False
        assert result.text == raw
