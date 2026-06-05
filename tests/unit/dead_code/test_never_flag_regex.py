"""The compiled never-flag regex must be glob-for-glob equivalent to fnmatch."""

from __future__ import annotations

import fnmatch
import os

import networkx as nx
import pytest

from repowise.core.analysis.dead_code.analyzer import (
    DeadCodeAnalyzer,
    _never_flag_regex,
)
from repowise.core.analysis.dead_code.constants import _NEVER_FLAG_PATTERNS

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
    # Negatives — close but should NOT match.
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
