"""The pure parts of ``scripts/release_stats.py``: git output parsing, counting,
the first-time split and the rendered blocks."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _script():
    path = ROOT / "scripts" / "release_stats.py"
    spec = importlib.util.spec_from_file_location("release_stats", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["release_stats"] = module
    spec.loader.exec_module(module)
    return module


rs = _script()


def _log(*commits: tuple[str, str, str, str, str]) -> str:
    return "".join(rs._FIELD_SEP.join(c) + rs._COMMIT_SEP + "\n" for c in commits)


def test_parse_log():
    text = _log(
        ("a1", "Ada", "ada@x", "feat(web): a thing (#12)", "feat(web): a thing (#12)\n\nFixes #3"),
        ("b2", "Bo", "bo@x", "fix: other (#13)", "fix: other (#13)"),
    )
    commits = rs.parse_log(text)
    assert [c["sha"] for c in commits] == ["a1", "b2"]
    assert commits[0]["body"].endswith("Fixes #3")


def test_parse_shortstat():
    assert rs.parse_shortstat(" 12 files changed, 300 insertions(+), 40 deletions(-)\n") == (
        12,
        300,
        40,
    )
    assert rs.parse_shortstat(" 1 file changed, 2 insertions(+)") == (1, 2, 0)
    assert rs.parse_shortstat(" 1 file changed, 5 deletions(-)") == (1, 0, 5)
    assert rs.parse_shortstat("") == (0, 0, 0)


def test_counts():
    subjects = [
        "feat(web): one (#1)",
        "fix: two (#2)",
        "fix(core)!: three (#3)",
        "perf: four (#4)",
        "refactor(x): five",
        "docs: six (#6)",
        "chore(deps): seven (#7)",
        "Merge branch main",
        "fix: two again (#2)",
    ]
    assert rs.count_types(subjects) == {
        "fixes": 3,
        "features": 1,
        "perf": 1,
        "refactors": 1,
        "docs": 1,
    }
    assert rs.count_prs(subjects) == 6
    bodies = ["Fixes #10 and closes #11", "Resolved #10", "see #12", "fixed #13"]
    assert rs.count_issues(bodies) == 3


def test_is_test_path():
    assert rs.is_test_path("tests/unit/test_x.py")
    assert rs.is_test_path("packages/web/src/a.test.tsx")
    assert rs.is_test_path("pkg/foo_test.go")
    assert not rs.is_test_path("packages/core/src/attest.py")
    assert not rs.is_test_path("docs/testing.md")


def test_split_first_time_drops_unknown_lookups():
    returning, first = rs.split_first_time(
        ["zed", "Amy", "bob", "amy-unknown"], {"zed": 4, "Amy": 0, "bob": 1, "amy-unknown": None}
    )
    assert returning == ["bob", "zed"]
    assert first == ["Amy"]


def test_maintainers_and_bots():
    assert rs.is_maintainer("RaghavChamadiya", "anything")
    assert rs.is_maintainer(None, "Swati")
    assert not rs.is_maintainer("someone", "Raghav")
    assert rs.is_bot("dependabot[bot]", "x")


_STATS = {
    "since": "0.56.0",
    "days": 3,
    "prs": 1205,
    "contributors": 24,
    "first_time_contributors": 11,
    "files_changed": 1226,
    "lines_added": 103206,
    "lines_removed": 11127,
    "types": {"fixes": 147, "features": 45, "perf": 2, "refactors": 4, "docs": 6},
    "test_files_touched": 437,
    "test_files_new": 144,
    "issues_closed": 25,
}


def test_render_markdown_has_no_names():
    md = rs.render_markdown(_STATS)
    assert md.startswith("### By the numbers\n\nIn the 3 days since 0.56.0:")
    assert "- **1,205 pull requests** merged, from **24 contributors**, **11 of them" in md
    assert "**+103,206 / -11,127** lines" in md
    assert "**147 fixes, 45 features, 2 performance changes**, 4 refactors and 6" in md
    assert "@" not in md


def test_render_markdown_without_github_omits_first_time():
    md = rs.render_markdown({**_STATS, "first_time_contributors": None, "days": 1})
    assert "first-time" not in md
    assert "In the 1 day since" in md


def test_render_thanks():
    text = rs.render_thanks(["a", "b"], ["c"])
    assert "@a and @b, thank you" in text
    assert "**1 person whose first contribution landed in this release**: @c." in text
    assert rs.render_thanks([], []) == ""


def test_write_json_merges_by_version(tmp_path):
    path = tmp_path / "release_stats.json"
    rs.write_json("0.56.0", {"prs": 1}, path)
    rs.write_json("unreleased", {"prs": 2}, path)
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "0.56.0": {"prs": 1},
        "unreleased": {"prs": 2},
    }
    # Releasing replaces the Unreleased figures.
    rs.write_json("0.57.0", {"prs": 3}, path)
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "0.56.0": {"prs": 1},
        "0.57.0": {"prs": 3},
    }


def test_render_markdown_omits_zero_kinds():
    types = {"fixes": 147, "features": 45, "perf": 0, "refactors": 4, "docs": 0}
    md = rs.render_markdown({**_STATS, "types": types})
    assert "- **147 fixes, 45 features**, 4 refactors\n" in md
    assert "performance" not in md and "documentation" not in md
    none = {"fixes": 0, "features": 0, "perf": 0, "refactors": 0, "docs": 0}
    assert "fixes" not in rs.render_markdown({**_STATS, "types": none})
    only = {"fixes": 3, "features": 0, "perf": 1, "refactors": 0, "docs": 0}
    assert "- **3 fixes and 1 performance changes**\n" in rs.render_markdown({**_STATS, "types": only})
