"""A fixture-tree namer stands for the test code that may read its fixture tree."""

from __future__ import annotations

import subprocess

import pytest

from repowise.core.analysis.changed_lines import ChangeSet, FileDiff
from repowise.core.analysis.fixture_readers import (
    fixture_root,
    helper_readers,
    with_data_file_readers,
    with_fixture_readers,
)
from repowise.core.analysis.test_collection import plan_scopes, read_checkout
from repowise.core.analysis.test_selection import (
    SHARED_TEST_DATA_REASON,
    SelectionInput,
    TestSelectionConfig,
    select_tests,
)


def test_a_fixture_root_is_the_directory_under_the_outermost_fixtures_directory() -> None:
    assert fixture_root("tests/fixtures/ts_sample/packages/lib/src/orphan.ts") == (
        "tests/fixtures/ts_sample"
    )
    assert fixture_root("pkg/__fixtures__/app/a.ts") == "pkg/__fixtures__/app"
    assert fixture_root("tests/fixtures/repo/fixtures/inner/a.py") == "tests/fixtures/repo"
    assert fixture_root("tests/Fixtures/repo/a.py") == "tests/Fixtures/repo"
    assert fixture_root("tests/fixtures/data.json") is None
    assert fixture_root("src/app.ts") is None


_TEXTS = {
    "tests/test_ts.py": 'ROOT = FIXTURES / "ts_sample"\n',
    "tests/test_dynamic.py": 'ROOT = HERE / "fixtures" / f"{lang}_sample"\n',
    "tests/test_fstring.py": 'ROOT = HERE / f"ts_sample/{part}"\n',
    "tests/helpers.py": "def trees():\n    return list((HERE / 'FIXTURES').iterdir())\n",
    "tests/test_uses_helper.py": "from tests.helpers import trees\n",
    "tests/conftest.py": "import pytest\n",
    "tests/test_other.py": 'ROOT = "ts_sample_extra"\n',
    "tests/fixtures/ts_sample/b.ts": "// ts_sample fixture\n",
    "tests/web/fixtures/data.ts": "/** Synthetic fixtures. */\nexport const page = 1;\n",
    "tests/test_doc.py": '"""Reads fixtures."""\nx = 1  # fixtures\n',
    "src/app.py": "FIXTURE_WORDS = ('fixtures',)\n",
}
_TEST_CODE = [p for p in _TEXTS if p.startswith("tests/")]
_OTHER = [p for p in _TEXTS if not p.startswith("tests/")]


def _read(path: str) -> str | None:
    return _TEXTS.get(path)


def test_a_fixture_namer_becomes_every_piece_of_test_code_naming_its_tree() -> None:
    namers = {"package.json": ["src/cli.py", "tests/fixtures/ts_sample/a.ts"]}
    out = with_fixture_readers(namers, _TEST_CODE, _OTHER, _read)
    # A helper and a dynamically built tree name are readers too; the helper
    # routes to its importers like any namer. Fixture files are not readers,
    # nor is a mention in a comment or a docstring.
    assert out == {
        "package.json": [
            "src/cli.py",
            "tests/test_ts.py",
            "tests/test_dynamic.py",
            "tests/test_fstring.py",
            "tests/helpers.py",
        ]
    }


def test_trees_sharing_a_name_share_their_readers() -> None:
    namers = {"package.json": ["a/fixtures/ts_sample/x.ts", "b/fixtures/ts_sample/y.ts"]}
    out = with_fixture_readers(namers, _TEST_CODE, _OTHER, _read)["package.json"]
    assert "tests/test_ts.py" in out
    assert not any("fixtures/ts_sample" in p for p in out)


def test_a_tree_no_test_code_names_keeps_its_file_as_the_namer() -> None:
    namers = {"package.json": ["tests/fixtures/go_sample/main.go"]}
    assert with_fixture_readers(namers, ["tests/test_other.py"], [], _read) == namers


def test_a_tree_named_by_code_outside_the_test_trees_keeps_its_full_run() -> None:
    texts = {**_TEXTS, "src/indexer.py": 'SAMPLE = "tests/fixtures/ts_sample"\n'}
    namers = {"package.json": ["tests/fixtures/ts_sample/a.ts"]}
    out = with_fixture_readers(namers, _TEST_CODE, [*_OTHER, "src/indexer.py"], texts.get)
    assert out == namers


def test_grep_narrowing_gives_the_same_readers() -> None:
    namers = {"package.json": ["tests/fixtures/ts_sample/a.ts"]}

    def holding(names):
        return {p for p, t in _TEXTS.items() if any(n.lower() in t.lower() for n in names)}

    narrowed = with_fixture_readers(namers, _TEST_CODE, _OTHER, _read, holding)
    assert narrowed == with_fixture_readers(namers, _TEST_CODE, _OTHER, _read)


# --- a changed fixture data file runs the tests reading it (#4094) -----------------


def test_a_data_file_is_read_by_the_test_code_naming_it_or_its_tree() -> None:
    files = ["tests/fixtures/ts_sample/b.json", "tests/fixtures/cases.json"]
    namers = {"tests/fixtures/cases.json": ["tests/test_other.py"]}
    out = with_data_file_readers(files, namers, _TEST_CODE, _OTHER, _read)
    # A file directly in the fixtures directory has no tree: only a name reads it.
    assert out["tests/fixtures/cases.json"] == ["tests/test_other.py"]
    assert out["tests/fixtures/ts_sample/b.json"] == [
        "tests/test_ts.py",
        "tests/test_dynamic.py",
        "tests/test_fstring.py",
        "tests/helpers.py",
    ]


def test_a_data_file_no_test_code_names_keeps_its_full_run() -> None:
    assert with_data_file_readers(["tests/fixtures/x.json"], {}, _TEST_CODE, _OTHER, _read) == {}


def test_a_data_file_named_by_production_code_keeps_its_full_run() -> None:
    namers = {"tests/fixtures/cases.json": ["tests/test_other.py", "src/app.py"]}
    assert (
        with_data_file_readers(["tests/fixtures/cases.json"], namers, _TEST_CODE, _OTHER, _read)
        == {}
    )


def test_a_data_file_in_a_tree_named_by_production_code_keeps_its_full_run() -> None:
    texts = {**_TEXTS, "src/indexer.py": 'SAMPLE = "tests/fixtures/ts_sample"\n'}
    out = with_data_file_readers(
        ["tests/fixtures/ts_sample/b.json"],
        {"tests/fixtures/ts_sample/b.json": ["tests/test_other.py"]},
        _TEST_CODE,
        [*_OTHER, "src/indexer.py"],
        texts.get,
    )
    assert out == {}


_REPO = {
    "pkg/core.py": "def add(a, b):\n    return a + b\n",
    "pkg/loader.py": 'DATA = "tests/fixtures/prod.json"\n',
    "tests/test_core.py": 'CASES = json.loads((HERE / "fixtures" / "cases.json").read_text())\n',
    "tests/test_other.py": "from pkg.core import add\n",
    "tests/test_tree_a.py": 'ROOT = FIXTURES / "golden"\n',
    "tests/test_tree_b.py": 'ROOT = HERE / "fixtures" / "golden"\n',
    "tests/test_helper_user.py": "from tests.helpers import load\n",
    "tests/helpers.py": 'def load():\n    return open("loose.json")\n',
    "tests/orphan_helpers.py": 'def load():\n    return open("stray.json")\n',
    "tests/fixtures/cases.json": "{}",
    "tests/fixtures/orphan.json": "{}",
    "tests/fixtures/prod.json": "{}",
    "tests/fixtures/loose.json": "{}",
    "tests/fixtures/stray.json": "{}",
    "tests/fixtures/golden/a.json": "{}",
    "Cargo.lock": "",
}
_KNOWN = sorted(p for p in _REPO if p.startswith("tests/test_"))


def _selection(tmp_path, changed, config=None, importers=None, rows=()):
    for path, text in _REPO.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    config = config or TestSelectionConfig()
    change = ChangeSet(
        files={p: FileDiff(path=p) for p in changed}, deleted=set(), label="t", base=None, head=None
    )
    plan = plan_scopes(change, config, read_checkout(tmp_path))
    # What the graph answers for a route that is itself a test: that test.
    inferred = [
        {"source_file": r, "test_file": r, "via": "changed-test"}
        for r in plan.routes
        if r in _KNOWN
    ]
    inferred += [{"source_file": s, "test_file": t, "via": "import-graph"} for s, t in rows]
    tiers = {
        "covered": {},
        "inferred": inferred,
        "unknown": [],
        "helper_importers": importers or {},
    }
    inp = SelectionInput(list(changed), (), tiers, config, scopes=plan.scopes, known_tests=_KNOWN)
    return select_tests(inp)


def test_a_fixture_file_named_by_one_test_selects_only_that_test(tmp_path) -> None:
    sel = _selection(tmp_path, ["tests/fixtures/cases.json"])
    assert not sel.run_all, sel.reasons
    assert sel.test_files == ("tests/test_core.py",)
    assert sel.basis == {"tests/fixtures/cases.json": "fixture-readers"}
    why = sel.why["tests/test_core.py"]
    assert why.startswith("tests/fixtures/cases.json changed (fixture-readers:")
    assert "read by tests/test_core.py" in why


def test_a_fixture_file_in_a_tree_named_by_two_tests_selects_both(tmp_path) -> None:
    sel = _selection(tmp_path, ["tests/fixtures/golden/a.json"])
    assert not sel.run_all, sel.reasons
    # ``test_core.py`` names the fixtures directory itself (``HERE / "fixtures"``), which
    # lists every tree in it; ``test_other.py`` names neither and stays out.
    assert sorted(sel.test_files) == [
        "tests/test_core.py",
        "tests/test_tree_a.py",
        "tests/test_tree_b.py",
    ]


def test_a_fixture_file_no_test_names_still_runs_everything(tmp_path) -> None:
    sel = _selection(tmp_path, ["tests/fixtures/orphan.json"])
    assert sel.run_all
    assert sel.reasons[0] == f"tests/fixtures/orphan.json changed: {SHARED_TEST_DATA_REASON}."
    assert sel.basis == {"tests/fixtures/orphan.json": "full-run"}


def test_a_fixture_file_named_by_production_code_still_runs_everything(tmp_path) -> None:
    sel = _selection(tmp_path, ["tests/fixtures/prod.json"])
    assert sel.run_all
    assert sel.reasons[0] == f"tests/fixtures/prod.json changed: {SHARED_TEST_DATA_REASON}."


def test_a_fixture_read_by_a_helper_a_test_imports_selects_that_test(tmp_path) -> None:
    importers = {"tests/helpers.py": ["tests/test_helper_user.py"]}
    rows = [("tests/helpers.py", "tests/test_helper_user.py")]
    sel = _selection(tmp_path, ["tests/fixtures/loose.json"], importers=importers, rows=rows)
    assert not sel.run_all, sel.reasons
    assert sel.test_files == ("tests/test_helper_user.py",)


def test_a_fixture_read_by_a_helper_no_test_imports_still_runs_everything(tmp_path) -> None:
    sel = _selection(tmp_path, ["tests/fixtures/stray.json"])
    assert sel.run_all
    assert sel.reasons[0].startswith(
        "tests/fixtures/stray.json changed; tests/orphan_helpers.py is a test helper no test imports"
    )


def test_a_fixture_changed_with_a_build_file_still_runs_everything(tmp_path) -> None:
    sel = _selection(tmp_path, ["tests/fixtures/cases.json", "Cargo.lock"])
    assert sel.run_all
    assert any(r.startswith("Cargo.lock changed:") for r in sel.reasons)


def test_a_configured_full_run_still_wins_over_the_readers(tmp_path) -> None:
    config = TestSelectionConfig(full_run_on=("tests/fixtures/cases.json",))
    sel = _selection(tmp_path, ["tests/fixtures/cases.json"], config)
    assert sel.run_all
    assert sel.basis == {"tests/fixtures/cases.json": "full-run"}


@pytest.mark.parametrize("path", ["tests/fixtures/sample.py", "tests/fixtures/mod/handler.ts"])
def test_fixture_code_keeps_its_full_run(tmp_path, path) -> None:
    """A test may import fixture code by module name, which no file name search sees."""
    sel = _selection(tmp_path, [path])
    assert sel.run_all


def test_production_code_is_only_tokenized_when_it_holds_a_tree_path(monkeypatch) -> None:
    from repowise.core.analysis import fixture_readers

    texts = {**_TEXTS, "src/indexer.py": 'SAMPLE = "Tests/Fixtures/TS_SAMPLE"\n'}
    other = [*_OTHER, "src/indexer.py"]
    seen: list[str] = []
    real = fixture_readers.code_text

    def counted(path: str, text: str) -> str:
        seen.append(path)
        return real(path, text)

    monkeypatch.setattr(fixture_readers, "code_text", counted)
    out = with_data_file_readers(
        ["tests/fixtures/ts_sample/b.json"], {}, _TEST_CODE, other, texts.get
    )
    # The tree's path in any case still keeps the full run.
    assert out == {}
    assert "src/indexer.py" in seen
    assert "src/app.py" not in seen


# -- test helpers no test imports ---------------------------------------------

NL = chr(10)
_HELPER_SOURCES = {
    "tests/typing/typing_options.py": "from typing import assert_type" + NL + "import click",
    "tests/typing/typing_prompt.py": "from typing import assert_type",
    "tests/test_basic.py": "import typing" + NL + "from typing import Any",
    "tests/autobahn/client/client.py": "import aiohttp",
    "tests/autobahn/test_autobahn.py": 'CMD = ["python", "tests/autobahn/client/client.py"]',
    "src/v4/__testfixtures__/rename.output.tsx": "import { q } from 'q'",
    "src/v4/__tests__/rename.test.cjs": "defineTest(__dirname, 'rename', null, 'rename')",
    "cmd/integration_test/main.go": "package main",
    "pkg/integration/clients/go_test.go": "// go run cmd/integration_test/main.go"
    + NL
    + "package clients",
    "tests/tools/lister_test.go": 'dir := filepath.Join("tools", "x")',
    "tests/tools/run.py": "print(1)",
    "tests/tools/test_run.py": "import os",
}


def test_a_type_check_input_is_read_only_by_the_runnable_tests_near_it() -> None:
    """``from typing import`` names a module, not the ``typing`` directory, so the
    sibling type-check file is no reader; the runnable tests under ``tests/`` may
    list the directory, so they are."""
    found = helper_readers(
        ["tests/typing/typing_options.py"], list(_HELPER_SOURCES), _HELPER_SOURCES.get
    )
    assert found == {
        "tests/typing/typing_options.py": [
            "tests/autobahn/test_autobahn.py",
            "tests/test_basic.py",
            "tests/tools/lister_test.go",
            "tests/tools/test_run.py",
        ]
    }


def test_a_helper_started_by_path_is_read_by_the_test_naming_it() -> None:
    found = helper_readers(
        ["tests/autobahn/client/client.py", "src/v4/__testfixtures__/rename.output.tsx"],
        list(_HELPER_SOURCES),
        _HELPER_SOURCES.get,
    )
    assert found["tests/autobahn/client/client.py"] == ["tests/autobahn/test_autobahn.py"]
    # A fixture runner names the case, and adds ``.input.tsx`` / ``.output.tsx`` itself.
    assert found["src/v4/__testfixtures__/rename.output.tsx"] == [
        "src/v4/__tests__/rename.test.cjs"
    ]


def test_an_entry_point_is_named_by_its_directory_and_comments_do_not_count() -> None:
    found = helper_readers(
        ["cmd/integration_test/main.go"], list(_HELPER_SOURCES), _HELPER_SOURCES.get
    )
    assert found == {"cmd/integration_test/main.go": []}
    sources = {
        **_HELPER_SOURCES,
        "pkg/x_test.go": 'exec.Command("go", "run", "./cmd/integration_test")',
    }
    found = helper_readers(["cmd/integration_test/main.go"], list(sources), sources.get)
    assert found == {"cmd/integration_test/main.go": ["pkg/x_test.go"]}


def test_a_helper_below_a_listed_directory_is_read_by_the_test_listing_it() -> None:
    """A test above the helper writing its parent's parent reaches it unnamed."""
    sources = {
        "suite/cases/sub/case.py": "x = 1",
        "e2e/test_cases.py": 'ROOT = "suite/cases"',
        "e2e/test_other.py": "import os",
    }
    found = helper_readers(["suite/cases/sub/case.py"], list(sources), sources.get)
    assert found == {"suite/cases/sub/case.py": ["e2e/test_cases.py"]}


def test_a_case_list_or_a_lister_beside_it_is_a_reader() -> None:
    """Data naming the helper, or code beside it listing its directory, reach it;
    neither is a runnable test, so selection keeps the full run."""
    sources = {
        "suite/cases/sub/case.py": "x = 1",
        "suite/cases/sub/run_all.py": "for f in Path(__file__).parent.glob('*.py'): ...",
        "e2e/cases.yaml": "- case.py",
    }
    found = helper_readers(["suite/cases/sub/case.py"], list(sources), sources.get)
    assert found == {"suite/cases/sub/case.py": ["e2e/cases.yaml", "suite/cases/sub/run_all.py"]}


def test_a_helper_at_the_repository_root_keeps_its_users_unknown() -> None:
    assert helper_readers(["helpers.py"], ["test_a.py"], {"test_a.py": ""}.get) == {}


def test_a_helper_a_runner_config_names_keeps_its_users_unknown() -> None:
    """An alias or setup file hands it to tests that never import or name it."""
    sources = {
        "web/vitest.config.ts": "alias: { '@render': './src/test/render.tsx' }",
        "web/src/test/render.tsx": "export {}",
        "web/src/a.test.tsx": "import { render } from '@render'",
    }
    assert helper_readers(["web/src/test/render.tsx"], list(sources), sources.get) == {}


def test_a_config_spelling_the_helper_directory_keeps_its_users_unknown() -> None:
    sources = {
        "web/vitest.config.ts": "alias: { '@h': './src/test' }",
        "web/src/test/render.tsx": "export {}",
        "web/e2e/a.test.tsx": "import { render } from '@h/render'",
    }
    tests = ["web/src/test/render.tsx", "web/e2e/a.test.tsx"]
    assert helper_readers(["web/src/test/render.tsx"], tests, sources.get) == {}
    # A relative alias target of one name is the directory too.
    sources["web/vitest.config.ts"] = "alias: { 'test-utils': './test' }"
    sources["web/src/vitest.config.ts"] = sources.pop("web/vitest.config.ts")
    assert helper_readers(["web/src/test/render.tsx"], tests, sources.get) == {}
    # A lone directory name where tests live hands no helper to a test; the test
    # outside the helper's tree reads it through the alias path it writes.
    sources["web/src/vitest.config.ts"] = "include: ['test/**/*.test.tsx'], root: 'src/'"
    found = helper_readers(["web/src/test/render.tsx"], tests, sources.get)
    assert found == {"web/src/test/render.tsx": ["web/e2e/a.test.tsx"]}


# aiohttp: the helper's stem is a word of the package's own module and prose.
_AUTOBAHN = {
    "setup.cfg": "filterwarnings =\n    ignore:Unclosed client session <aiohttp.client.ClientSession",
    "pyproject.toml": 'description = "Async http client/server framework (asyncio)"',
    "tests/conftest.py": "from aiohttp.client import ClientSession\nclient_ssl_ctx = 1",
    "tests/test_web.py": "async def test_x(aiohttp_client):\n    client = await aiohttp_client(app)",
    "tests/autobahn/client/client.py": "import aiohttp",
    "tests/autobahn/test_autobahn.py": 'CMD = ["python", "tests/autobahn/client/client.py"]',
}
_CLIENT = "tests/autobahn/client/client.py"


def _tests(sources: dict[str, str]) -> list[str]:
    return [p for p in sources if p.startswith("tests/")]


def test_a_different_dotted_name_or_prose_does_not_name_the_helper() -> None:
    """``aiohttp.client`` is another module and ``client session`` prose: neither the
    configs nor the conftest name the helper. A bare ``client`` in a test may be an
    import of it, so that test still reads it."""
    found = helper_readers([_CLIENT], _tests(_AUTOBAHN), _AUTOBAHN.get)
    assert found == {_CLIENT: ["tests/autobahn/test_autobahn.py", "tests/test_web.py"]}


def test_a_module_path_resolving_to_the_helper_names_it() -> None:
    sources = {
        **_AUTOBAHN,
        "tests/helpers/abs.py": "import tests.autobahn.client.client",
        "tests/helpers/alias.py": "import tests.autobahn.client as h\nh.client.main()",
        "tests/helpers/other.py": "from tests.other.client import run",
        "tests/autobahn/client/near.py": "from .client import main",
        "tests/autobahn/client/far.py": "from ..client import main",
        "tests/autobahn/up.py": "from .client.client import main",
        "tests/helpers/attr.py": "x = make().client",
    }
    found = helper_readers([_CLIENT], _tests(sources), sources.get)[_CLIENT]
    for reader in ("abs", "alias"):
        assert f"tests/helpers/{reader}.py" in found
    assert "tests/autobahn/client/near.py" in found
    assert "tests/autobahn/up.py" in found
    for not_reader in ("tests/helpers/other.py", "tests/helpers/attr.py", "tests/conftest.py"):
        assert not_reader not in found
    # ``..client`` from inside the package is tests/autobahn/client, the package itself.
    assert "tests/autobahn/client/far.py" not in found


def test_a_config_names_the_helper_only_by_its_path() -> None:
    """A path to another file of the same name is not the helper; its own path is."""
    sources = {**_AUTOBAHN, "tests/helpers/rel.py": 'P = "../client/client.py"'}
    sources["setup.cfg"] = "omit = aiohttp/client.py src/aiohttp/client"
    found = helper_readers([_CLIENT], _tests(sources), sources.get)[_CLIENT]
    assert "tests/helpers/rel.py" in found
    for named in ("addopts = tests/autobahn/client/client.py", "-p tests.autobahn.client.client"):
        sources["setup.cfg"] = named
        assert helper_readers([_CLIENT], _tests(sources), sources.get) == {}


def test_an_unreadable_test_file_keeps_every_helper_unknown() -> None:
    """It may be the file naming the helper, so no helper is cleared."""
    sources = {"suite/cases/sub/case.py": "x = 1", "e2e/test_ok.py": "import os"}
    files = [*sources, "e2e/test_locked.py"]
    assert helper_readers(["suite/cases/sub/case.py"], files, sources.get) == {}
