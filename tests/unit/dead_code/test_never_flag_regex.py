"""The compiled never-flag regex must be glob-for-glob equivalent to fnmatch."""

from __future__ import annotations

import fnmatch
import os

import networkx as nx
import pytest

from repowise.core.analysis.dead_code.analyzer import DeadCodeAnalyzer
from repowise.core.analysis.dead_code.constants import (
    _NEVER_FLAG_PATTERNS,
    _never_flag_regex,
    never_flag_match,
)

# Positives derived from the glob list plus negatives that brush close to it.
_PROBE_PATHS = [
    "pkg/__init__.py",
    "src/app/__main__.py",
    "tests/conftest.py",
    "alembic/env.py",
    "backend/alembic/versions/0042_add_users.py",
    "manage.py",
    "site/wsgi.py",
    "api/asgi.py",
    "app/migrations/0001_initial.py",
    "db/schema.sql",
    "scripts/seed_users.py",
    "types/global.d.ts",
    "setup.py",
    "setup.cfg",
    "next.config.mjs",
    "apps/web/vite.config.ts",
    "tailwind.config.js",
    "postcss.config.cjs",
    "jest.config.ts",
    "vitest.config.mts",
    "app/dashboard/page.tsx",
    "app/dashboard/layout.ts",
    "app/api/users/route.ts",
    "app/loading.tsx",
    "app/error.tsx",
    "app/not-found.tsx",
    "ui/components/Button.qml",
    "src/Forms/MainForm.Designer.vb",
    "src/Forms/MainForm.designer.vb",
    "src/My Project/Settings.Designer.vb",
    "src/My Project/Resources.vb",
    "src/Properties/AssemblyInfo.vb",
    # Negatives — close but should NOT match.
    "src/Forms/MainForm.vb",
    "src/DesignerHelper.vb",
    "src/MyProject/Helper.vb",
    "src/pages.py",
    "core/router.py",
    "lib/pagination.tsx",
    "app/page_helpers.ts",
    "frontend/layouts.css",
    "packages/core/src/engine.py",
    "tools/configure.py",
    "src/seedling_data.txt",  # *seed* glob actually matches this — keep parity either way
    "docs/setup.md",
]


def _fnmatch_any(path: str) -> bool:
    return any(fnmatch.fnmatch(path, pat) for pat in _NEVER_FLAG_PATTERNS)


class TestRegexEquivalence:
    @pytest.mark.parametrize("path", _PROBE_PATHS)
    def test_probe_paths_match_fnmatch(self, path):
        regex = _never_flag_regex(_NEVER_FLAG_PATTERNS)
        assert bool(regex.match(os.path.normcase(path))) == _fnmatch_any(path), path

    def test_every_pattern_has_a_matching_path(self):
        """Synthesize a concrete path per glob and assert both sides agree."""
        regex = _never_flag_regex(_NEVER_FLAG_PATTERNS)
        for pat in _NEVER_FLAG_PATTERNS:
            concrete = pat.replace("*", "x")
            assert bool(regex.match(os.path.normcase(concrete))) == _fnmatch_any(concrete), pat


class TestMemoizedMatch:
    """The lru_cached match wrapper must equal the direct regex match,
    including for symbol-node ids (``path::Name``), which the detector
    passes feed it alongside file paths."""

    @pytest.mark.parametrize(
        "path",
        [*_PROBE_PATHS, "pkg/a.go::Handler", "cmd/main_test.go::TestX", "app/page.tsx::Page"],
    )
    def test_memoized_equals_direct(self, path):
        direct = bool(_never_flag_regex(_NEVER_FLAG_PATTERNS).match(os.path.normcase(path)))
        assert never_flag_match(path) == direct, path
        # Second call exercises the cached branch.
        assert never_flag_match(path) == direct, path


class TestShouldNeverFlag:
    def _analyzer(self) -> DeadCodeAnalyzer:
        return DeadCodeAnalyzer(nx.DiGraph(), {})

    def test_whitelist_takes_priority(self):
        assert self._analyzer()._should_never_flag("anything.py", {"anything.py"})

    def test_glob_hit(self):
        assert self._analyzer()._should_never_flag("app/dashboard/page.tsx", set())

    def test_glob_miss(self):
        assert not self._analyzer()._should_never_flag("core/router.py", set())

    def test_workspace_never_flag_node_attr(self):
        g = nx.DiGraph()
        g.add_node("bench/jmh_runner.java", is_never_flag=True)
        analyzer = DeadCodeAnalyzer(g, {})
        assert analyzer._should_never_flag("bench/jmh_runner.java", set())

    def test_init_py_barrel(self):
        assert self._analyzer()._should_never_flag("pkg/sub/__init__.py", set())

    def test_qml_component(self):
        # Instantiated by type name and loaded by the Qt runtime, so a QML
        # file never has a static importer to find.
        assert self._analyzer()._should_never_flag("ui/components/Button.qml", set())
        assert not self._analyzer()._should_never_flag("ui/components/button.js", set())

    @pytest.mark.parametrize(
        "path",
        [
            "src/Forms/MainForm.Designer.vb",
            "src/Forms/MainForm.designer.vb",
            "src/Properties/AssemblyInfo.vb",
            "src/My Project/Settings.Designer.vb",
            "src/My Project/Resources.vb",
        ],
    )
    def test_vbnet_generated_files_are_never_flagged(self, path):
        """VB.NET's generated side-files have no static importer by design,
        the same way the C# designer and AssemblyInfo files above do not."""
        assert self._analyzer()._should_never_flag(path, set())

    @pytest.mark.parametrize(
        "path",
        ["src/Forms/MainForm.vb", "src/DesignerHelper.vb", "src/MyProject/Helper.vb"],
    )
    def test_hand_written_vbnet_files_stay_flaggable(self, path):
        assert not self._analyzer()._should_never_flag(path, set())


class TestSuffixIndexEquivalence:
    r"""``never_flag_match`` buckets the patterns by the literal text
    after their last ``*`` and only tests the buckets a path's tail can reach.
    That is sound only because ``fnmatch.translate`` end-anchors every
    alternative, so these pin both the equivalence and the assumption.
    """

    def test_no_pattern_uses_a_construct_the_split_cannot_see(self):
        """The suffix is taken with ``rsplit("*", 1)``, which yields a literal
        tail only while no pattern uses ``?``, a character class or a brace.
        Adding one would silently make the prefilter drop real matches, so
        fail here rather than there."""
        offenders = [p for p in _NEVER_FLAG_PATTERNS if set("?[]{}") & set(p)]
        assert offenders == [], offenders

    def test_every_alternative_is_end_anchored(self):
        r"""Each branch carries its own ``\Z``, which is what makes "the
        pattern ends in literal S, so it can only match a path ending in S"
        true. One shared trailing anchor would make the buckets wrong rather
        than merely slow."""
        for pat in ("*.sh", "*/apps/**/*.cc", "build.rs"):
            assert fnmatch.translate(os.path.normcase(pat)).endswith(r"\Z")

    @pytest.mark.parametrize(
        "path",
        [
            # Repeated segments: fnmatch emits atomic groups for interior
            # ``*literal`` runs, which commit to the FIRST occurrence and never
            # retry a later one, so a matcher that backtracks would over-match.
            "a/tests/b/tests/c/d.cpp",
            "vendor/x/vendor/y.rs",
            "src/generated/deep/generated/nested/file.ts",
            "apps/x/apps/y.cc",
            "build/a/build/b.rs",
            # Tails that brush the bucket boundaries.
            "a.sh",
            "a.sh::Foo",
            "A.SH",
            "x/y/livereload/livereload.js",
            "livereload/livereload.js",
            "notbuild.rs",
            "build.rs",
            "deeply/nested/build.rs",
            "",
            "a",
            "::",
        ],
    )
    def test_matches_the_single_alternation(self, path):
        expected = bool(_never_flag_regex(_NEVER_FLAG_PATTERNS).match(os.path.normcase(path)))
        assert never_flag_match(path) == expected, path

    def test_synthesized_probe_per_pattern_agrees(self):
        """One concrete path per glob, plus near-miss variants around it, run
        through the bucketed matcher rather than the raw regex."""
        regex = _never_flag_regex(_NEVER_FLAG_PATTERNS)
        for pat in _NEVER_FLAG_PATTERNS:
            base = pat.replace("**", "x").replace("*", "x")
            for probe in (base, base.upper(), f"prefix/{base}", f"{base}/trailing.py"):
                expected = bool(regex.match(os.path.normcase(probe)))
                assert never_flag_match(probe) == expected, (pat, probe)
