"""Concept grouping in the selection layer.

Replaces the curated/community/top-dir grouping tests. That grouping is gone,
not deprecated: ``module_page`` is now produced from the concept partition
(``concept_tree.grouping.group_files``), so the properties worth asserting
changed from "which of three sources won" to the two the partition
guarantees — every production file is covered exactly once, and the same
input always produces the same page identities.

Fixtures deliberately disagree with the alphabet where ordering matters, and
every path fixture includes a root-level file, because the repository root's
directory path is the empty string and a falsy check cannot tell it from "no
directory found". That defect shipped once.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

from repowise.core.generation.concept_tree.grouping import ConceptGroup
from repowise.core.generation.models import GenerationConfig
from repowise.core.generation.selection import SelectionInputs, select_pages
from repowise.core.generation.selection.selector import (
    ModuleGroup,
    _build_module_groups,
    _build_rollup_groups,
    _chapter_members,
    retitle_chapters,
)
from tests.unit.generation.test_selection_contract import (
    FakeFileInfo,
    FakeParsedFile,
    FakeSymbol,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _paths() -> list[str]:
    """Production paths spanning several subtrees, plus root-level files.

    PageRank below is assigned so the highest-scoring group is NOT the one
    that sorts first alphabetically, so an ordering assertion cannot pass by
    accident on a sorted-by-path implementation.
    """
    return (
        [f"packages/app/src/core/ingestion/f{i}.py" for i in range(8)]
        + [f"packages/app/src/core/analysis/f{i}.py" for i in range(6)]
        + [f"packages/app/src/ui/c4/f{i}.py" for i in range(5)]
        + ["packages/app/src/ui/tiny/f0.py", "packages/app/src/ui/tiny/f1.py"]
        # Root-level files: the empty-string directory is real.
        + ["setup.py", "main.py"]
    )


def _test_paths() -> list[str]:
    return [f"tests/unit/test_f{i}.py" for i in range(6)]


def _inputs(
    *,
    with_tests: bool = True,
    kg_modules: list[dict] | None = None,
    paths: list[str] | None = None,
):
    prod = paths if paths is not None else _paths()
    paths = prod + (_test_paths() if with_tests else [])
    parsed = [
        FakeParsedFile(
            file_info=FakeFileInfo(path=p, is_test=p.startswith("tests/")),
            symbols=[FakeSymbol(name="fn")],
        )
        for p in paths
    ]
    # ui/c4 carries the highest mass, and "packages/app/src/ui/c4" sorts after
    # "packages/app/src/core/...", so score-descending order must not equal
    # path order.
    pagerank = {p: (1.0 if "/ui/c4/" in p else 0.1) for p in paths}
    return SelectionInputs(
        parsed_files=parsed,
        pagerank=pagerank,
        betweenness={p: 0.0 for p in paths},
        community={p: 0 for p in paths},
        community_info=None,
        sccs=[],
        git_meta_map=None,
        config=GenerationConfig(coverage_pct=0.20),
        kg_modules=kg_modules,
    )


# ---------------------------------------------------------------------------
# Coverage and totality
# ---------------------------------------------------------------------------


def test_every_production_file_is_covered_exactly_once():
    groups = [g for _, g in _build_module_groups(_inputs()).scored]
    claimed = [p for g in groups for p in g.file_paths]

    assert sorted(claimed) == sorted(_paths()), "the partition is not total"
    assert len(claimed) == len(set(claimed)), "a file was claimed by two pages"


def test_root_level_files_get_a_usable_target_path():
    """The repository root is a real directory whose path is ``""``.

    A group anchored there must not persist an empty ``target_path``: the page
    id is ``"{type}:{target_path}"``, so an empty target mints ``module_page:``
    and leaves the page with nothing for the tree, the breadcrumbs or the
    bench gold set to match on.
    """
    groups = [g for _, g in _build_module_groups(_inputs()).scored]
    owning = [g for g in groups if "setup.py" in g.file_paths]

    assert owning, "root-level files were dropped from the partition"
    assert any("main.py" in g.file_paths for g in groups)
    for g in groups:
        assert g.key, "a group persisted an empty target_path"
    assert any(
        g.key == "root" for g in owning
    ), f"root-anchored group did not get the root target: {[g.key for g in owning]}"


def test_test_files_never_enter_the_concept_tree():
    """D8: tests keep file pages, they do not get concept pages."""
    groups = [g for _, g in _build_module_groups(_inputs()).scored]
    claimed = {p for g in groups for p in g.file_paths}

    assert not any(p.startswith("tests/") for p in claimed)
    # And their absence is not an artifact of them being absent from input.
    assert any(p.file_info.path.startswith("tests/") for p in _inputs().parsed_files)


def test_dropping_the_test_files_does_not_change_the_partition():
    """Excluding tests must be a filter, not a force that reshapes groups."""
    with_tests = [g.structural_key for _, g in _build_module_groups(_inputs()).scored]
    without = [g.structural_key for _, g in _build_module_groups(_inputs(with_tests=False)).scored]

    assert with_tests == without


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def test_groups_carry_a_concept_prefixed_structural_key():
    """The prefix is what lets a wiki tell a concept page from an old one."""
    groups = [g for _, g in _build_module_groups(_inputs()).scored]

    assert groups
    assert all(g.structural_key.startswith("concept-") for g in groups)
    keys = [g.structural_key for g in groups]
    assert len(keys) == len(set(keys)), "two groups share an identity"


def test_target_paths_are_unique():
    """The page id is derived from the target path alone.

    Two groups resolving to one target is a row collision: the second page
    silently overwrites the first on persist.
    """
    groups = [g for _, g in _build_module_groups(_inputs()).scored]
    keys = [g.key for g in groups]

    assert len(keys) == len(set(keys)), f"duplicate target paths: {keys}"


def test_identity_is_stable_across_processes():
    """Hash randomisation must not move a page's identity.

    Run in two subprocesses under different ``PYTHONHASHSEED`` values. Calling
    the function twice in one process cannot fail this way and therefore
    cannot test it.
    """
    script = textwrap.dedent(
        """
        from tests.unit.generation.test_selection_concept_groups import (
            _build_module_groups, _inputs,
        )
        groups = [g for _, g in _build_module_groups(_inputs()).scored]
        print("|".join(f"{g.key}={g.structural_key}" for g in groups))
        """
    )
    outs = []
    for seed in ("0", "12345"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(_REPO_ROOT),
            timeout=120,
        )
        assert proc.returncode == 0, proc.stderr
        outs.append(proc.stdout.strip())

    assert outs[0] == outs[1], "page identity moved with the hash seed"
    assert outs[0], "the subprocess produced no groups, so this asserted nothing"


# ---------------------------------------------------------------------------
# The importance floor covers documentation and example source too
# ---------------------------------------------------------------------------


def _support_paths() -> list[str]:
    """Documentation and example source at the repository root."""
    return (
        [f"docs/conf{i}.py" for i in range(4)]
        + [f"docs_src/tutorial/app{i}.py" for i in range(8)]
        + [f"examples/basic/demo{i}.py" for i in range(5)]
        + ["samples/quickstart.py"]
    )


def test_root_documentation_and_examples_get_no_concept_page():
    """Measured, not assumed: on one framework these outnumbered the library."""
    groups = [g for _, g in _build_module_groups(_inputs(paths=_paths() + _support_paths())).scored]
    claimed = [p for g in groups for p in g.file_paths]

    assert claimed, "fixture produced no groups"
    for prefix in ("docs/", "docs_src/", "examples/", "samples/"):
        assert not any(
            p.startswith(prefix) for p in claimed
        ), f"{prefix} reached the concept tree: {[p for p in claimed if p.startswith(prefix)]}"
    # The fixture is only meaningful if those files were in the input.
    assert any(p.startswith("docs_src/") for p in _support_paths())


def test_a_docs_directory_inside_a_package_keeps_its_pages():
    """The rule is anchored at the repository root, so a docs *feature* stays.

    This is the case the amendment names: a source directory that happens to
    be called ``docs`` deep inside a package is code, and excluding it would
    be the substring bug the test rule originally shipped with, wearing a
    different hat.
    """
    feature = [f"packages/app/src/core/docs/render{i}.py" for i in range(7)]
    groups = [g for _, g in _build_module_groups(_inputs(paths=_paths() + feature)).scored]
    claimed = {p for g in groups for p in g.file_paths}

    assert set(feature) <= claimed, f"a docs feature was excluded: {set(feature) - claimed}"


def test_production_paths_that_merely_begin_with_the_word_are_kept():
    """Segment match, not prefix-of-string match."""
    lookalikes = [
        "documentation_builder/render.py",
        "docsearch/index.py",
        "examples_runner/main.py",
        "sampler/draw.py",
    ]
    groups = [g for _, g in _build_module_groups(_inputs(paths=_paths() + lookalikes)).scored]
    claimed = {p for g in groups for p in g.file_paths}

    assert set(lookalikes) <= claimed, f"excluded by a prefix match: {set(lookalikes) - claimed}"


def test_dropping_support_files_does_not_change_the_partition():
    """The floor is a pure filter: it must not reshape the remaining groups."""
    with_support = [
        g.structural_key
        for _, g in _build_module_groups(_inputs(paths=_paths() + _support_paths())).scored
    ]
    without = [g.structural_key for _, g in _build_module_groups(_inputs(paths=_paths())).scored]

    assert with_support == without


def test_a_repository_that_is_only_documentation_still_gets_a_tree():
    """Documenting the docs is a bad wiki. Having none at all is a worse one."""
    groups = [g for _, g in _build_module_groups(_inputs(paths=_support_paths())).scored]
    claimed = [p for g in groups for p in g.file_paths]

    assert groups, "the floor emptied the tree instead of yielding"
    assert sorted(claimed) == sorted(_support_paths())


# ---------------------------------------------------------------------------
# Ordering and naming
# ---------------------------------------------------------------------------


def test_ranked_by_summed_pagerank_not_by_path():
    """The heaviest subsystem sorts first even though its path sorts last."""
    scored = _build_module_groups(_inputs()).scored
    ordered_keys = [g.key for _, g in scored]

    assert len(scored) > 1, "a one-group fixture cannot test ordering"
    # The fixture is only meaningful if path order disagrees with score order,
    # or the assertion below would pass on a sorted-by-path implementation.
    assert ordered_keys != sorted(
        ordered_keys
    ), f"fixture is degenerate: score order equals path order ({ordered_keys})"
    # ui/c4 carries 1.0 per file against 0.1 elsewhere, so whichever group
    # holds it must come first.
    top_group = scored[0][1]
    assert any(
        "/ui/c4/" in p for p in top_group.file_paths
    ), f"expected the ui/c4 mass to rank first, got {top_group.key}"


def test_display_is_a_name_not_a_bare_path():
    groups = [g for _, g in _build_module_groups(_inputs()).scored]

    for g in groups:
        assert g.display, f"{g.key} has no title"
        assert "/" not in g.display, f"{g.display} is a path, not a name"


def test_layer_labels_from_the_kg_reach_the_title():
    """The KG layer map is read when present and absent-safe when not.

    The rescue only fires on a name too short to say anything, so the fixture is
    a single directory whose path yields one word (``gateway`` -> "Gateway").
    A group that already yields two words needs no prefix and would show the
    feature nothing either way — the earlier fixture only exercised this because
    a grouping bug had swept its files into a one-word "root" page.
    """
    gateway = [f"packages/app/src/gateway/f{i}.py" for i in range(8)]
    paths = gateway + [f"packages/app/src/core/analysis/f{i}.py" for i in range(8)] + ["setup.py"]
    modules = [
        {
            "id": "module:gateway",
            "path": "packages/app/src/gateway",
            "layerId": "layer:presentation",
            "nodeIds": [f"file:{p}" for p in gateway],
        }
    ]
    with_kg = [g for _, g in _build_module_groups(_inputs(paths=paths, kg_modules=modules)).scored]
    without_kg = [g for _, g in _build_module_groups(_inputs(paths=paths)).scored]

    # Membership is unchanged by the layer signal: it steers merging of
    # adjacent runs, it never forces a split.
    assert [g.structural_key for g in with_kg] == [g.structural_key for g in without_kg]
    assert all(g.display for g in with_kg)

    # The label itself has to reach a title, or the map is being read and
    # thrown away. Titles differ only where the layer applies: the prefix
    # exists to rescue a name too short to say anything, so groups whose
    # paths already yield two words are identical either way.
    titled = [g.display for g in with_kg]
    untitled = [g.display for g in without_kg]
    assert titled != untitled, titled
    assert any("Presentation" in t for t in titled), titled


# ---------------------------------------------------------------------------
# The bucket is not rationed
# ---------------------------------------------------------------------------


def test_select_pages_emits_every_group():
    inputs = _inputs()
    groups = _build_module_groups(inputs).scored
    selection = select_pages(inputs)

    assert len(selection.module_groups) == len(groups)
    assert len(selection.module_groups) == len(groups)


def test_structural_key_survives_selection():
    """The key the grouper minted has to reach the page, not be recomputed."""
    selection = select_pages(_inputs())

    assert selection.module_groups
    assert all(g.structural_key.startswith("concept-") for g in selection.module_groups)


# ---------------------------------------------------------------------------
# Titles a reader can tell apart
# ---------------------------------------------------------------------------


def test_two_groups_never_share_a_title():
    """Same directory name in two packages must not produce two identical rows.

    Page identity is structural, so the pages stay distinct — but the tree
    shows the title, and two rows reading "Ui Components" are the same row as
    far as a reader is concerned.
    """
    paths = (
        [f"packages/alpha/src/ui/f{i}.py" for i in range(8)]
        + [f"packages/beta/src/ui/f{i}.py" for i in range(8)]
        + ["setup.py"]
    )
    groups = [g for _, g in _build_module_groups(_inputs(paths=paths)).scored]

    titles = [g.display for g in groups]
    assert len(titles) == len(set(titles)), titles
    assert len({g.key for g in groups}) == len(groups)


# ---------------------------------------------------------------------------
# Parent-directory rollup overview pages
# ---------------------------------------------------------------------------


def _leaf(members: list[str]) -> ConceptGroup:
    """A concept group whose target_path is its members' shared directory."""
    tp = members[0].rsplit("/", 1)[0]
    return ConceptGroup(members=sorted(members), dirs=[tp], target_path=tp)


def _synthesised(leaves: list[ConceptGroup], files: list[str]):
    """The chapters that need a page of their own, as the selector builds them."""
    chapters = _chapter_members(leaves, files)
    return chapters, _build_rollup_groups(
        chapters, leaves, files, {f: "python" for f in files}, {}
    )


def test_chapter_emitted_for_parent_of_two_leaf_pages():
    """A parent that heads >=2 leaf pages and is itself no leaf gets a chapter.

    Its target_path is exactly that directory, because directory-level retrieval
    matches a page to a directory by exact target_path equality.
    """
    leaves = [
        _leaf(["p/ingestion/languages/a.py", "p/ingestion/languages/b.py"]),
        _leaf(["p/ingestion/graph/c.py", "p/ingestion/graph/d.py"]),
        # Two unrelated subsystems so ingestion stays a minority of the repo and
        # the near-repo-wide guard does not fire; each owns a single leaf, so
        # neither earns a chapter of its own.
        _leaf([f"p/other/e{i}.py" for i in range(6)]),
        _leaf([f"p/extra/g{i}.py" for i in range(6)]),
    ]
    files = [m for g in leaves for m in g.members] + ["p/ingestion/loose.py"]

    chapters, rollups = _synthesised(leaves, files)

    # ingestion heads two leaf children; the others head one each, so no chapter.
    assert set(chapters) == {"p/ingestion"}
    assert {m.key for _, m in rollups} == {"p/ingestion"}
    (_, roll) = rollups[0]
    assert roll.is_rollup is True
    assert roll.structural_key.startswith("concept-rollup")
    # No group of its own, so it owns nothing and every file stays with a leaf.
    assert roll.file_paths == ()
    # It still reads the subsystem's files for context, including loose ones.
    assert "p/ingestion/loose.py" in roll.context_paths


def test_a_directory_that_is_both_chapter_and_leaf_yields_one_page():
    """The parent keeps its own group and heads its children from it.

    Both pages would be ``module_page:p/svc`` and only one page can have an id,
    so this is the case that used to be dropped. Dropping it removed the
    chapter a reader most wants: the directory holding both the subsystem's
    entry points and its parts.
    """
    parent = _leaf(["p/svc/a.py", "p/svc/b.py"])  # target p/svc — the parent itself
    leaves = [
        parent,
        _leaf(["p/svc/api/c.py", "p/svc/api/d.py"]),
        _leaf(["p/svc/db/e.py", "p/svc/db/f.py"]),
        _leaf([f"p/other/e{i}.py" for i in range(8)]),
    ]
    files = [m for g in leaves for m in g.members]

    chapters, rollups = _synthesised(leaves, files)

    # p/svc heads a subsystem...
    assert "p/svc" in chapters
    # ...but no second page is synthesised for it, because its own group is the
    # page. Exactly one thing may claim ``module_page:p/svc``.
    assert "p/svc" not in {m.key for _, m in rollups}


def _module_group(key: str, *, files: tuple[str, ...], chapter: bool, display: str):
    return ModuleGroup(
        key=key, display=display, language="python", file_paths=files, is_rollup=chapter
    )


def test_chapter_titles_are_disambiguated_against_the_leaves():
    """A chapter title never duplicates a leaf's, or another chapter's.

    Two identical rows in the tree are indistinguishable to a reader, and
    anything keying on a title has to guess between them. The leaves do not
    move: one of them may carry a model-written name, and a chapter yielding to
    it is the cheaper of the two collisions to resolve.
    """
    groups = [
        # Two chapters whose directories share a last segment, plus a leaf
        # already holding the name they would both take.
        _module_group("a/web/components", files=(), chapter=True, display=""),
        _module_group("a/ext/components", files=(), chapter=True, display=""),
        _module_group(
            "a/web/components/x", files=("a/web/components/x/1.py",),
            chapter=False, display="Components Overview",
        ),
        _module_group(
            "a/ext/components/y", files=("a/ext/components/y/5.py",),
            chapter=False, display="Entity Cards",
        ),
    ]

    out = retitle_chapters(groups)
    titles = [g.display for g in out]

    assert len(titles) == len(set(titles)), titles
    # The leaf keeps the name it was given; the chapters take qualified ones.
    by_key = dict(zip([g.key for g in out], titles, strict=True))
    assert by_key["a/web/components/x"] == "Components Overview"


def test_a_chapter_is_named_for_the_directory_it_heads():
    """Not for its own files, and not by the model.

    A namer shown a chapter's directory names the loose files at that
    directory's root, and that name then stands over everything below it. The
    name a reader can act on is the place.
    """
    groups = [
        _module_group(
            "p/ingestion", files=("p/ingestion/loose.py",),
            chapter=True, display="Pipeline Bootstrap Helpers",
        ),
        _module_group(
            "p/ingestion/languages", files=("p/ingestion/languages/a.py",),
            chapter=False, display="Language Catalog",
        ),
    ]

    out = {g.key: g.display for g in retitle_chapters(groups)}

    assert out["p/ingestion"] == "Ingestion Overview"
    # A leaf is never touched: the model names those well.
    assert out["p/ingestion/languages"] == "Language Catalog"


def test_a_chapter_skips_container_and_route_segments():
    """``src`` and ``[id]`` name no subject, so the chapter climbs past them."""
    groups = [
        _module_group("packages/ui/src", files=(), chapter=True, display=""),
        _module_group(
            "packages/ui/src/zoom", files=("packages/ui/src/zoom/a.ts",),
            chapter=False, display="Zoom Canvas",
        ),
        _module_group("app/repos/[id]", files=(), chapter=True, display=""),
        _module_group(
            "app/repos/[id]/docs", files=("app/repos/[id]/docs/p.tsx",),
            chapter=False, display="Docs Routes",
        ),
    ]

    out = {g.key: g.display for g in retitle_chapters(groups)}

    assert out["packages/ui/src"] == "Ui Overview"
    assert out["app/repos/[id]"] == "Repos Overview"


def test_a_chapter_spells_its_subject_the_way_the_repository_does():
    """The children are the only place that says whether ``ui`` is "UI" or "Ui".

    Taken from them rather than from an acronym list, which would be a rule
    tuned to the repositories whose acronyms happened to be on it.
    """
    groups = [
        _module_group("packages/ui/src", files=(), chapter=True, display=""),
        _module_group(
            "packages/ui/src/zoom", files=("packages/ui/src/zoom/a.ts",),
            chapter=False, display="Zoom UI Canvas",
        ),
        _module_group(
            "packages/ui/src/wiki", files=("packages/ui/src/wiki/b.ts",),
            chapter=False, display="Wiki UI Panels",
        ),
    ]

    assert {g.key: g.display for g in retitle_chapters(groups)}["packages/ui/src"] == "UI Overview"


def test_a_chapter_takes_its_casing_only_from_a_deliberate_spelling():
    """A lowercase occurrence in a child's title is not a spelling of the word.

    It is the word used mid-sentence, or a quoted path. Taking it would open a
    page title in lower case.
    """
    groups = [
        _module_group("p/core", files=(), chapter=True, display=""),
        _module_group(
            "p/core/a", files=("p/core/a/x.py",),
            chapter=False, display="Helpers for core and friends",
        ),
    ]

    assert {g.key: g.display for g in retitle_chapters(groups)}["p/core"] == "Core Overview"


def test_a_chapter_does_not_become_a_case_variant_of_a_leaf():
    """"UI Overview" over "Ui Overview" is one thing written twice.

    The chapter borrows its casing from its children and the leaf derives its
    own from a path, so the two arrive spelled differently and an exact-match
    uniqueness check lets both through.
    """
    groups = [
        _module_group("packages/ui/src", files=(), chapter=True, display=""),
        _module_group(
            "packages/ui/src/overview", files=("packages/ui/src/overview/a.ts",),
            chapter=False, display="Ui Overview",
        ),
        _module_group(
            "packages/ui/src/zoom", files=("packages/ui/src/zoom/b.ts",),
            chapter=False, display="Zoom UI Canvas",
        ),
    ]

    titles = [g.display for g in retitle_chapters(groups)]

    assert len({t.lower() for t in titles}) == len(titles), titles


def test_a_chapter_is_named_from_the_children_the_tree_puts_under_it():
    """Nearest chapter, not nearest directory — chapters nest.

    A title drawn from a different set of children than the tree shows beneath
    it is a title about the wrong pages.
    """
    groups = [
        _module_group("p/svc", files=(), chapter=True, display=""),
        _module_group("p/svc/api", files=(), chapter=True, display=""),
        # Two levels down, so its nearest chapter is api and not svc.
        _module_group(
            "p/svc/api/v2", files=("p/svc/api/v2/a.py",), chapter=False, display="V2 API Routes"
        ),
        _module_group(
            "p/svc/db", files=("p/svc/db/b.py",), chapter=False, display="Storage Layer"
        ),
    ]

    out = {g.key: g.display for g in retitle_chapters(groups)}

    assert out["p/svc"] == "Svc Overview"
    # "API" comes from the grandchild's title, which is what the tree nests here.
    assert out["p/svc/api"] == "API Overview"


def test_chapter_skips_near_repo_wide_parent():
    """A parent covering most of the repo is the repo overview, not a chapter."""
    leaves = [
        _leaf(["mono/a/x.py", "mono/a/y.py"]),
        _leaf(["mono/b/z.py", "mono/b/w.py"]),
    ]
    files = [m for g in leaves for m in g.members]
    chapters, rollups = _synthesised(leaves, files)
    assert "mono" not in chapters
    assert "mono" not in {m.key for _, m in rollups}


def test_chapters_never_double_claim_a_file():
    """Ownership stays a partition once chapters own files.

    The tree's ``module_of_file``, the incremental cascade's ``module_page_of``
    and ``related_pages``' sibling map all read ``file_paths`` as an exclusive
    claim and resolve a conflict by silently picking one. A chapter that
    out-claimed its own leaves would reparent their files onto itself.
    """
    leaves = [
        _leaf(["p/svc/a.py", "p/svc/b.py"]),
        _leaf(["p/svc/api/c.py", "p/svc/api/d.py"]),
        _leaf(["p/svc/db/e.py", "p/svc/db/f.py"]),
        _leaf(["p/ingestion/lang/g.py", "p/ingestion/lang/h.py"]),
        _leaf(["p/ingestion/graph/i.py", "p/ingestion/graph/j.py"]),
        _leaf([f"p/other/k{i}.py" for i in range(8)]),
    ]
    files = [m for g in leaves for m in g.members]
    _chapters, rollups = _synthesised(leaves, files)

    claims: list[str] = [m for g in leaves for m in g.members]
    claims += [p for _, mg in rollups for p in mg.file_paths]

    assert sorted(claims) == sorted(files), "every file is claimed exactly once"
    assert len(claims) == len(set(claims))
