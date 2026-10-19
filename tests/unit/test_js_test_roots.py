"""Where a JS/TS runner collects from, read from Vitest and Jest config files."""

from __future__ import annotations

import pytest

from repowise.core.js_test_roots import js_test_options, read_js_test_roots

_VITEST_CONFIG = 'export default defineConfig({ test: { include: ["**/*.vitest.ts", "**/*.eval.ts"] } });\n'
_JEST_CONFIG = 'module.exports = { testMatch: ["<rootDir>/src/**/*.custom.ts"] };\n'
_JEST_REGEX_CONFIG = 'module.exports = { testRegex: ".*\\\\.eval\\\\.ts$" };\n'
_PACKAGE_JSON = '{"name": "app", "jest": {"testMatch": ["<rootDir>/src/**/*.test-custom.ts"]}}\n'


@pytest.mark.parametrize(
    ("files", "path", "expected"),
    [
        ({"vitest.config.ts": _VITEST_CONFIG}, "core/stream/charStream.vitest.ts", True),
        ({"vitest.config.ts": _VITEST_CONFIG}, "evals/model.eval.ts", True),
        ({"vitest.config.ts": _VITEST_CONFIG}, "core/stream/charStream.ts", False),
        ({"jest.config.js": _JEST_CONFIG}, "src/sub/foo.custom.ts", True),
        ({"jest.config.js": _JEST_CONFIG}, "other/foo.custom.ts", False),
        ({"jest.config.js": _JEST_REGEX_CONFIG}, "src/prompt.eval.ts", True),
        ({"jest.config.js": _JEST_REGEX_CONFIG}, "src/prompt.ts", False),
        ({"package.json": _PACKAGE_JSON}, "src/api.test-custom.ts", True),
        ({"package.json": _PACKAGE_JSON}, "lib/api.test-custom.ts", False),
        # Spread or variable yields None (unknown)
        ({"vitest.config.ts": 'export default defineConfig({ test: { include: [...base, "**/*.ts"] } });\n'}, "src/a.ts", None),
        ({"vitest.config.ts": 'export default defineConfig({ test: { include: myPatterns } });\n'}, "src/a.ts", None),
        ({"jest.config.js": "module.exports = { testMatch: getMatches() };\n"}, "src/a.ts", None),
        # vite.config without test block has no opinion
        ({"vite.config.ts": "export default defineConfig({ plugins: [] });\n"}, "src/a.vitest.ts", None),
        # no config anywhere
        ({}, "src/a.vitest.ts", None),
    ],
)
def test_collects_follows_the_config(files, path, expected) -> None:
    assert read_js_test_roots(files.items()).collects(path) is expected


def test_nested_config_anchored_to_its_own_directory() -> None:
    roots = read_js_test_roots(
        [
            ("packages/core/vitest.config.ts", 'export default defineConfig({ test: { include: ["**/*.vitest.ts"] } });\n'),
            ("packages/evals/vitest.config.ts", 'export default defineConfig({ test: { include: ["**/*.eval.ts"] } });\n'),
        ]
    )
    # Core vitest config collects its own *.vitest.ts but not evals
    assert roots.collects("packages/core/stream/charStream.vitest.ts") is True
    assert roots.collects("packages/core/stream/charStream.eval.ts") is False

    # Evals vitest config collects its own *.eval.ts but not vitest
    assert roots.collects("packages/evals/prompt.eval.ts") is True
    assert roots.collects("packages/evals/prompt.vitest.ts") is False

    # Other package is not governed by either
    assert roots.collects("packages/other/charStream.vitest.ts") is None


def test_package_json_with_jest_key_in_package_directory() -> None:
    roots = read_js_test_roots(
        [
            ("packages/app/package.json", '{"name": "app", "jest": {"testMatch": ["<rootDir>/tests/**/*.it.ts"]}}\n'),
        ]
    )
    assert roots.collects("packages/app/tests/integration.it.ts") is True
    assert roots.collects("packages/app/src/index.ts") is False
    assert roots.collects("packages/other/tests/integration.it.ts") is None


def test_options_extracted_from_parsed_package_json() -> None:
    parsed = {"name": "app", "jest": {"testMatch": ["<rootDir>/spec/**/*.ts"]}}
    assert js_test_options("package.json", json_data=parsed) == {
        "patterns": ("spec/**/*.ts",),
        "regexes": (),
    }
    assert js_test_options("README.md", "hello") is None


def test_a_test_key_inside_a_plugin_option_is_not_the_test_block() -> None:
    config = (
        "export default defineConfig({\n"
        '  plugins: [foo({ test: { include: ["src/**/*.ts"] } })],\n'
        '  test: { include: ["**/*.vitest.ts"] },\n'
        "});\n"
    )
    roots = read_js_test_roots([("vitest.config.ts", config)])
    assert roots.collects("src/a.vitest.ts") is True
    assert roots.collects("src/a.ts") is False


def _vitest(*include: str):
    quoted = ", ".join(f'"{p}"' for p in include)
    return read_js_test_roots(
        [("vitest.config.ts", f"export default defineConfig({{ test: {{ include: [{quoted}] }} }});\n")]
    )


def test_a_single_star_stays_inside_one_path_segment() -> None:
    roots = _vitest("src/*.ts")
    assert roots.collects("src/a.ts") is True
    assert roots.collects("src/a/b.ts") is False
    assert _vitest("src/*").collects("src/a/b.ts") is False


def test_a_globstar_spans_zero_or_more_segments() -> None:
    roots = _vitest("**/*.vitest.ts")
    assert roots.collects("a.vitest.ts") is True
    assert roots.collects("core/stream/charStream.vitest.ts") is True
    assert roots.collects("core/stream/charStream.ts") is False


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/a.spec.tsx", True),
        ("src/a.test.js", True),
        ("src/test.js", True),
        ("src/a.test.jsx", True),
        ("src/atest.js", False),
        ("src/a.test.css", False),
    ],
)
def test_jest_default_extglob_pattern(path, expected) -> None:
    roots = read_js_test_roots(
        [("jest.config.js", 'module.exports = { testMatch: ["**/?(*.)+(spec|test).[jt]s?(x)"] };\n')]
    )
    assert roots.collects(path) is expected


def test_a_negation_pattern_excludes() -> None:
    roots = _vitest("**/*.vitest.ts", "!legacy/**")
    assert roots.collects("src/a.vitest.ts") is True
    assert roots.collects("legacy/a.vitest.ts") is False


def test_an_unsupported_negated_extglob_promotes_nothing() -> None:
    assert _vitest("src/!(x).ts").collects("src/a.ts") is False
