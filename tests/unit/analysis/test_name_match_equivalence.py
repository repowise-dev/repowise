"""``tests_matching_by_name`` against a plain, scan-everything oracle.

The production function answers from indexes built once (files by directory,
tests by name and by name prefix). The oracle below is the same rule written the
obvious way: for every target, test every test file. Seeded random trees,
including mirrored directories, one stem in many directories, JS/TS suffixes and
more same-name tests than ``MAX_TESTS_PER_TARGET``, must give identical results.
"""

from __future__ import annotations

import random
from pathlib import PurePath

import pytest

from repowise.core.analysis.test_reach.model import ReachedBy, capped
from repowise.core.analysis.test_reachability import MAX_TESTS_PER_TARGET
from repowise.core.analysis.test_reachability import tests_matching_by_name as name_match
from repowise.core.test_paths import js_family_extensions, names_test_for, paired_test_names

_JS_TEST_SUFFIXES = frozenset({".ts", ".tsx", ".js", ".mts", ".cts"})
_JS_SOURCE_SUFFIXES = frozenset(
    {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts", ".vue", ".svelte", ".astro"}
)


def _overlap(source: str, test: str) -> int:
    count = 0
    for a, b in zip(reversed(source.split("/")[:-1]), reversed(test.split("/")[:-1]), strict=False):
        if a != b:
            break
        count += 1
    return count


def _mirrored_by_rival(target, test, all_files, test_files) -> bool:
    """A same-named source file mirrors *test* more deeply than *target* does."""
    name = target.rpartition("/")[2]
    for rival in all_files:
        if rival == target or rival in test_files or rival.rpartition("/")[2] != name:
            continue
        in_family = PurePath(rival).suffix.lower() in _JS_SOURCE_SUFFIXES
        if PurePath(test).suffix.lower() in _JS_SOURCE_SUFFIXES and not in_family:
            continue
        if _overlap(rival, test) > _overlap(target, test):
            return True
    return False


def _oracle(targets, test_files, indexed_files=()) -> dict[str, ReachedBy]:
    all_files = set(targets) | set(test_files) | set(indexed_files)
    owned_by: dict[str, set[str]] = {}
    for source in all_files:
        directory = source.rpartition("/")[0]
        for name in paired_test_names(source):
            for test in test_files:
                in_place = test.rpartition("/")[0] == directory
                if in_place and test.rpartition("/")[2] == name and source != test:
                    owned_by.setdefault(test, set()).add(source)

    out: dict[str, ReachedBy] = {}
    for target in targets:
        target_dir = target.rpartition("/")[0]
        siblings = [p for p in all_files if p != target and p.rpartition("/")[0] == target_dir]
        found = []
        for test in test_files:
            test_stem = PurePath(test).stem.lower()
            target_stem = PurePath(target).stem.lower()
            bare = target_stem[1:] if target_stem.startswith("u") else target_stem
            if target_stem not in test_stem and bare not in test_stem:
                continue
            if PurePath(test).suffix.lower() in _JS_TEST_SUFFIXES and (
                PurePath(target).suffix.lower() not in _JS_SOURCE_SUFFIXES
            ):
                continue
            if not names_test_for(test, target, siblings):
                continue
            if test in owned_by and target not in owned_by[test]:
                continue
            if _mirrored_by_rival(target, test, all_files, test_files):
                continue
            found.append(test)
        if not found:
            continue
        dirs = target.split("/")[:-1]

        def overlap(test: str, dirs=dirs) -> int:
            count = 0
            for a, b in zip(reversed(dirs), reversed(test.split("/")[:-1]), strict=False):
                if a != b:
                    break
                count += 1
            return count

        best = max(overlap(t) for t in found)
        if best > 0:
            found = [t for t in found if overlap(t) == best]
        out[target] = capped(target, found, "name-match")
    return out


_STEMS = ["x", "loader", "foo", "foo_bar", "index", "analyzer", "uFoo", "a.config", "Widget"]
_SOURCE_EXTS = [".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".go", ".java", ".ex", ".pas", ".rs"]
_DIRS = ["src", "pkg", "tests", "a", "b", "dead_code", "doc_drift", "__tests__", "ui", "lib"]
_QUALIFIERS = ["_golden", ".retry", "-cli", "Retry", "_bar", "s", "2"]


def _dir(rng: random.Random) -> str:
    return "/".join(rng.choice(_DIRS) for _ in range(rng.randint(0, 3)))


def _join(directory: str, name: str) -> str:
    return f"{directory}/{name}" if directory else name


def _test_name(rng: random.Random, source: str) -> str:
    p = PurePath(source)
    names = sorted(paired_test_names(source))
    if rng.random() < 0.6:
        return rng.choice(names)
    # A qualified name: a paired name with a qualifier after the stem.
    base = rng.choice(names)
    stem = p.stem
    qualifier = rng.choice(_QUALIFIERS)
    return base.replace(stem, stem + qualifier, 1) if stem in base else base


def _tree(seed: int):
    rng = random.Random(seed)
    sources = sorted(
        {
            _join(_dir(rng), rng.choice(_STEMS) + rng.choice(_SOURCE_EXTS))
            for _ in range(rng.randint(3, 40))
        }
    )
    tests = [
        _join(_dir(rng), _test_name(rng, rng.choice(sources))) for _ in range(rng.randint(3, 60))
    ]
    if seed % 5 == 0:
        # One stem in many directories: more same-name tests than the cap.
        stem = rng.choice(_STEMS)
        tests += [f"tests/{i:03}/test_{stem}.py" for i in range(MAX_TESTS_PER_TARGET + 5)]
        sources += [f"src/{stem}.py", f"src/{rng.choice(_DIRS)}/{stem}.py"]
    if seed % 7 == 0:
        tests += tests[:3]  # a repeated entry
    targets = [*rng.sample(sources, k=rng.randint(1, len(sources))), "ghost/none.py"]
    indexed = rng.sample(sources, k=rng.randint(0, len(sources)))
    return targets, tests, indexed


@pytest.mark.parametrize("seed", range(150))
def test_index_lookup_matches_the_scan_everything_oracle(seed: int) -> None:
    targets, tests, indexed = _tree(seed)
    container = set(tests) if seed % 2 else tests

    assert name_match(targets, container, indexed) == _oracle(targets, container, indexed)


def test_the_random_trees_exercise_matches_mirroring_and_the_cap() -> None:
    capped_total = mirrored = matched = 0
    for seed in range(150):
        targets, tests, indexed = _tree(seed)
        for target, row in _oracle(targets, tests, indexed).items():
            matched += 1
            capped_total += row.total > MAX_TESTS_PER_TARGET
            mirrored += any(
                t.rpartition("/")[0].rpartition("/")[2]
                == target.rpartition("/")[0].rpartition("/")[2]
                and "/" in target
                for t in row.tests
            )
    assert matched > 150
    assert capped_total > 0
    assert mirrored > 0


def test_the_js_family_comes_from_the_language_registry() -> None:
    assert js_family_extensions() == _JS_SOURCE_SUFFIXES
