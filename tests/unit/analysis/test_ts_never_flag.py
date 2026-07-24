"""Phase 1 — TS/JS never-flag patterns and entry-point symbol coverage.

These tests pin the dead-code analyzer's TS/JS exemptions: test/spec
files, Storybook stories, codegen output, Next.js / Remix / SvelteKit
convention files, and route-export symbol names. Each assertion has a
matching "negative" — an ordinary source path that must still be
flaggable — so the patterns stay tight.
"""

from __future__ import annotations

import fnmatch


def _matches(path: str) -> bool:
    from repowise.core.analysis.dead_code.constants import _NEVER_FLAG_PATTERNS
    return any(fnmatch.fnmatch(path, p) for p in _NEVER_FLAG_PATTERNS)


class TestTestSpecFiles:
    def test_test_suffix_variants(self):
        assert _matches("src/compose.test.ts")
        assert _matches("src/foo.test.tsx")
        assert _matches("src/foo.test.js")
        assert _matches("src/foo.test.jsx")
        assert _matches("src/foo.test.mjs")
        assert _matches("src/foo.test.cjs")
        assert _matches("packages/app/src/util.spec.ts")

    def test_tests_and_mocks_dirs(self):
        assert _matches("src/__tests__/foo.ts")
        assert _matches("packages/lib/__tests__/util.tsx")
        assert _matches("src/__mocks__/fs.ts")

    def test_ordinary_source_not_flagged(self):
        assert not _matches("src/index.ts")
        assert not _matches("src/utils/format.ts")


class TestStoriesAndBench:
    def test_storybook_stories(self):
        assert _matches("src/Button.stories.tsx")
        assert _matches("packages/ui/src/Card.stories.ts")
        assert _matches("apps/web/Foo.stories.mdx")

    def test_bench_files(self):
        assert _matches("packages/zod/bench/arr.bench.ts")
        assert _matches("bench/parse.bench.js")


class TestCodegenAndConvention:
    def test_next_env_and_gen(self):
        assert _matches("next-env.d.ts")
        assert _matches("apps/web/next-env.d.ts")
        assert _matches("src/api.gen.ts")
        assert _matches("src/types_generated.ts")
        assert _matches("packages/api/src/__generated__/schema.ts")
        assert _matches("packages/api/src/generated/openapi.ts")

    def test_next_app_router_conventions(self):
        assert _matches("apps/web/middleware.ts")
        assert _matches("apps/web/app/sitemap.ts")
        assert _matches("apps/web/app/robots.ts")
        assert _matches("apps/web/app/manifest.ts")
        assert _matches("apps/web/app/icon.tsx")
        assert _matches("apps/web/app/opengraph-image.tsx")
        assert _matches("apps/web/app/global-error.tsx")
        assert _matches("apps/web/instrumentation.ts")

    def test_remix_entries(self):
        assert _matches("app/entry.client.tsx")
        assert _matches("app/entry.server.ts")

    def test_sveltekit_conventions(self):
        assert _matches("src/routes/+page.svelte")
        assert _matches("src/routes/+layout.ts")
        assert _matches("src/routes/api/+server.ts")


class TestNeverFlagDoesNotLeak:
    def test_python_not_affected(self):
        # New TS-only globs must not accidentally match Python paths.
        assert not _matches("src/foo.py")
        assert not _matches("tests/test_foo.py")  # py tests get their own conftest path

    def test_go_not_affected(self):
        assert not _matches("internal/server/handler.go")
        # *_test.go is already exempt via the Go block — sanity check.
        assert _matches("internal/server/handler_test.go")


class TestEntryPointSymbolNames:
    def test_nextjs_route_exports_included(self):
        from repowise.core.analysis.dead_code.analyzer import _ENTRY_POINT_SYMBOL_NAMES
        for name in (
            "generateStaticParams",
            "generateMetadata",
            "getStaticProps",
            "getServerSideProps",
            "reportWebVitals",
        ):
            assert name in _ENTRY_POINT_SYMBOL_NAMES

    def test_remix_route_exports_included(self):
        from repowise.core.analysis.dead_code.analyzer import _ENTRY_POINT_SYMBOL_NAMES
        for name in ("ErrorBoundary", "HydrateFallback", "clientLoader", "shouldRevalidate"):
            assert name in _ENTRY_POINT_SYMBOL_NAMES

    def test_common_names_excluded(self):
        # These names are too common to blanket-exempt — they'd mask
        # genuine dead code in non-route files across every language.
        from repowise.core.analysis.dead_code.analyzer import _ENTRY_POINT_SYMBOL_NAMES
        for name in ("load", "action", "loader", "meta", "metadata", "config", "headers"):
            assert name not in _ENTRY_POINT_SYMBOL_NAMES


class TestTsUnusedExportConstants:
    def test_non_importable_kinds_allows_ts_const_and_var(self):
        from repowise.core.analysis.dead_code.analyzer import _non_importable_kinds
        kinds = _non_importable_kinds("typescript")
        assert "constant" not in kinds
        assert "variable" not in kinds
        assert "method" in kinds
        assert "field" in kinds

    def test_non_importable_kinds_allows_js_const_and_var(self):
        from repowise.core.analysis.dead_code.analyzer import _non_importable_kinds
        kinds = _non_importable_kinds("javascript")
        assert "constant" not in kinds
        assert "variable" not in kinds

    def test_other_languages_retain_universal_non_importable(self):
        from repowise.core.analysis.dead_code.analyzer import _non_importable_kinds
        python_kinds = _non_importable_kinds("python")
        assert "constant" in python_kinds
        assert "variable" in python_kinds

