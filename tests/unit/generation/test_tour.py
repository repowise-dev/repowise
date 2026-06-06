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
    language: str = "python"


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


def test_score_entry_points_withholds_stem_bonus_from_docs():
    # docs/index.md has an entry-style stem but is markdown — it must never
    # outrank a real main.py, even when ingestion's stem rule flagged it.
    files = [
        _PF(_FI(path="docs/index.md", language="markdown", is_entry_point=True)),
        _PF(_FI(path="src/main.py")),
    ]
    pr = {"docs/index.md": 0.9, "src/main.py": 0.5}
    scored = {p: s for s, p in score_entry_points(files, pr)}
    assert scored["src/main.py"] >= 3.0
    assert scored.get("docs/index.md", 0.0) < 3.0


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


def test_build_tour_unreached_files_get_honest_reasons():
    # disconnected.py is never reached from the entry point — its reason must
    # not claim it was "reached N imports deep".
    files = _repo({"main.py": True, "a.py": False, "disconnected.py": False})
    pr = {"main.py": 0.9, "a.py": 0.5, "disconnected.py": 0.1}
    edges = [("main.py", "a.py")]
    stops = build_tour(
        files, pr, edges, file_page_paths={"main.py", "a.py", "disconnected.py"}
    )
    by_path = {s.target_path: s for s in stops}
    assert "Off the import path" in by_path["disconnected.py"].reason
    assert "Reached" not in by_path["disconnected.py"].reason
    assert "Directly used" in by_path["a.py"].reason  # reached ones unchanged


def test_score_entry_points_withholds_entry_bonuses_from_examples():
    # examples/*/main.go are entries by *name* only — the library itself
    # must outrank them.
    files = [
        _PF(_FI(path="_examples/hello-world/main.go", is_entry_point=True)),
        _PF(_FI(path="cmd/app/main.go", is_entry_point=True)),
    ]
    pr = {"_examples/hello-world/main.go": 0.9, "cmd/app/main.go": 0.5}
    scored = {p: s for s, p in score_entry_points(files, pr)}
    assert scored["cmd/app/main.go"] >= 3.0
    assert scored.get("_examples/hello-world/main.go", 0.0) < 3.0


def test_score_entry_points_withholds_entry_bonuses_from_test_files():
    # tests/testserver/server.py has an entry-style stem, but a test fixture
    # must never seed the onboarding walk.
    files = [
        _PF(_FI(path="tests/testserver/server.py", is_entry_point=True)),
        _PF(_FI(path="src/pkg/models.py")),
    ]
    pr = {"tests/testserver/server.py": 0.9, "src/pkg/models.py": 0.5}
    scored = {p: s for s, p in score_entry_points(files, pr)}
    assert scored.get("tests/testserver/server.py", 0.0) < 3.0


def test_build_tour_seedless_repo_reasons_do_not_overclaim():
    # A flat library with no entry-style file: steps must not reference
    # entry points that don't exist.
    files = _repo({"src/pkg/models.py": False, "src/pkg/cookies.py": False})
    pr = {"src/pkg/models.py": 0.8, "src/pkg/cookies.py": 0.6}
    stops = build_tour(
        files, pr, [], file_page_paths={"src/pkg/models.py", "src/pkg/cookies.py"}
    )
    for s in stops:
        assert "An entry point" not in s.reason
        # the offending phrase referenced entry points that don't exist;
        # evidence-free parked files now say "Off the import paths walked
        # above" instead, which references only the walk itself
        assert "from the entry points" not in s.reason


def test_build_tour_seedless_anchor_is_a_code_file():
    # The fallback anchor must skip docs/config — a CLAUDE.md at the root
    # outscores deep code files but cannot anchor an import walk.
    files = [
        _PF(_FI(path=".claude/CLAUDE.md", language="markdown")),
        _PF(_FI(path="src/pkg/api.py")),
        _PF(_FI(path="src/pkg/models.py")),
    ]
    pr = {".claude/CLAUDE.md": 0.9, "src/pkg/api.py": 0.5, "src/pkg/models.py": 0.4}
    edges = [("src/pkg/api.py", "src/pkg/models.py")]
    documented = {".claude/CLAUDE.md", "src/pkg/api.py", "src/pkg/models.py"}
    stops = build_tour(files, pr, edges, file_page_paths=documented)
    anchor = next(s for s in stops if s.depth == 0 and s.kind == "code")
    assert anchor.target_path == "src/pkg/api.py"


def test_build_tour_respects_max_stops():
    files = _repo({f"f{i}.py": (i == 0) for i in range(50)})
    pr = {f"f{i}.py": 1.0 - i * 0.01 for i in range(50)}
    edges = [(f"f{i}.py", f"f{i+1}.py") for i in range(49)]
    documented = {f"f{i}.py" for i in range(50)}
    stops = build_tour(files, pr, edges, file_page_paths=documented, repo_name="r")
    assert len(stops) <= DEFAULT_MAX_STOPS


def test_score_entry_points_withholds_bonuses_from_api_contracts_and_infra():
    # Schema/data languages (graphql, proto, sql, openapi) and
    # infra wiring (shell, terraform, dockerfile) never earn entry bonuses,
    # however entry-like their stems are.
    files = [
        _PF(_FI(path="index.graphql", language="graphql", is_entry_point=True)),
        _PF(_FI(path="main.sql", language="sql", is_entry_point=True)),
        _PF(_FI(path="run.sh", language="shell", is_entry_point=True)),
        _PF(_FI(path="main.tf", language="terraform", is_entry_point=True)),
        _PF(_FI(path="src/main.py")),
    ]
    pr = {p.file_info.path: 0.5 for p in files}
    scored = {p: s for s, p in score_entry_points(files, pr)}
    assert scored["src/main.py"] >= 3.0
    for path in ("index.graphql", "main.sql", "run.sh", "main.tf"):
        assert scored.get(path, 0.0) < 3.0, path


def test_build_tour_anchor_reasons_never_claim_entry_points():
    # Anchor-seeded walks (no genuine entries) must not say "the entry
    # points above" — there are none.
    files = [
        _PF(_FI(path="src/core.py")),
        _PF(_FI(path="src/helper.py")),
        _PF(_FI(path="src/deep.py")),
    ]
    pr = {"src/core.py": 0.5, "src/helper.py": 0.3, "src/deep.py": 0.2}
    edges = [("src/core.py", "src/helper.py"), ("src/helper.py", "src/deep.py")]
    documented = {"src/core.py", "src/helper.py", "src/deep.py"}
    stops = build_tour(files, pr, edges, file_page_paths=documented)
    for s in stops:
        assert "entry point" not in s.reason or s.depth == 0, s.reason
    anchor = next(s for s in stops if s.depth == 0)
    assert "anchor" in anchor.reason
    d1 = next(s for s in stops if s.depth == 1)
    assert "anchor" in d1.reason and "entry points" not in d1.reason


def test_build_tour_unreached_non_code_files_take_no_slot():
    # D-037 watch item: sparse-mode walks filled their budget with
    # CHANGELOG.md / *.toml "not on the resolved import paths" slots.
    # Unreached documents and config can never be on an import path —
    # they are not worth a step that displaces code.
    files = [
        _PF(_FI(path="lib/init.lua", language="luau", is_entry_point=True)),
        _PF(_FI(path="lib/util.lua", language="luau")),
        _PF(_FI(path="CHANGELOG.md", language="markdown")),
        _PF(_FI(path="wally.toml", language="toml")),
    ]
    pr = {p: 0.25 for p in ("lib/init.lua", "lib/util.lua", "CHANGELOG.md", "wally.toml")}
    edges = [("lib/init.lua", "lib/util.lua")]
    documented = {"lib/init.lua", "lib/util.lua", "CHANGELOG.md", "wally.toml"}
    stops = build_tour(files, pr, edges, file_page_paths=documented, graph_mode="sparse")
    paths = {s.target_path for s in stops}
    assert "CHANGELOG.md" not in paths
    assert "wally.toml" not in paths
    assert {"lib/init.lua", "lib/util.lua"} <= paths


def test_build_tour_reached_config_keeps_its_slot():
    # A config file genuinely on an import path (TS importing data.json)
    # keeps its step — only *unreached* non-code is exempt from parking.
    files = [
        _PF(_FI(path="src/index.ts", language="typescript", is_entry_point=True)),
        _PF(_FI(path="src/config.json", language="json")),
    ]
    pr = {"src/index.ts": 0.6, "src/config.json": 0.4}
    edges = [("src/index.ts", "src/config.json")]
    documented = {"src/index.ts", "src/config.json"}
    stops = build_tour(files, pr, edges, file_page_paths=documented)
    assert "src/config.json" in {s.target_path for s in stops}


# ---------------------------------------------------------------------------
# Wiring-stub entry co-anchor
# ---------------------------------------------------------------------------


def _stub_entry_repo():
    """An OTP-style library: the only entry is a supervision stub whose
    forward BFS reaches nothing, while a hub file's imports fan out wide."""
    paths = {
        "lib/app/application.ex": True,  # wiring stub — no outgoing imports
        "lib/encode.ex": False,
        "lib/codegen.ex": False,
        "lib/fragment.ex": False,
        "lib/decoder.ex": False,
        "lib/helpers.ex": False,
        "lib/sigil.ex": False,
        "lib/jason.ex": False,
    }
    files = [_PF(_FI(path=p, is_entry_point=e, language="elixir")) for p, e in paths.items()]
    edges = [
        ("lib/encode.ex", "lib/codegen.ex"),
        ("lib/encode.ex", "lib/fragment.ex"),
        ("lib/encode.ex", "lib/jason.ex"),
        ("lib/codegen.ex", "lib/decoder.ex"),
    ]
    pr = dict.fromkeys(paths, 0.1)
    return files, pr, edges, set(paths)


def test_wiring_stub_entry_gets_a_co_anchor():
    files, pr, edges, documented = _stub_entry_repo()
    stops = build_tour(files, pr, edges, file_page_paths=documented)
    by_path = {s.target_path: s for s in stops}
    # the entry keeps its step and wording
    assert "An entry point" in by_path["lib/app/application.ex"].reason
    # the widest-fanout file co-anchors at depth 0 with anchor wording
    anchor = by_path["lib/encode.ex"]
    assert anchor.depth == 0
    assert "entry point above only wires" in anchor.reason
    # the walk actually proceeds through the anchor's imports
    assert by_path["lib/codegen.ex"].depth == 1
    assert "anchor above" in by_path["lib/codegen.ex"].reason
    assert by_path["lib/decoder.ex"].depth == 2


def test_entry_with_real_reach_gets_no_co_anchor():
    paths = {
        "src/main.py": True,
        "src/a.py": False,
        "src/b.py": False,
        "src/c.py": False,
        "src/hub.py": False,
        "src/d.py": False,
        "src/e.py": False,
        "src/f.py": False,
    }
    files = _repo(paths)
    edges = [
        ("src/main.py", "src/a.py"),
        ("src/a.py", "src/b.py"),
        ("src/b.py", "src/c.py"),
        # a hub with wide fanout that must NOT displace the real entry walk
        ("src/hub.py", "src/d.py"),
        ("src/hub.py", "src/e.py"),
        ("src/hub.py", "src/f.py"),
    ]
    pr = dict.fromkeys(paths, 0.1)
    stops = build_tour(files, pr, edges, file_page_paths=set(paths))
    by_path = {s.target_path: s for s in stops}
    assert "An entry point" in by_path["src/main.py"].reason
    assert "only wires" not in " ".join(s.reason for s in stops)


# ---------------------------------------------------------------------------
# Manifests never park
# ---------------------------------------------------------------------------


def test_unreached_manifest_never_parks():
    paths = {
        "lib/core.ex": False,
        "lib/util.ex": False,
        "mix.exs": False,
        "project.clj": False,
    }
    files = [_PF(_FI(path=p, is_entry_point=False, language="elixir")) for p in paths]
    edges = [("lib/core.ex", "lib/util.ex")]
    pr = dict.fromkeys(paths, 0.1)
    stops = build_tour(files, pr, edges, file_page_paths=set(paths))
    visited = {s.target_path for s in stops}
    assert "mix.exs" not in visited
    assert "project.clj" not in visited
