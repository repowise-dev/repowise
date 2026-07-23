"""The deterministic partition: totality, stability, and bounded page size.

Grouping is the half of the outline planner that must not vary, because page
identity hashes the member list. A grouping that reshuffled between two runs of
the same commit would remint every page id, strand the vectors behind them, and
leave the old rows as duplicates. So the properties asserted here are
correctness properties, not tidiness ones.

Every fixture that depends on ordering is built so the structure disagrees with
the alphabet. A fixture whose spine and alphabet agree cannot tell a test that
reads structure from one that sorts.
"""

from __future__ import annotations

import os

from repowise.core.generation.concept_tree.grouping import (
    GroupingParams,
    group_files,
    params_for,
)

# Two sibling subsystems plus a deep one. Named so alphabetical order
# (alpha, beta, zulu) is NOT the order the tree nests them in.
TINY = GroupingParams(min_files=3, max_files=6)


def _repo(spec: dict[str, int]) -> list[str]:
    """Build a file list: ``{directory: count}``. ``""`` is the repo root."""
    out: list[str] = []
    for directory, count in spec.items():
        prefix = f"{directory}/" if directory else ""
        out.extend(f"{prefix}f{i:02d}.py" for i in range(count))
    return out


SPEC = {
    # Root-level files are the common case, not an edge case: setup.py,
    # manage.py, index.js, vite.config.ts all live here. A fixture without them
    # cannot see that the root's directory path is the empty string, which is
    # a real directory and was once mistaken for a missing one.
    "": 3,
    "src/zulu": 5,
    "src/alpha": 5,
    "src/beta/deep": 5,
    "src/beta/other": 4,
}


class TestTotality:
    def test_every_file_lands_in_exactly_one_group(self):
        files = _repo(SPEC)
        groups = group_files(files, params=TINY)
        claimed = [m for g in groups for m in g.members]
        assert sorted(claimed) == sorted(files)
        assert len(claimed) == len(set(claimed))

    def test_no_group_is_empty(self):
        """An empty group is a page about nothing, and a hard validator failure."""
        groups = group_files(_repo(SPEC), params=TINY)
        assert groups
        assert all(g.members for g in groups)

    def test_empty_input_is_harmless(self):
        assert group_files([]) == []

    def test_a_repo_smaller_than_the_ceiling_is_one_group(self):
        groups = group_files(_repo({"src": 4}), params=TINY)
        assert len(groups) == 1
        assert groups[0].file_count == 4


class TestStability:
    def test_two_processes_produce_identical_structural_keys(self):
        """Determinism that survives a fresh interpreter, not just a fresh call.

        Calling a pure function twice in one process cannot detect the failure
        that actually matters here. Python randomises string hashing per
        process, so a grouping that leaked set or dict iteration order would
        agree with itself all day and disagree between two indexing runs —
        reminting every page id. These two subprocesses are given different
        hash seeds on purpose.
        """
        import json
        import subprocess
        import sys
        import textwrap

        script = textwrap.dedent(
            """
            import json
            from repowise.core.generation.concept_tree.grouping import (
                GroupingParams, group_files,
            )
            spec = json.loads(input())
            files = []
            for d, n in spec.items():
                p = f"{d}/" if d else ""
                files.extend(f"{p}f{i:02d}.py" for i in range(n))
            groups = group_files(files, params=GroupingParams(3, 6))
            print(json.dumps([[g.target_path, g.structural_key] for g in groups]))
            """
        )

        def run(seed: str) -> list[list[str]]:
            out = subprocess.run(
                [sys.executable, "-c", script],
                input=json.dumps(SPEC),
                capture_output=True,
                text=True,
                env={**os.environ, "PYTHONHASHSEED": seed},
                check=True,
            )
            return json.loads(out.stdout.strip().splitlines()[-1])

        assert run("0") == run("12345")

    def test_input_order_does_not_change_the_grouping(self):
        files = _repo(SPEC)
        forward = [g.structural_key for g in group_files(files, params=TINY)]
        reverse = [g.structural_key for g in group_files(list(reversed(files)), params=TINY)]
        assert forward == reverse

    def test_one_added_file_does_not_reshuffle_the_tree(self):
        """The property the whole design exists for.

        A commit that adds one file must change the identity of the page that
        gained it and of nothing else. If a global parameter were derived from
        the file count, every key would move on every commit.
        """
        files = _repo(SPEC)
        before = {g.structural_key for g in group_files(files, params=TINY)}
        after = {
            g.structural_key for g in group_files([*files, "src/alpha/new_thing.py"], params=TINY)
        }
        assert len(before - after) == 1, "more than one group changed identity"
        assert len(after - before) == 1

    def test_the_size_ladder_is_flat_across_a_whole_band(self):
        """No parameter may be computed from the file count.

        A ceiling of ``total / target`` would move on almost every commit, and
        because the ceiling decides the partition, every page in the wiki
        would be reminted. Asserting two adjacent counts is not enough to catch
        that — integer division agrees with itself most of the time — so this
        asserts the whole band is flat.
        """
        band = [params_for(n) for n in range(1250, 2350, 7)]
        assert len(set(band)) == 1, "the ceiling varies with the file count"

    def test_the_ladder_does_step_between_bands(self):
        """The flatness above must not be flatness everywhere."""
        assert params_for(500) != params_for(5000)

    def test_an_unrelated_group_keeps_its_identity_when_another_changes(self):
        files = _repo(SPEC)
        base = {g.target_path: g.structural_key for g in group_files(files, params=TINY)}
        after = {
            g.target_path: g.structural_key
            for g in group_files([*files, "src/alpha/new_thing.py"], params=TINY)
        }
        untouched = [t for t in base if "alpha" not in t and t in after]
        assert untouched, "fixture proves nothing if no group is untouched"
        for target in untouched:
            assert base[target] == after[target]


class TestIdentity:
    def test_target_paths_are_unique(self):
        """Two groups sharing a target path would share a page id."""
        groups = group_files(_repo(SPEC), params=TINY)
        targets = [g.target_path for g in groups]
        assert len(targets) == len(set(targets))

    def test_two_runs_of_siblings_under_one_parent_do_not_collide(self):
        """The case that actually produced a page-id collision.

        Four sibling directories partition into two groups of two. Neither
        group holds a file sitting directly in the shared parent, so both
        compute that parent as their shallowest common directory — and the
        page id is the target path, so the second page would have overwritten
        the first on persist. Verified to fail when target assignment is
        removed.
        """
        params = GroupingParams(min_files=3, max_files=9)
        files = _repo({"src/x/a": 4, "src/x/b": 4, "src/x/c": 4, "src/x/d": 4})
        groups = group_files(files, params=params)
        assert len(groups) > 1, "fixture must produce more than one group"
        targets = [g.target_path for g in groups]
        assert len(targets) == len(set(targets)), targets

    def test_target_path_stays_a_real_directory(self):
        files = _repo(SPEC)
        # The repository root is a real directory whose path is the empty
        # string, and the empty string cannot be a page id, so it is the one
        # target that is a name rather than a path.
        directories = {f.rsplit("/", 1)[0] for f in files if "/" in f} | {"root"}
        for group in group_files(files, params=TINY):
            assert (
                group.target_path in directories
            ), f"{group.target_path!r} is not a directory in this repository"

    def test_a_group_of_root_level_files_is_named_for_the_root(self):
        """The repository root is a directory whose path is the empty string.

        Treating that as "no directory found" sent the group to the
        identity-hash fallback, which then became its page id, its title
        ("D 73789 F 001 Bd") and its scope sentence. It cannot keep the empty
        string either, because the page id is built from the target and an
        empty one mints ``module_page:``, so it gets a name here where
        uniqueness is enforced.
        """
        files = _repo({"": 3, "src/pkg": 10})
        groups = group_files(files, params=GroupingParams(min_files=2, max_files=4))
        root = next(g for g in groups if "setup" in " ".join(g.members) or "f00.py" in g.members)
        assert root.target_path == "root"
        assert all(g.target_path for g in groups)
        assert all("#" not in g.target_path for g in groups)

    def test_a_repository_with_its_own_root_directory_still_gets_unique_targets(self):
        """A top-level directory literally called ``root`` is not a conflict.

        The root group's name is picked against the directories that exist,
        so it steps aside rather than colliding — a collision here would mint
        one page id for two pages and silently drop one on persist.
        """
        files = _repo({"": 3, "root": 6, "src/pkg": 6})
        groups = group_files(files, params=GroupingParams(min_files=2, max_files=8))
        targets = [g.target_path for g in groups]

        assert len(targets) == len(set(targets)), targets
        assert "root" in targets
        assert any(t.startswith("_root") for t in targets), targets

    def test_structural_key_follows_members_not_target(self):
        a = group_files(_repo({"src/one": 4}), params=TINY)[0]
        b = group_files(_repo({"src/two": 4}), params=TINY)[0]
        # Different paths, same shape: the key must differ because the members
        # differ, not because the directory name does.
        assert a.structural_key != b.structural_key

    def test_reordering_members_does_not_change_the_key(self):
        files = _repo({"src/one": 4})
        a = group_files(files, params=TINY)[0]
        b = group_files(list(reversed(files)), params=TINY)[0]
        assert a.structural_key == b.structural_key


class TestSize:
    def test_no_group_exceeds_the_ceiling_unless_it_is_one_directory(self):
        """The fixture must actually contain an over-ceiling group.

        It did not, once: every group in ``SPEC`` fits under the ceiling, so
        the loop body never ran and the test passed whatever the code did.
        """
        spec = {**SPEC, "src/huge": TINY.max_files * 3}
        groups = group_files(_repo(spec), params=TINY)
        over = [g for g in groups if g.file_count > TINY.max_files]
        assert over, "fixture produced no over-ceiling group, so this proves nothing"
        for group in over:
            assert group.oversized
            assert len(group.dirs) == 1

    def test_a_flat_directory_over_the_ceiling_is_flagged_not_split(self):
        """No path rule can split it, so it stays whole and says so."""
        groups = group_files(_repo({"src/big": 20}), params=TINY)
        assert len(groups) == 1
        assert groups[0].oversized
        assert groups[0].file_count == 20

    def test_absorption_considers_the_left_neighbour_too(self):
        """A unit test of the absorption pass, not of a whole partition.

        The walk merges forward only: a thin remainder is offered to the group
        it was flushed after. Absorption is what gives a thin group its other
        neighbour, and the case is reachable but fiddly to provoke end to end,
        so it is asserted directly. Here the right neighbour is full and only
        the left one has room.
        """
        from repowise.core.generation.concept_tree.grouping import (
            _absorb_thin,
            _Partitioner,
        )

        params = GroupingParams(min_files=4, max_files=8)
        part = _Partitioner(params, {})
        groups = [
            part.make(_repo({"src/a": 5})),
            part.make(_repo({"src/b": 1})),
            part.make(_repo({"src/c": 8})),
        ]
        result = _absorb_thin(groups, part)
        assert [g.file_count for g in result] == [6, 8]
        assert not any(g.file_count < params.min_files for g in result)

    def test_grouping_actually_runs_the_absorption_pass(self, monkeypatch):
        """Wiring, asserted separately from behaviour.

        The pass above can be correct and simply not called. The end-to-end
        fixtures cannot catch that, because the forward merge in the walk
        already absorbs most thin remainders, so removing the call leaves
        their output unchanged.
        """
        from repowise.core.generation.concept_tree import grouping as mod

        called: list[int] = []

        def spy(groups, part):
            called.append(len(groups))
            return groups

        monkeypatch.setattr(mod, "_absorb_thin", spy)
        mod.group_files(_repo(SPEC), params=TINY)
        assert called, "group_files did not run the absorption pass"


class TestLayers:
    def test_the_layer_boundary_changes_which_siblings_merge(self):
        """The layer signal steers a split that is already happening.

        Without it the walk merges the first two siblings that fit; with it,
        the first sibling is flushed alone because its layer differs, and the
        two that share a layer merge instead. Asserting composition rather
        than group count is what makes this discriminate: both answers have
        two groups.
        """
        params = GroupingParams(min_files=3, max_files=12)
        files = _repo({"src/aaa": 5, "src/bbb": 5, "src/ccc": 5})
        layers = {f: ("layer:ui" if "/aaa/" in f else "layer:core") for f in files}

        blind = {g.target_path: g.file_count for g in group_files(files, params=params)}
        aware = {
            g.target_path: g.file_count
            for g in group_files(files, layer_of_file=layers, params=params)
        }
        # Blind: aaa and bbb merge, ccc is flushed on its own.
        assert blind == {"src/aaa": 10, "src/ccc": 5}
        # Aware: aaa is flushed alone at the layer boundary, bbb and ccc merge.
        assert aware == {"src/aaa": 5, "src/bbb": 10}
        assert len(blind) == len(aware), "counts match, so composition is the signal"

    def test_a_subtree_within_the_ceiling_is_not_split_by_layer(self):
        """A documented limitation, asserted so it cannot change silently.

        Recursion stops as soon as a subtree fits, so the layer signal is
        never consulted for one that does — a small directory spanning two
        layers stays one page. Splitting every fitting subtree on layer would
        fragment the outline and would inherit the layer data's own noise,
        which is worse than an occasionally impure small page.
        """
        params = GroupingParams(min_files=3, max_files=12)
        files = _repo({"src/aaa": 5, "src/bbb": 5})
        layers = {f: ("layer:ui" if "/aaa/" in f else "layer:core") for f in files}
        groups = group_files(files, layer_of_file=layers, params=params)
        assert len(groups) == 1

    def test_dominant_layer_ties_break_deterministically(self):
        files = _repo({"src/x": 4})
        layers = {f: ("layer:a" if i % 2 else "layer:b") for i, f in enumerate(files)}
        first = group_files(files, layer_of_file=layers, params=TINY)[0]
        second = group_files(list(reversed(files)), layer_of_file=layers, params=TINY)[0]
        assert first.dominant_layer == second.dominant_layer
