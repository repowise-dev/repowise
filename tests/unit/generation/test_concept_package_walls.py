"""Package roots are walls in the concept tree.

A page that merged several small packages used to take one member's directory
as its target and its title, then describe every member as that one. Now no
group holds files of two packages, except a roll-up of whole thin sibling
packages, which is named for their shared parent plus a member list and lists
each package in a deterministic table.
"""

from __future__ import annotations

import networkx as nx

from repowise.core.generation.concept_tree.grouping import (
    GroupingParams,
    group_files,
    package_file_counts,
)
from repowise.core.generation.concept_tree.naming import (
    build_payload,
    decode_response,
    deterministic_title,
)
from repowise.core.generation.concept_tree.planner import PlannerInputs, plan_deterministic
from repowise.core.generation.context_assembler import ContextAssembler, FilePageContext
from repowise.core.generation.models import GenerationConfig
from repowise.core.generation.page_generator import PageGenerator
from repowise.core.generation.selection import SelectionInputs
from repowise.core.generation.selection.selector import _build_module_groups
from repowise.core.providers.llm.mock import MockProvider
from tests.unit.generation.test_selection_contract import (
    FakeFileInfo,
    FakeParsedFile,
    FakeSymbol,
)

PARAMS = GroupingParams(min_files=4, max_files=10)

THIN = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"]


def _monorepo() -> tuple[list[str], set[str]]:
    """Six thin packages and one fat one under ``packages/``, plus root files."""
    files = [f"packages/{name}/src/f{i}.ts" for name in THIN for i in range(2)]
    files += [
        f"packages/kernel/src/{sub}/f{i}.ts" for sub in ("io", "model", "run") for i in range(6)
    ]
    files += ["build.ts", "index.ts"]
    roots = {f"packages/{name}" for name in [*THIN, "kernel"]}
    return files, roots


def _package_of(path: str, roots: set[str]) -> str:
    hits = [r for r in roots if path.startswith(r + "/")]
    return max(hits, key=len) if hits else ""


def _groups():
    files, roots = _monorepo()
    return group_files(files, params=PARAMS, package_roots=roots), roots


def test_no_leaf_group_spans_two_packages():
    groups, roots = _groups()
    for g in groups:
        owners = {_package_of(m, roots) for m in g.members}
        if g.packages:
            continue
        assert len(owners) == 1, (g.target_path, owners)


def test_thin_siblings_roll_up_whole_under_their_parent():
    groups, _roots = _groups()
    rollups = [g for g in groups if g.packages]
    assert len(rollups) == 1
    rollup = rollups[0]
    assert rollup.packages == tuple(f"packages/{n}" for n in THIN)
    # Whole packages only: every file of each member is on this page.
    for pkg in rollup.packages:
        assert {m for m in _monorepo()[0] if m.startswith(pkg + "/")} <= set(rollup.members)
    assert rollup.target_path == "packages"
    assert package_file_counts(rollup.members, rollup.packages) == [
        (f"packages/{n}", 2) for n in THIN
    ]


def test_no_title_or_target_names_a_single_member():
    groups, _roots = _groups()
    for g in groups:
        if not g.packages:
            continue
        assert not any(g.target_path == p or g.target_path.startswith(p + "/") for p in g.packages)
        title = deterministic_title(g)
        assert title.startswith("Packages:"), title
        assert "Alpha" in title and "Bravo" in title and "More" in title


def test_a_subtree_that_fits_still_splits_at_a_package_root():
    """Two packages small enough to share one subtree group stay two groups."""
    files = [f"libs/one/f{i}.py" for i in range(4)] + [f"libs/two/f{i}.py" for i in range(4)]
    groups = group_files(files, params=PARAMS, package_roots={"libs/one", "libs/two"})
    assert sorted(g.target_path for g in groups) == ["libs/one", "libs/two"]
    assert not any(g.packages for g in groups)


def test_a_lone_thin_package_does_not_absorb_its_neighbour():
    files = [f"src/app/f{i}.py" for i in range(8)] + ["src/plugin/a.py", "src/plugin/b.py"]
    groups = group_files(files, params=PARAMS, package_roots={"src/plugin"})
    by_target = {g.target_path: sorted(g.members) for g in groups}
    assert by_target["src/plugin"] == ["src/plugin/a.py", "src/plugin/b.py"]


def test_without_package_roots_the_partition_is_unchanged():
    files, _roots = _monorepo()
    plain = group_files(files, params=PARAMS)
    walled_nothing = group_files(files, params=PARAMS, package_roots=())
    assert [g.members for g in plain] == [g.members for g in walled_nothing]


def test_the_model_cannot_rename_a_rollup_after_one_member():
    groups, _roots = _groups()
    payload, index = build_payload(groups)
    gid = next(gid for gid, g in index.items() if g.packages)
    assert next(e for e in payload["groups"] if e["id"] == gid)["packages"] == THIN
    named, _ = decode_response(
        {"names": {gid: {"title": "Alpha Provider Package", "scope": "The alpha provider."}}},
        index,
    )
    entry = next(n for n in named if n.group.packages)
    assert entry.title == deterministic_title(entry.group)
    assert "6 small sibling packages" in entry.scope


def _selection_inputs() -> SelectionInputs:
    files, roots = _monorepo()
    parsed = [
        FakeParsedFile(
            file_info=FakeFileInfo(path=p, language="typescript"), symbols=[FakeSymbol(name="fn")]
        )
        for p in files
    ]
    parsed += [
        FakeParsedFile(
            file_info=FakeFileInfo(path=f"{r}/package.json", language="json"), symbols=[]
        )
        for r in sorted(roots)
    ]
    paths = [p.file_info.path for p in parsed]
    return SelectionInputs(
        parsed_files=parsed,
        pagerank={p: 0.1 for p in paths},
        betweenness={p: 0.0 for p in paths},
        community={p: 0 for p in paths},
        community_info=None,
        sccs=[],
        git_meta_map=None,
        config=GenerationConfig(coverage_pct=0.20),
    )


def test_selection_reads_the_walls_off_the_parsed_manifests():
    groups = [mg for _, mg in _build_module_groups(_selection_inputs()).scored]
    rollups = [mg for mg in groups if mg.packages]
    assert len(rollups) == 1
    assert rollups[0].key == "packages"
    assert rollups[0].display.startswith("Packages:")
    kernel = [mg for mg in groups if any(f.startswith("packages/kernel/") for f in mg.file_paths)]
    assert all(all(f.startswith("packages/kernel/") for f in mg.file_paths) for mg in kernel)


class _RecordingProvider(MockProvider):
    async def generate(self, *args, **kwargs):
        response = await super().generate(*args, **kwargs)
        response.content = "# Small packages\n\nThese packages each wrap one provider.\n"
        return response


def _context(path: str) -> FilePageContext:
    return FilePageContext(
        file_path=path,
        language="typescript",
        docstring=None,
        symbols=[],
        imports=[],
        exports=[],
        pagerank_score=0.1,
        betweenness_score=0.0,
        community_id=0,
        dependents=[],
        dependencies=[],
        is_api_contract=False,
        is_entry_point=False,
        is_test=False,
        parse_errors=[],
        estimated_tokens=10,
    )


async def test_rollup_prompt_names_every_package_and_page_lists_them(sample_config):
    provider = _RecordingProvider()
    gen = PageGenerator(provider, ContextAssembler(sample_config), sample_config)
    members = [f"packages/{n}/src/f0.ts" for n in ("alpha", "bravo", "charlie")]
    page = await gen.generate_module_page(
        "Packages: Alpha, Bravo and 1 More",
        "typescript",
        [_context(m) for m in members],
        nx.DiGraph(),
        target_path="packages",
        structural_key="k",
        members=members,
        packages=[{"path": f"packages/{n}", "files": 1} for n in ("alpha", "bravo", "charlie")],
    )
    prompt = provider._calls[-1]["user_prompt"]
    assert (
        "covers 3 sibling packages: `packages/alpha`, `packages/bravo`, `packages/charlie`"
        in prompt
    )
    assert "## Packages" in page.content
    for n in ("alpha", "bravo", "charlie"):
        assert f"| `packages/{n}` | 1 |" in page.content


def test_a_rollup_at_a_chapter_directory_keeps_its_name_and_material():
    """Two fat siblings make ``packages`` chapter-eligible; the roll-up stays a roll-up."""
    files, roots = _monorepo()
    files += [f"packages/{n}/f{i}.ts" for n in ("golf", "hotel") for i in range(7)]
    files += [f"apps/web/{d}/f{i}.ts" for d in ("a", "b", "c", "d", "e", "f") for i in range(8)]
    roots |= {"packages/golf", "packages/hotel"}
    parsed = [FakeParsedFile(file_info=FakeFileInfo(path=p, language="typescript")) for p in files]
    parsed += [
        FakeParsedFile(file_info=FakeFileInfo(path=f"{r}/package.json", language="json"))
        for r in sorted(roots)
    ]
    inputs = _selection_inputs()
    inputs.parsed_files = parsed
    rollup = next(mg for _, mg in _build_module_groups(inputs).scored if mg.packages)
    assert not rollup.is_rollup
    assert rollup.display.startswith("Packages:")
    assert rollup.context_paths == ()


def test_the_planner_walls_packages_the_way_selection_does():
    files, roots = _monorepo()
    walled = group_files(files, params=PARAMS, package_roots=roots)
    _outline, groups = plan_deterministic(
        PlannerInputs(repo_name="r", production_files=files, package_roots=roots), params=PARAMS
    )
    assert [g.members for g in groups] == [g.members for g in walled]
    assert [g.packages for g in groups if g.packages] == [tuple(f"packages/{n}" for n in THIN)]


def test_top_level_thin_packages_roll_up_at_the_root():
    files = [f"{n}/f{i}.py" for n in ("alpha", "bravo") for i in range(2)]
    files += [f"core/{d}/f{i}.py" for d in ("x", "y") for i in range(6)]
    groups = group_files(files, params=PARAMS, package_roots={"alpha", "bravo"})
    rollup = next(g for g in groups if g.packages)
    assert rollup.target_path == "root"
    assert deterministic_title(rollup) == "Root Packages: Alpha, Bravo"
