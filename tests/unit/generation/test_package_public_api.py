"""Package pages and their computed Public API.

A package root always heads a page covering the whole package, and that page's
prompt carries what the package publishes: read from the manifest's entries
and the re-exports behind them, never guessed. A class nothing in the repo
imports is still public when the package's front door re-exports it.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import networkx as nx

from repowise.core.generation.context.public_api import (
    _Resolver,
    api_roots,
    compute_public_api,
)
from repowise.core.generation.context_assembler import ContextAssembler, FilePageContext
from repowise.core.generation.page_generator import PageGenerator
from repowise.core.generation.page_generator.levels import _rollup_child_pages
from repowise.core.generation.selection.selector import _build_module_groups
from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.package_roots import package_roots_from_paths
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.traverser import FileTraverser
from repowise.core.providers.llm.mock import MockProvider
from tests.unit.generation.test_concept_package_walls import _selection_inputs
from tests.unit.generation.test_selection_contract import FakeFileInfo, FakeParsedFile


def _index(repo: Path, files: dict[str, str]) -> dict:
    for rel, body in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(body, encoding="utf-8")
    parser = ASTParser()
    builder = GraphBuilder(repo)
    for fi in FileTraverser(repo).traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    builder.build()
    return builder._parsed_files


_SDK = {
    "packages/sdk/package.json": json.dumps({"name": "sdk", "main": "./dist/index.js"}),
    "packages/sdk/src/index.ts": (
        "export * from './client';\nexport { Tool as SdkTool } from './tools';\n"
        "export { Client as LegacyClient } from './client';\n"
        "export * as tools from './tools';\n"
    ),
    "packages/sdk/src/client.ts": (
        "/** Talks to the service. */\nexport class Client {\n  send(): void {}\n}\n"
        "function helper(): number { return 1; }\n"
    ),
    "packages/sdk/src/tools.ts": "export class Tool {}\nexport class Hidden {}\n",
    "packages/sdk/src/internal.ts": "export function notPublished(): void {}\n",
    "packages/py/pyproject.toml": '[project]\nname = "pysdk"\n',
    "packages/py/pysdk/__init__.py": "from ._client import Client\nfrom .models import *\n",
    "packages/py/pysdk/_client.py": 'class Client:\n    """Python client."""\n',
    "packages/py/pysdk/models.py": "class Model:\n    pass\n\ndef _private():\n    pass\n",
}


def _api(parsed: dict, prefix: str) -> list[dict]:
    material = [p for p in parsed if p.startswith(prefix)]
    return compute_public_api(material, parsed, package_roots_from_paths(set(parsed)))


def test_manifest_entries_are_the_roots(tmp_path):
    parsed = _index(tmp_path, _SDK)
    roots = api_roots(list(parsed), parsed, package_roots_from_paths(set(parsed)))
    assert roots == ["packages/py/pysdk/__init__.py", "packages/sdk/src/index.ts"]


def test_reexported_class_with_no_importers_is_public(tmp_path):
    parsed = _index(tmp_path, _SDK)
    ts = {(e["name"], e["file"]) for e in _api(parsed, "packages/sdk/")}
    # ``Client`` has in-degree 0 in the repo; the barrel alone publishes it.
    assert ("Client", "packages/sdk/src/client.ts") in ts
    assert ("SdkTool", "packages/sdk/src/tools.ts") in ts
    names = {name for name, _ in ts}
    assert not names & {"Hidden", "notPublished", "helper", "send", "Tool"}
    by_name = {e["name"]: e for e in _api(parsed, "packages/sdk/")}
    # A second name for one symbol is listed, pointing at the first, with no excerpt.
    assert by_name["LegacyClient"]["alias_of"] == "Client"
    assert not by_name["LegacyClient"]["signature"]
    # ``export * as ns`` publishes the module itself, not its members.
    assert (by_name["tools"]["kind"], by_name["tools"]["file"]) == (
        "module",
        "packages/sdk/src/tools.ts",
    )

    py = {(e["name"], e["file"]) for e in _api(parsed, "packages/py/")}
    assert py == {
        ("Client", "packages/py/pysdk/_client.py"),
        ("Model", "packages/py/pysdk/models.py"),
    }


def test_barrel_is_the_root_when_no_manifest_names_one(tmp_path):
    parsed = _index(
        tmp_path,
        {
            "lib/__init__.py": "from .core import Engine\n",
            "lib/core.py": "class Engine:\n    pass\n",
            "lib/sub/__init__.py": "from .x import Deep\n",
            "lib/sub/x.py": "class Deep:\n    pass\n",
        },
    )
    assert [e["name"] for e in compute_public_api(list(parsed), parsed, set())] == ["Engine"]


class _RecordingProvider(MockProvider):
    async def generate(self, *args, **kwargs):
        response = await super().generate(*args, **kwargs)
        response.content = "# SDK\n\nThe SDK client.\n"
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


async def test_public_api_reaches_the_prompt_excerpts(tmp_path, sample_config):
    parsed = _index(tmp_path, _SDK)
    api = _api(parsed, "packages/sdk/")
    provider = _RecordingProvider()
    gen = PageGenerator(provider, ContextAssembler(sample_config), sample_config)
    await gen.generate_module_page(
        "SDK",
        "typescript",
        [_context("packages/sdk/src/index.ts")],
        nx.DiGraph(),
        target_path="packages/sdk",
        public_api=api,
    )
    prompt = provider._calls[-1]["user_prompt"]
    assert '"public_api": [' in prompt
    assert '"name": "Client"' in prompt
    assert '"signature": "class Client' in prompt
    assert "Talks to the service." in prompt


def test_public_api_past_the_budget_keeps_every_name(sample_config):
    api = [
        {"name": f"f{i}", "kind": "function", "file": "a.ts", "signature": "x" * 400, "doc": ""}
        for i in range(200)
    ]
    ctx = ContextAssembler(sample_config).assemble_module_page(
        "A", "typescript", [_context("a.ts")], nx.DiGraph(), public_api=api
    )
    assert [e["name"] for e in ctx.public_api] == [e["name"] for e in api]
    assert ctx.public_api[0]["signature"] and not ctx.public_api[-1]["signature"]


def test_a_package_split_across_groups_gets_its_own_whole_package_page():
    """``packages/kernel`` splits into leaves one level below its root, so no
    chapter would sit at the root itself; being a package puts one there."""
    inputs = _selection_inputs()
    groups = [mg for _, mg in _build_module_groups(inputs).scored]
    kernel = [mg for mg in groups if mg.key == "packages/kernel"]
    assert len(kernel) == 1 and kernel[0].is_rollup
    files = {p.file_info.path for p in inputs.parsed_files}
    assert set(kernel[0].context_paths) == {
        f for f in files if f.startswith("packages/kernel/") and f.endswith(".ts")
    }


def test_a_package_page_is_kept_even_when_it_is_most_of_the_repo():
    files = [f"packages/core/src/{d}/f{i}.py" for d in "abcdef" for i in range(10)]
    parsed = [FakeParsedFile(file_info=FakeFileInfo(path=p)) for p in files]
    parsed.append(FakeParsedFile(file_info=FakeFileInfo(path="packages/core/pyproject.toml")))
    inputs = _selection_inputs()
    inputs.parsed_files = parsed
    keys = {mg.key for _, mg in _build_module_groups(inputs).scored}
    assert "packages/core" in keys


def test_rollup_links_to_leaves_below_it_with_no_chapter_between():
    g = lambda key, rollup=False: SimpleNamespace(key=key, display=key, is_rollup=rollup)  # noqa: E731
    pkg, sub = g("pkgs/a", True), g("pkgs/a/src/x/deep", True)
    groups = [pkg, g("pkgs/a/src/io"), g("pkgs/a/src/model"), sub, g("pkgs/a/src/x/deep/y")]
    assert [c["path"] for c in _rollup_child_pages(pkg, groups)] == [
        "pkgs/a/src/io",
        "pkgs/a/src/model",
        "pkgs/a/src/x/deep",
    ]
    assert [c["path"] for c in _rollup_child_pages(sub, groups)] == ["pkgs/a/src/x/deep/y"]


def test_python_dunder_all_decides_what_is_published(tmp_path):
    parsed = _index(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "lib"\n',
            "lib/__init__.py": (
                "from .__version__ import __version__\nfrom ._models import *\n"
                "from ._util import helper\n\n__all__ = ['__version__', 'Request', 'helper']\n"
            ),
            "lib/__version__.py": '__version__ = "1.0"\n',
            "lib/_models.py": "__all__ = ['Request']\n\nclass Request:\n    pass\n\nclass Unlisted:\n    pass\n",
            "lib/_util.py": "__all__ = []\n\ndef helper():\n    pass\n",
        },
    )
    # A named import reaches past the source's ``__all__``; a wildcard does not.
    names = [e["name"] for e in compute_public_api(list(parsed), parsed, set())]
    assert sorted(names) == ["Request", "__version__", "helper"]


def test_barrels_that_re_export_each_other_publish_everything_from_every_root(tmp_path):
    parsed = _index(
        tmp_path,
        {
            "one/index.ts": "export * from '../two';\nexport class One {}\n",
            "two/index.ts": "export * from '../one';\nexport class Two {}\n",
        },
    )
    resolver = _Resolver(parsed)
    for root in ("one/index.ts", "two/index.ts"):
        # The cycle cut short while walking the first root must not stick to the second.
        assert sorted(name for name, _, _ in resolver.exported(root)) == ["One", "Two"]
