"""Tests for TsconfigResolver — path alias resolution for TS/JS imports."""

from __future__ import annotations

import json
from pathlib import Path

from repowise.core.ingestion.tsconfig_resolver import TsconfigResolver

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _make_resolver(
    tmp_path: Path,
    configs: dict[str, dict],
    path_set: set[str],
) -> TsconfigResolver:
    """Write tsconfig JSON files and return a resolver for the tmp dir."""
    for rel_path, data in configs.items():
        _write_json(tmp_path / rel_path, data)
    return TsconfigResolver(repo_path=tmp_path, path_set=path_set)


def _importer(tmp_path: Path, rel: str) -> str:
    """Return absolute path string for a repo-relative importer."""
    return str(tmp_path / rel)


# ---------------------------------------------------------------------------
# Next.js @/* alias
# ---------------------------------------------------------------------------


class TestBasicAliasResolution:
    def test_nextjs_at_alias(self, tmp_path: Path) -> None:
        """@/* maps to src/* — Next.js default."""
        resolver = _make_resolver(
            tmp_path,
            configs={
                "tsconfig.json": {
                    "compilerOptions": {
                        "baseUrl": ".",
                        "paths": {"@/*": ["./src/*"]},
                    }
                }
            },
            path_set={"src/components/Button.tsx", "src/utils/format.ts"},
        )
        result = resolver.resolve("@/components/Button", _importer(tmp_path, "src/app.tsx"))
        assert result == "src/components/Button.tsx"

        result = resolver.resolve("@/utils/format", _importer(tmp_path, "src/app.tsx"))
        assert result == "src/utils/format.ts"

    def test_baseurl_without_paths(self, tmp_path: Path) -> None:
        """baseUrl: 'src' resolves bare imports like 'utils/format'."""
        resolver = _make_resolver(
            tmp_path,
            configs={
                "tsconfig.json": {
                    "compilerOptions": {"baseUrl": "src"},
                }
            },
            path_set={"src/utils/format.ts", "src/services/api.ts"},
        )
        result = resolver.resolve("utils/format", _importer(tmp_path, "src/app.tsx"))
        assert result == "src/utils/format.ts"

        result = resolver.resolve("services/api", _importer(tmp_path, "src/app.tsx"))
        assert result == "src/services/api.ts"

    def test_exact_non_wildcard_mapping(self, tmp_path: Path) -> None:
        """paths: {'utils': ['./src/utils/index.ts']} — exact key, no wildcard."""
        resolver = _make_resolver(
            tmp_path,
            configs={
                "tsconfig.json": {
                    "compilerOptions": {
                        "baseUrl": ".",
                        "paths": {"utils": ["./src/utils/index.ts"]},
                    }
                }
            },
            path_set={"src/utils/index.ts"},
        )
        result = resolver.resolve("utils", _importer(tmp_path, "src/app.tsx"))
        assert result == "src/utils/index.ts"


# ---------------------------------------------------------------------------
# Directory index resolution
# ---------------------------------------------------------------------------


class TestIndexResolution:
    def test_alias_resolves_to_directory_index(self, tmp_path: Path) -> None:
        """@/Button resolves to src/Button/index.ts when src/Button.ts doesn't exist."""
        resolver = _make_resolver(
            tmp_path,
            configs={
                "tsconfig.json": {
                    "compilerOptions": {
                        "baseUrl": ".",
                        "paths": {"@/*": ["./src/*"]},
                    }
                }
            },
            path_set={"src/Button/index.ts"},
        )
        result = resolver.resolve("@/Button", _importer(tmp_path, "src/app.tsx"))
        assert result == "src/Button/index.ts"

    def test_alias_resolves_to_directory_index_tsx(self, tmp_path: Path) -> None:
        """Tries index.tsx when index.ts isn't in path_set."""
        resolver = _make_resolver(
            tmp_path,
            configs={
                "tsconfig.json": {
                    "compilerOptions": {
                        "baseUrl": ".",
                        "paths": {"@/*": ["./src/*"]},
                    }
                }
            },
            path_set={"src/Button/index.tsx"},
        )
        result = resolver.resolve("@/Button", _importer(tmp_path, "src/app.tsx"))
        assert result == "src/Button/index.tsx"


# ---------------------------------------------------------------------------
# Pattern specificity
# ---------------------------------------------------------------------------


class TestPatternSpecificity:
    def test_specific_pattern_wins_over_broad(self, tmp_path: Path) -> None:
        """@components/* is tried before @/* for '@components/Button'."""
        resolver = _make_resolver(
            tmp_path,
            configs={
                "tsconfig.json": {
                    "compilerOptions": {
                        "baseUrl": ".",
                        "paths": {
                            "@/*": ["./src/*"],
                            "@components/*": ["./src/ui/components/*"],
                        },
                    }
                }
            },
            path_set={
                "src/ui/components/Button.tsx",
                "src/components/Button.tsx",
            },
        )
        result = resolver.resolve("@components/Button", _importer(tmp_path, "src/app.tsx"))
        # Should match @components/* (more specific) -> src/ui/components/*
        assert result == "src/ui/components/Button.tsx"


# ---------------------------------------------------------------------------
# Multiple candidates
# ---------------------------------------------------------------------------


class TestMultipleCandidates:
    def test_first_matching_candidate_wins(self, tmp_path: Path) -> None:
        """['./src/*', './lib/*'] — first candidate that resolves wins."""
        resolver = _make_resolver(
            tmp_path,
            configs={
                "tsconfig.json": {
                    "compilerOptions": {
                        "baseUrl": ".",
                        "paths": {"@/*": ["./src/*", "./lib/*"]},
                    }
                }
            },
            path_set={"src/utils.ts", "lib/utils.ts"},
        )
        result = resolver.resolve("@/utils", _importer(tmp_path, "src/app.tsx"))
        assert result == "src/utils.ts"  # src/* tried first

    def test_second_candidate_when_first_missing(self, tmp_path: Path) -> None:
        """Falls to second candidate when first doesn't exist in path_set."""
        resolver = _make_resolver(
            tmp_path,
            configs={
                "tsconfig.json": {
                    "compilerOptions": {
                        "baseUrl": ".",
                        "paths": {"@/*": ["./src/*", "./lib/*"]},
                    }
                }
            },
            path_set={"lib/utils.ts"},
        )
        result = resolver.resolve("@/utils", _importer(tmp_path, "src/app.tsx"))
        assert result == "lib/utils.ts"


# ---------------------------------------------------------------------------
# jsconfig.json support
# ---------------------------------------------------------------------------


class TestJsconfig:
    def test_jsconfig_fallback(self, tmp_path: Path) -> None:
        """jsconfig.json used when no tsconfig.json present."""
        resolver = _make_resolver(
            tmp_path,
            configs={
                "jsconfig.json": {
                    "compilerOptions": {
                        "baseUrl": ".",
                        "paths": {"@/*": ["./src/*"]},
                    }
                }
            },
            path_set={"src/utils.js"},
        )
        result = resolver.resolve("@/utils", _importer(tmp_path, "src/app.js"))
        assert result == "src/utils.js"

    def test_tsconfig_wins_over_jsconfig(self, tmp_path: Path) -> None:
        """Both in same dir -> tsconfig takes precedence."""
        resolver = _make_resolver(
            tmp_path,
            configs={
                "tsconfig.json": {
                    "compilerOptions": {
                        "baseUrl": ".",
                        "paths": {"@/*": ["./src/*"]},
                    }
                },
                "jsconfig.json": {
                    "compilerOptions": {
                        "baseUrl": ".",
                        "paths": {"@/*": ["./other/*"]},
                    }
                },
            },
            path_set={"src/utils.ts", "other/utils.ts"},
        )
        result = resolver.resolve("@/utils", _importer(tmp_path, "src/app.tsx"))
        assert result == "src/utils.ts"  # tsconfig's mapping, not jsconfig's


# ---------------------------------------------------------------------------
# Extends chains
# ---------------------------------------------------------------------------


class TestExtendsChain:
    def test_child_paths_override_parent(self, tmp_path: Path) -> None:
        """Child has own paths -> parent paths ignored entirely."""
        _write_json(
            tmp_path / "tsconfig.base.json",
            {
                "compilerOptions": {
                    "baseUrl": ".",
                    "paths": {"@/*": ["./parent-src/*"]},
                }
            },
        )
        resolver = _make_resolver(
            tmp_path,
            configs={
                "tsconfig.json": {
                    "extends": "./tsconfig.base.json",
                    "compilerOptions": {
                        "paths": {"@/*": ["./src/*"]},
                    },
                }
            },
            path_set={"src/utils.ts", "parent-src/utils.ts"},
        )
        result = resolver.resolve("@/utils", _importer(tmp_path, "src/app.tsx"))
        assert result == "src/utils.ts"

    def test_child_inherits_parent_paths(self, tmp_path: Path) -> None:
        """Child has no paths -> parent's paths are used."""
        _write_json(
            tmp_path / "tsconfig.base.json",
            {
                "compilerOptions": {
                    "baseUrl": ".",
                    "paths": {"@/*": ["./src/*"]},
                }
            },
        )
        resolver = _make_resolver(
            tmp_path,
            configs={
                "tsconfig.json": {
                    "extends": "./tsconfig.base.json",
                    "compilerOptions": {"strict": True},
                }
            },
            path_set={"src/utils.ts"},
        )
        result = resolver.resolve("@/utils", _importer(tmp_path, "src/app.tsx"))
        assert result == "src/utils.ts"

    def test_extends_chain_two_levels(self, tmp_path: Path) -> None:
        """Grandparent baseUrl inherited through two-level chain."""
        _write_json(
            tmp_path / "tsconfig.grandparent.json",
            {"compilerOptions": {"baseUrl": "src"}},
        )
        _write_json(
            tmp_path / "tsconfig.parent.json",
            {
                "extends": "./tsconfig.grandparent.json",
                "compilerOptions": {
                    "paths": {"@/*": ["./*"]},
                },
            },
        )
        resolver = _make_resolver(
            tmp_path,
            configs={
                "tsconfig.json": {
                    "extends": "./tsconfig.parent.json",
                    "compilerOptions": {"strict": True},
                }
            },
            path_set={"src/utils.ts"},
        )
        # Inherits paths from parent and baseUrl from grandparent.
        # paths @/* -> [./*] resolved against baseUrl "src" -> src/*
        result = resolver.resolve("@/utils", _importer(tmp_path, "src/app.tsx"))
        assert result == "src/utils.ts"

    def test_extends_from_node_modules(self, tmp_path: Path) -> None:
        """extends: '@tsconfig/next' resolved from node_modules.

        The parent config in node_modules sets strict options; the child
        project config adds its own baseUrl + paths relative to the project.
        """
        nm_pkg = tmp_path / "node_modules" / "@tsconfig" / "next"
        nm_pkg.mkdir(parents=True)
        _write_json(
            nm_pkg / "tsconfig.json",
            {
                "compilerOptions": {
                    "strict": True,
                    "esModuleInterop": True,
                }
            },
        )
        resolver = _make_resolver(
            tmp_path,
            configs={
                "tsconfig.json": {
                    "extends": "@tsconfig/next",
                    "compilerOptions": {
                        "baseUrl": ".",
                        "paths": {"@/*": ["./src/*"]},
                    },
                }
            },
            path_set={"src/utils.ts"},
        )
        result = resolver.resolve("@/utils", _importer(tmp_path, "src/app.tsx"))
        assert result == "src/utils.ts"

    def test_circular_extends(self, tmp_path: Path) -> None:
        """Circular extends chain logs warning, no crash, returns None."""
        _write_json(
            tmp_path / "a.json",
            {"extends": "./b.json", "compilerOptions": {}},
        )
        _write_json(
            tmp_path / "b.json",
            {"extends": "./a.json", "compilerOptions": {}},
        )
        # Should not hang or crash.
        resolver = _make_resolver(
            tmp_path,
            configs={
                "tsconfig.json": {
                    "extends": "./a.json",
                    "compilerOptions": {
                        "paths": {"@/*": ["./src/*"]},
                    },
                }
            },
            path_set={"src/utils.ts"},
        )
        # Still resolves because tsconfig.json itself has paths.
        result = resolver.resolve("@/utils", _importer(tmp_path, "src/app.tsx"))
        assert result == "src/utils.ts"


# ---------------------------------------------------------------------------
# Monorepo
# ---------------------------------------------------------------------------


class TestMonorepo:
    def test_per_package_tsconfig(self, tmp_path: Path) -> None:
        """Files in packages/web use web's tsconfig; packages/api use api's."""
        resolver = _make_resolver(
            tmp_path,
            configs={
                "packages/web/tsconfig.json": {
                    "compilerOptions": {
                        "baseUrl": ".",
                        "paths": {"@/*": ["./src/*"]},
                    }
                },
                "packages/api/tsconfig.json": {
                    "compilerOptions": {
                        "baseUrl": ".",
                        "paths": {"@/*": ["./lib/*"]},
                    }
                },
            },
            path_set={
                "packages/web/src/Button.tsx",
                "packages/api/lib/handler.ts",
            },
        )
        # Web file resolves via web tsconfig.
        result = resolver.resolve("@/Button", _importer(tmp_path, "packages/web/src/app.tsx"))
        assert result == "packages/web/src/Button.tsx"

        # API file resolves via api tsconfig.
        result = resolver.resolve("@/handler", _importer(tmp_path, "packages/api/lib/index.ts"))
        assert result == "packages/api/lib/handler.ts"


# ---------------------------------------------------------------------------
# Fallback / backwards compat
# ---------------------------------------------------------------------------


class TestFallback:
    def test_no_tsconfig_at_all(self, tmp_path: Path) -> None:
        """Empty repo -> resolve() always returns None."""
        resolver = TsconfigResolver(repo_path=tmp_path, path_set={"src/utils.ts"})
        result = resolver.resolve("@/utils", _importer(tmp_path, "src/app.tsx"))
        assert result is None

    def test_no_match_returns_none(self, tmp_path: Path) -> None:
        """Unmatched alias -> None (caller creates external: node)."""
        resolver = _make_resolver(
            tmp_path,
            configs={
                "tsconfig.json": {
                    "compilerOptions": {
                        "baseUrl": ".",
                        "paths": {"@/*": ["./src/*"]},
                    }
                }
            },
            path_set={"src/utils.ts"},
        )
        # "react" doesn't match any alias pattern and baseUrl won't find it.
        result = resolver.resolve("react", _importer(tmp_path, "src/app.tsx"))
        assert result is None

    def test_alias_candidate_missing_returns_none(self, tmp_path: Path) -> None:
        """Alias matches but resolved file not in path_set -> None."""
        resolver = _make_resolver(
            tmp_path,
            configs={
                "tsconfig.json": {
                    "compilerOptions": {
                        "baseUrl": ".",
                        "paths": {"@/*": ["./src/*"]},
                    }
                }
            },
            path_set=set(),  # empty path_set
        )
        result = resolver.resolve("@/nonexistent", _importer(tmp_path, "src/app.tsx"))
        assert result is None


# ---------------------------------------------------------------------------
# _match_alias unit tests
# ---------------------------------------------------------------------------


class TestMatchAlias:
    def test_exact_match(self) -> None:
        assert TsconfigResolver._match_alias("utils", "utils") == ""

    def test_exact_no_match(self) -> None:
        assert TsconfigResolver._match_alias("utils", "lodash") is None

    def test_wildcard_match(self) -> None:
        assert TsconfigResolver._match_alias("@/*", "@/components/Button") == "components/Button"

    def test_wildcard_no_match(self) -> None:
        assert TsconfigResolver._match_alias("@components/*", "@/Button") is None

    def test_wildcard_with_suffix(self) -> None:
        assert TsconfigResolver._match_alias("@/*.js", "@/utils.js") == "utils"

    def test_wildcard_empty_capture(self) -> None:
        assert TsconfigResolver._match_alias("@/*", "@/") == ""
