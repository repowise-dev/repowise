"""Tool-loaded files that no import reaches are never reported unreachable.

Each convention has positives (loaded by a tool or runtime by name) and near
misses that must stay flaggable.
"""

from __future__ import annotations

import networkx as nx
import pytest

from repowise.core.analysis.dead_code.constants import never_flag_match, never_flag_path
from repowise.core.analysis.dead_code.file_reachability import is_file_reachable


def _unreachable(path: str) -> bool:
    graph = nx.DiGraph()
    graph.add_node(path, language="typescript")
    return not is_file_reachable(path, graph)


@pytest.mark.parametrize(
    "path",
    [
        # Any tool's ``<name>.config.<js-family ext>`` is loaded by that tool.
        "webpack.config.js",
        "apps/docs/astro.config.mjs",
        "packages/ui/tsup.config.ts",
        "svelte.config.cjs",
        "packages/app/vite.config.mts",
        "drizzle.config.ts",
        # ``.<tool>rc.<js-family ext>``.
        ".eslintrc.js",
        "packages/web/.eslintrc.cjs",
        ".prettierrc.mjs",
        ".mocharc.ts",
        # pnpm's install hook.
        ".pnpmfile.cjs",
        "tools/.pnpmfile.cjs",
        # Next.js request proxy, the successor of ``middleware.ts``.
        "proxy.ts",
        "apps/web/proxy.ts",
        "apps/web/src/proxy.js",
    ],
)
def test_js_tool_convention_files_are_never_flagged(path):
    assert never_flag_match(path)
    assert not _unreachable(path)


@pytest.mark.parametrize(
    "path",
    [
        # CPython imports these at startup when they are on sys.path.
        "sitecustomize.py",
        "src/sitecustomize.py",
        "usercustomize.py",
        "tools/usercustomize.py",
    ],
)
def test_python_startup_hooks_are_never_flagged(path):
    assert never_flag_match(path)


@pytest.mark.parametrize(
    "path",
    [
        "src/config.ts",  # no tool name before ``.config``
        "src/settings.config.py",  # not a JS-family config
        "src/myconfig.js",
        "src/eslintrc.js",  # not a dotfile
        "src/source.js",  # ends in ``rc.js`` but is no rc file
        "src/api/reverse-proxy.ts",
        "src/httpproxy.ts",
        "src/mysitecustomize.py",
        "src/sitecustomize_helpers.py",
    ],
)
def test_near_misses_stay_flaggable(path):
    assert not never_flag_match(path)


@pytest.mark.parametrize(
    "path",
    [
        # Copied libraries and generator output are not this repository's to delete.
        "src/native/external/zlib/trees.c",
        "x-pack/plugin/esql/src/main/generated/org/x/FooEvaluator.java",
    ],
)
def test_vendored_and_generated_files_are_never_flagged(path):
    assert never_flag_path(path)
    assert not _unreachable(path)


@pytest.mark.parametrize(
    "path",
    [
        "src/pkg/external/client.py",
        "x-pack/plugin/inference/src/main/java/org/x/external/Sender.java",
        "src/libraries/System.Runtime/src/System/DateTime.cs",
    ],
)
def test_origin_near_misses_stay_flaggable(path):
    assert not never_flag_path(path)
