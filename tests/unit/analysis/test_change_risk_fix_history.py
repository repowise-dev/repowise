"""Fix-history pressure: the size-orthogonal half of the change-risk answer.

The score restates diff size, so these tests are mostly about the property the
score does not have: a small change to a file that keeps breaking must read as
carrying more fix history than a large change to files that never have.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from repowise.core.analysis.change_risk import (
    BaselineSample,
    FixHistoryUnavailableError,
    change_fix_density,
    change_risk_payload,
    clear_fix_pressure_cache,
    densities_excluding,
    fix_density_percentile,
    fix_pressure,
    score_live_change,
)
from repowise.core.analysis.change_risk.fix_history import rename_target


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _commit(repo: Path, files: dict[str, str], message: str) -> str:
    for rel, content in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        _git(["add", rel], repo)
    _git(["-c", "user.name=Dev", "-c", "user.email=d@e.com", "commit", "-m", message], repo)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_fix_pressure_cache()
    yield
    clear_fix_pressure_cache()


@pytest.fixture
def repo_with_history(tmp_path: Path) -> Path:
    """A repo where ``hot.py`` has broken repeatedly and ``cold.py`` never has."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _git(["config", "user.name", "Dev"], repo)
    _git(["config", "user.email", "d@e.com"], repo)
    _commit(repo, {"hot.py": "v = 0\n", "cold.py": "c = 0\n"}, "feat: seed")
    for i in range(1, 4):
        _commit(repo, {"hot.py": f"v = {i}\n"}, f"fix: crash number {i}")
    # Feature work on cold.py, so it has churn history but no fix history.
    for i in range(1, 4):
        _commit(repo, {"cold.py": f"c = {i}\n"}, f"feat: add capability {i}")
    return repo


def test_fix_pressure_skips_fix_shaped_shallow_boundary(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _git(["init", "-q"], source)
    _git(["config", "user.name", "Dev"], source)
    _git(["config", "user.email", "d@e.com"], source)

    _commit(
        source,
        {"hot.py": "v = 0\n", "cold.py": "c = 0\n"},
        "feat: seed",
    )
    _commit(source, {"hot.py": "v = 1\n"}, "feat: prepare")
    _commit(source, {"hot.py": "v = 2\n"}, "fix: boundary bug")
    _commit(source, {"hot.py": "v = 3\n"}, "fix: later bug")
    _commit(source, {"cold.py": "c = 1\n"}, "feat: latest")

    shallow = tmp_path / "shallow"
    subprocess.run(
        [
            "git",
            "clone",
            "-q",
            "--depth=3",
            source.resolve().as_uri(),
            str(shallow),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    shallow_pressure = fix_pressure(str(shallow), "HEAD")
    full_pressure = fix_pressure(str(source), "HEAD")

    assert "cold.py" not in shallow_pressure
    assert set(shallow_pressure) <= set(full_pressure)
    assert shallow_pressure["hot.py"] < full_pressure["hot.py"]


def test_fix_pressure_keeps_parentless_root_in_full_repo(tmp_path: Path) -> None:
    repo = tmp_path / "full"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _git(["config", "user.name", "Dev"], repo)
    _git(["config", "user.email", "d@e.com"], repo)

    _commit(repo, {"root.py": "value = 1\n"}, "fix: seed repository")

    pressure = fix_pressure(str(repo), "HEAD")

    assert pressure["root.py"] == pytest.approx(1.0, abs=0.05)


def test_fix_pressure_counts_only_fix_commits(repo_with_history: Path) -> None:
    pressure = fix_pressure(str(repo_with_history), "HEAD")
    assert pressure["hot.py"] == pytest.approx(3.0, abs=0.05)
    assert "cold.py" not in pressure  # churn is not fix history


def test_fix_density_is_orthogonal_to_diff_size(repo_with_history: Path) -> None:
    """One line in the fragile file outranks a thousand in the safe one."""
    pressure = fix_pressure(str(repo_with_history), "HEAD")
    surgical = change_fix_density(pressure, [("hot.py", 1)])
    bulk = change_fix_density(pressure, [("cold.py", 1000)])
    assert surgical > bulk
    assert bulk == 0.0


def test_fix_density_is_churn_weighted(repo_with_history: Path) -> None:
    """The file a change mostly edits dominates, not a one-line drive-by."""
    pressure = fix_pressure(str(repo_with_history), "HEAD")
    mostly_hot = change_fix_density(pressure, [("hot.py", 90), ("cold.py", 10)])
    mostly_cold = change_fix_density(pressure, [("hot.py", 10), ("cold.py", 90)])
    assert mostly_hot > mostly_cold


def test_fix_history_excludes_the_change_being_scored(repo_with_history: Path) -> None:
    """A commit is never credited with fix history it created itself."""
    sha = _commit(repo_with_history, {"hot.py": "v = 99\n"}, "fix: one more crash")
    before = fix_pressure(str(repo_with_history), f"{sha}^")
    after = fix_pressure(str(repo_with_history), sha)
    assert after["hot.py"] > before["hot.py"]
    result = score_live_change(str(repo_with_history), sha, baseline=0)
    # Scored against the parent's record — three prior fixes, not four.
    assert result.fix_density == pytest.approx(before["hot.py"], abs=0.05)


def test_percentile_needs_enough_commits_to_rank_against() -> None:
    assert fix_density_percentile([1.0, 2.0, 3.0], 2.5) is None
    assert fix_density_percentile([float(i) for i in range(8)], 2.5) is not None


def test_percentile_is_none_when_the_change_touches_no_fix_history() -> None:
    """Not "0th percentile" — the question does not apply, and 0 reads as a rank."""
    population = [float(i + 1) for i in range(10)]
    assert fix_density_percentile(population, 0.0) is None
    assert fix_density_percentile(population, 5.5) is not None


def test_percentile_ranks_a_multi_file_change_against_whole_commits() -> None:
    """The population is per-commit densities, so averaging is not a penalty.

    The old population was per-*file* pressures. A change spread over several
    files averages below the lowest single per-file value that exists anywhere,
    so it fell under the whole population and pinned to 0. The same change
    against per-commit densities lands where its ground actually sits.
    """
    pressure = {"hot.py": 5.0, "warm.py": 1.0, "cold.py": 0.0}
    spread = change_fix_density(pressure, [("hot.py", 10), ("cold.py", 10)])
    assert spread == pytest.approx(2.5)

    per_file = sorted(v for v in pressure.values() if v > 0)  # the old population
    assert spread > per_file[0]  # ... which this change would still clear here

    # The real repo has no per-file value below 0.75, so a wider spread does not.
    diluted = change_fix_density(pressure, [("hot.py", 1), ("cold.py", 20)])
    assert diluted < min(per_file)

    # Under the old per-file population it would have sat below every member
    # and pinned to 0. Against whole commits it ranks above the zero-density
    # ones and below the rest.
    population = [0.0, 0.0, 0.4, 0.9, 1.4, 2.5, 3.1, 4.0, 4.4, 5.0]
    assert fix_density_percentile(population, diluted) == pytest.approx(20.0)


def test_zero_density_commits_stay_in_the_population() -> None:
    """They are legitimate members; dropping them would inflate every rank."""
    pressure = {"hot.py": 4.0}
    samples = [
        BaselineSample(f"{i:040x}", 1.0, (("cold.py", 10),))  # touches no fix history
        for i in range(6)
    ] + [
        BaselineSample(f"{i:040x}", 1.0, (("hot.py", 10),)) for i in range(6, 10)
    ]

    population = densities_excluding(samples, "", pressure)

    assert population.count(0.0) == 6  # the zero-density commits survived the walk
    assert len(population) == 10


def test_percentile_uses_the_repos_own_commits(repo_with_history: Path) -> None:
    """End to end: a change to the fragile file outranks one to the safe file."""
    hot_sha = _commit(repo_with_history, {"hot.py": "v = 9\n"}, "feat: touch the hot file")
    cold_sha = _commit(repo_with_history, {"cold.py": "c = 9\n"}, "feat: touch the cold file")
    # Enough commits for the sample to clear MIN_DENSITY_POPULATION.
    for i in range(4, 8):
        _commit(repo_with_history, {"cold.py": f"c = {i}\n"}, f"feat: add capability {i}")

    hot = score_live_change(str(repo_with_history), hot_sha, baseline=200)
    cold = score_live_change(str(repo_with_history), cold_sha, baseline=200)

    assert hot.fix_percentile is not None
    assert hot.fix_percentile > 0.0
    assert cold.fix_percentile is None  # touches no fix history at all


def test_decay_is_anchored_to_the_change_not_to_the_latest_fix(tmp_path: Path) -> None:
    """A file's pressure must not move because an unrelated file was fixed.

    Anchoring decay to the newest fix anywhere in the repo would rescale every
    value whenever any fix landed, so the same commit would score differently
    on two runs and two commits would not be comparable.
    """
    repo = tmp_path / "anchored"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _git(["config", "user.name", "Dev"], repo)
    _git(["config", "user.email", "d@e.com"], repo)
    _commit(repo, {"a.py": "a = 0\n", "b.py": "b = 0\n"}, "feat: seed")
    _commit(repo, {"a.py": "a = 1\n"}, "fix: a breaks")
    sha = _commit(repo, {"a.py": "a = 2\n"}, "refactor: tidy a")
    before = fix_pressure(str(repo), sha)["a.py"]

    clear_fix_pressure_cache()
    _commit(repo, {"b.py": "b = 1\n"}, "fix: b breaks, unrelated to a")
    after = fix_pressure(str(repo), sha)["a.py"]
    assert after == pytest.approx(before)


@pytest.mark.parametrize(
    ("numstat_path", "expected"),
    [
        ("src/{old => new}.py", "src/new.py"),
        ("old.py => new.py", "new.py"),
        ("{a => b}/mod.py", "b/mod.py"),
        ("plain/path.py", "plain/path.py"),
    ],
)
def test_rename_target_resolves_numstat_rename_syntax(numstat_path: str, expected: str) -> None:
    """A moved-and-edited file must not lose its fix record to path syntax."""
    assert rename_target(numstat_path) == expected


def test_failed_walk_raises_rather_than_reporting_no_history(tmp_path: Path) -> None:
    """An empty record and a failed lookup must not read the same."""
    not_a_repo = tmp_path / "empty"
    not_a_repo.mkdir()
    with pytest.raises(FixHistoryUnavailableError):
        fix_pressure(str(not_a_repo), "HEAD", depth=10)


def test_payload_leads_with_fix_history(repo_with_history: Path) -> None:
    _commit(repo_with_history, {"hot.py": "v = 42\n"}, "refactor: tidy")
    payload = change_risk_payload(score_live_change(str(repo_with_history), "HEAD", baseline=0))
    block = payload["fix_history"]
    assert block["density"] > 0
    assert [f["path"] for f in block["files"]] == ["hot.py"]
    assert block["files"][0]["fix_pressure"] > 0
    # The score is still emitted, but says what it measures.
    assert "size" in payload["score_measures"]


def test_hot_files_are_ordered_by_pressure(tmp_path: Path) -> None:
    repo = tmp_path / "multi"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _git(["config", "user.name", "Dev"], repo)
    _git(["config", "user.email", "d@e.com"], repo)
    _commit(repo, {"a.py": "a = 0\n", "b.py": "b = 0\n"}, "feat: seed")
    for i in range(1, 5):
        _commit(repo, {"a.py": f"a = {i}\n"}, f"fix: a breaks {i}")
    _commit(repo, {"b.py": "b = 1\n"}, "fix: b breaks once")
    _commit(repo, {"a.py": "a = 9\n", "b.py": "b = 9\n"}, "refactor: touch both")

    result = score_live_change(str(repo), "HEAD", baseline=0)
    assert [path for path, _, _ in result.hot_files] == ["a.py", "b.py"]


def test_repo_without_fix_history_reports_nothing(tmp_path: Path) -> None:
    repo = tmp_path / "clean"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _git(["config", "user.name", "Dev"], repo)
    _git(["config", "user.email", "d@e.com"], repo)
    _commit(repo, {"a.py": "a = 0\n"}, "feat: seed")
    _commit(repo, {"a.py": "a = 1\n"}, "feat: extend")

    result = score_live_change(str(repo), "HEAD", baseline=0)
    assert result.fix_density == 0.0
    assert result.hot_files == ()
    assert result.fix_percentile is None
