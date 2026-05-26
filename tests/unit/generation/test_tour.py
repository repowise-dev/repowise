"""Tests for the topology-driven guided tour (generation.tour)."""

from __future__ import annotations

from dataclasses import dataclass, field

from repowise.core.generation.tour import (
    DEFAULT_MAX_STOPS,
    build_tour,
    score_entry_points,
    tour_landmark_paths,
)


@dataclass
class _FI:
    path: str
    is_entry_point: bool = False


@dataclass
class _PF:
    file_info: _FI


def _repo(paths_entry: dict[str, bool]) -> list[_PF]:
    return [_PF(_FI(path=p, is_entry_point=e)) for p, e in paths_entry.items()]


# ---------------------------------------------------------------------------
# Entry-point scoring
# ---------------------------------------------------------------------------


def test_score_entry_points_rewards_entry_flag_and_filename():
    files = _repo({"src/main.py": True, "src/helpers/util.py": False})
    pr = {"src/main.py": 0.9, "src/helpers/util.py": 0.1}
    scored = score_entry_points(files, pr)
    ranked = [p for _, p in scored]
    assert ranked[0] == "src/main.py"  # entry flag + main filename + shallow


def test_score_entry_points_excludes_zero_score():
    files = _repo({"deep/nested/pkg/thing.py": False})
    pr = {"deep/nested/pkg/thing.py": 0.0}
    assert score_entry_points(files, pr) == []


# ---------------------------------------------------------------------------
# Landmarks
# ---------------------------------------------------------------------------


def test_tour_landmark_paths_is_bounded():
    files = _repo({f"src/app_{i}.py": True for i in range(20)})
    pr = {f"src/app_{i}.py": 1.0 - i * 0.01 for i in range(20)}
    landmarks = tour_landmark_paths(files, pr, max_landmarks=3)
    assert len(landmarks) == 3


# ---------------------------------------------------------------------------
# build_tour
# ---------------------------------------------------------------------------


def test_build_tour_only_references_documented_pages():
    files = _repo({"main.py": True, "a.py": False, "b.py": False, "undocumented.py": False})
    pr = {"main.py": 0.9, "a.py": 0.5, "b.py": 0.3, "undocumented.py": 0.2}
    edges = [("main.py", "a.py"), ("a.py", "b.py"), ("main.py", "undocumented.py")]
    documented = {"main.py", "a.py", "b.py"}  # undocumented.py NOT selected
    stops = build_tour(
        files, pr, edges, file_page_paths=documented, repo_name="myrepo"
    )
    targets = {s.target_path for s in stops}
    assert "undocumented.py" not in targets
    assert "myrepo" in targets  # overview opens the tour


def test_build_tour_orders_by_bfs_depth():
    files = _repo({"main.py": True, "a.py": False, "b.py": False})
    pr = {"main.py": 0.9, "a.py": 0.5, "b.py": 0.3}
    edges = [("main.py", "a.py"), ("a.py", "b.py")]
    stops = build_tour(files, pr, edges, file_page_paths={"main.py", "a.py", "b.py"})
    code_stops = [s for s in stops if s.kind == "code"]
    order = [s.target_path for s in code_stops]
    assert order == ["main.py", "a.py", "b.py"]
    assert [s.depth for s in code_stops] == [0, 1, 2]


def test_build_tour_weaves_infra_last():
    files = _repo({"main.py": True, "a.py": False})
    pr = {"main.py": 0.9, "a.py": 0.5, "Dockerfile": 0.1}
    edges = [("main.py", "a.py")]
    stops = build_tour(
        files,
        pr,
        edges,
        file_page_paths={"main.py", "a.py"},
        infra_paths={"Dockerfile"},
        repo_name="r",
    )
    assert stops[-1].kind == "infra"
    assert stops[-1].target_path == "Dockerfile"


def test_build_tour_respects_max_stops():
    files = _repo({f"f{i}.py": (i == 0) for i in range(50)})
    pr = {f"f{i}.py": 1.0 - i * 0.01 for i in range(50)}
    edges = [(f"f{i}.py", f"f{i+1}.py") for i in range(49)]
    documented = {f"f{i}.py" for i in range(50)}
    stops = build_tour(files, pr, edges, file_page_paths=documented, repo_name="r")
    assert len(stops) <= DEFAULT_MAX_STOPS
