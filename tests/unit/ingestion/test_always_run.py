"""Tests the graph cannot see into: tree walks and self-runs, found from source text."""

from __future__ import annotations

import time

import pytest

from repowise.core.ingestion.always_run import (
    OwnCode,
    always_run_reason,
    own_code,
    stamp_always_run,
)

_OWN = OwnCode(
    commands=frozenset({"tool", "tool-hook"}),
    bin_files=frozenset({"bin/tool.mjs"}),
    modules=frozenset({"tool", "tool.cli", "tool.cli.main", "cli.main", "main"}),
)

_WALK = "it lists and reads files under a source directory"


def _reason(path: str, text: str) -> str | None:
    return always_run_reason(path, text, _OWN)


@pytest.mark.parametrize(
    "text",
    [
        # A lint test: anchored root, rglob, then a parse of each file.
        "import ast, pathlib\n"
        "_PACKAGES = pathlib.Path(__file__).resolve().parents[2] / 'packages'\n"
        "def test_no_copies():\n"
        "    for p in _PACKAGES.rglob('*.py'):\n"
        "        ast.parse(p.read_text())\n",
        # The root comes through a module's ``__file__`` and a loop variable.
        "from pathlib import Path\nimport tool\n"
        "ROOT = Path(tool.__file__).parent\n"
        "SCAN = [ROOT / 'a', ROOT / 'b']\n"
        "def test_x():\n"
        "    for entry in SCAN:\n"
        "        for f in sorted(entry.glob('*.j2')):\n"
        "            assert 'x' not in f.read_text()\n",
        # ``os.walk`` from the working directory.
        "import os\n"
        "def test_x():\n"
        "    for d, _, names in os.walk(os.getcwd()):\n"
        "        open(os.path.join(d, names[0]))\n",
    ],
)
def test_a_python_test_walking_the_source_tree_is_detected(text) -> None:
    assert _reason("tests/test_lint.py", text) == _WALK


@pytest.mark.parametrize(
    "text",
    [
        # Its own fixtures directory.
        "from pathlib import Path\n"
        "FIXTURES = Path(__file__).parent / 'fixtures'\n"
        "def test_x():\n"
        "    for p in FIXTURES.glob('*.json'):\n"
        "        p.read_text()\n",
        # A temporary directory it filled itself.
        "def test_x(tmp_path):\n"
        "    for p in tmp_path.iterdir():\n"
        "        p.read_text()\n",
        # Lists the tree but never reads a file.
        "from pathlib import Path\n"
        "ROOT = Path(__file__).parent\n"
        "def test_x():\n"
        "    assert list(ROOT.glob('*.py'))\n",
    ],
)
def test_a_python_test_walking_its_own_material_is_not(text) -> None:
    assert _reason("tests/test_x.py", text) is None


@pytest.mark.parametrize(
    "text",
    [
        # Its own directory: sibling case files, not the source tree.
        "from pathlib import Path\n"
        "HERE = Path(__file__).parent\n"
        "def test_cases():\n"
        "    for p in HERE.glob('case_*.txt'):\n"
        "        p.read_text()\n",
        # ``git ls-files`` in a repository the test built in a temp dir.
        "import subprocess\n"
        "def test_x(tmp_path):\n"
        "    out = subprocess.run(['git', 'ls-files'], cwd=tmp_path).stdout\n"
        "    open(tmp_path / out.split()[0])\n",
        # The walk is anchored, but the only read is in an unrelated test.
        "from pathlib import Path\n"
        "ROOT = Path(__file__).parents[2]\n"
        "def test_count():\n"
        "    assert len(list(ROOT.rglob('*.py'))) > 10\n"
        "\n"
        "def test_other(tmp_path):\n"
        "    (tmp_path / 'a').write_text('x')\n"
        "    assert open(tmp_path / 'a').read() == 'x'\n",
    ],
)
def test_walks_that_are_not_the_source_tree_or_read_nothing_from_it(text) -> None:
    assert _reason("tests/unit/test_x.py", text) is None


def test_a_test_beside_the_code_may_walk_its_own_directory() -> None:
    text = (
        "import { readdirSync, readFileSync } from 'node:fs';\n"
        "const dir = new URL('./', import.meta.url);\n"
        "it('guards', () => {\n"
        "  for (const f of readdirSync(dir)) readFileSync(new URL(f, dir), 'utf8');\n"
        "});\n"
    )
    assert _reason("src/tools/boundary.test.ts", text) == _WALK
    assert _reason("test/tools/boundary.test.ts", text) is None


def test_a_large_cli_test_file_is_judged_fast() -> None:
    lines = ["import subprocess"]
    lines += [f"cmd = ['git', 'log', '-{i}']" for i in range(2000)]
    lines += ["def test_x():"] + [f"    subprocess.run(cmd, cwd='d{i}')" for i in range(1000)]
    start = time.perf_counter()
    assert _reason("tests/test_cli_big.py", "\n".join(lines)) is None
    assert time.perf_counter() - start < 2


def test_a_walk_inside_a_helper_counts_when_the_helper_is_given_a_source_root() -> None:
    text = (
        "import fs from 'node:fs';\nimport path from 'node:path';\n"
        "async function collect(dir) {\n"
        "  const entries = await fs.promises.readdir(dir, { withFileTypes: true });\n"
        "  return entries.map((e) => path.join(dir, e.name));\n"
        "}\n"
        "it('guards callsites', async () => {\n"
        "  const root = process.cwd();\n"
        "  const files = await collect(path.join(root, 'src'));\n"
        "  for (const f of files) fs.readFileSync(f, 'utf8');\n"
        "});\n"
    )
    assert _reason("src/gateway/callsites.guard.test.ts", text) == _WALK


def test_a_typescript_test_listing_a_temp_dir_is_not_a_walk() -> None:
    text = (
        "import fs from 'node:fs/promises';\nimport os from 'node:os';\n"
        "const here = new URL('.', import.meta.url);\n"
        "it('cleans up', async () => {\n"
        "  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'x-'));\n"
        "  expect(await fs.readdir(dir)).toEqual([]);\n"
        "  await fs.readFile(path.join(dir, 'a'));\n"
        "});\n"
    )
    assert _reason("src/media/store.test.ts", text) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "import subprocess, sys\n"
            "def test_x():\n"
            "    subprocess.run([sys.executable, '-m', 'tool.cli.main', 'status'])\n",
            "it runs `python -m tool.cli.main` in a child process",
        ),
        (
            "import subprocess\n"
            "def test_x():\n"
            "    subprocess.run(['tool', 'init'], check=True)\n",
            "it runs the project's own command `tool` in a child process",
        ),
    ],
)
def test_a_python_test_running_the_project_in_a_child_process_is_detected(text, expected) -> None:
    assert _reason("tests/test_cli_smoke.py", text) == expected


def test_helper_built_code_strings_name_the_module_they_import() -> None:
    text = (
        "import subprocess, sys\n"
        "def _cmd():\n"
        "    return [sys.executable, '-c', 'from tool.cli import main; main()']\n"
        "def test_x():\n"
        "    cmd = _cmd()\n"
        "    subprocess.run(cmd, input='{}')\n"
    )
    assert _reason("tests/test_hook.py", text) == "it runs code importing `tool.cli` in a child process"


@pytest.mark.parametrize(
    "text",
    [
        # Runs git; the project's name only appears as a directory.
        "import subprocess\n"
        "def test_x(tmp_path):\n"
        "    repo = tmp_path / 'tool'\n"
        "    subprocess.run(['git', 'init'], cwd=repo)\n",
        # A monkeypatch target string is not a ``-m`` module.
        "import subprocess\n"
        "def test_x(monkeypatch):\n"
        "    monkeypatch.setattr('tool.cli.main', None)\n"
        "    subprocess.run(['git', 'status'])\n",
        # Mentions the command in a message, never runs it.
        "import subprocess\n"
        "def test_x():\n"
        "    r = subprocess.run(['git', 'log'])\n"
        "    assert 'tool init' in r.stdout\n",
    ],
)
def test_a_child_process_that_does_not_run_the_project_is_not_detected(text) -> None:
    assert _reason("tests/test_git.py", text) is None


def test_a_typescript_test_spawning_the_package_bin_is_detected() -> None:
    text = (
        "import { spawnSync } from 'node:child_process';\n"
        "const args = process.env.X\n"
        "  ? ['dist/index.js']\n"
        "  : ['bin/tool.mjs', '--help'];\n"
        "it('runs', () => { spawnSync(process.execPath, args); });\n"
    )
    assert _reason("test/launcher.e2e.test.ts", text) == (
        "it runs the project's own bin `bin/tool.mjs` in a child process"
    )


def test_a_bare_file_name_is_not_another_packages_bin() -> None:
    text = (
        "import { spawnSync } from 'node:child_process';\n"
        "it('runs', () => { spawnSync('node', ['tool.mjs']); });\n"
    )
    assert _reason("pkg/a.test.ts", text) is None


def test_a_regex_exec_is_not_a_child_process() -> None:
    text = "it('x', () => { const m = /tool (\\d+)/.exec('tool 1'); expect(m).toBeTruthy(); });\n"
    assert _reason("src/a.test.ts", text) is None


def test_other_languages_are_not_judged() -> None:
    assert _reason("pkg/a_test.go", 'exec.Command("tool")') is None


def test_an_unclosed_string_full_of_backslashes_is_fast() -> None:
    text = "import subprocess\nsubprocess.run(['x', '" + "\\\\" * 5000 + "\n"
    start = time.perf_counter()
    assert _reason("tests/test_win.py", text) is None
    assert time.perf_counter() - start < 1


def test_own_code_takes_console_scripts_and_reads_bins_and_production_modules() -> None:
    texts = {
        "web/package.json": '{"name": "@acme/web", "bin": {"web-tool": "./bin/run.mjs"}}',
        "cli/package.json": '{"name": "@acme/cli", "bin": "cli.js"}',
    }
    paths = [*texts, "pyproject.toml", "src/tool/cli/main.py", "src/tool/__init__.py"]
    # pyproject.toml is never read: its launchers come from the traverser.
    own = own_code(paths, texts.__getitem__, ["tool"])
    assert own.commands == {"tool", "web-tool", "cli"}
    assert own.bin_files == {"web/bin/run.mjs", "cli/cli.js"}
    assert own.modules == {"src.tool.cli.main", "tool.cli.main", "cli.main", "main", "src.tool", "tool"}


def test_stamp_sets_the_reason_on_test_nodes_only() -> None:
    import networkx as nx

    from repowise.core.ingestion.models import FileInfo, ParsedFile

    lint = (
        "import pathlib\nROOT = pathlib.Path(__file__).parents[1]\n"
        "def test_x():\n    [p.read_text() for p in ROOT.rglob('*.py')]\n"
    )
    sources = {"tests/test_lint.py": lint, "tests/test_a.py": "def test_a():\n    pass\n"}
    sources["src/walker.py"] = lint
    graph = nx.DiGraph()
    parsed = {}
    for path, text in sources.items():
        graph.add_node(path, node_type="file")
        info = FileInfo(
            path=path,
            abs_path=path,
            language="python",
            size_bytes=len(text),
            git_hash="",
            last_modified=None,
            is_test=path.startswith("tests/"),
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        )
        parsed[path] = ParsedFile(file_info=info, symbols=[], imports=[], exports=[])
    stamped = stamp_always_run(graph, parsed, {p: t.encode() for p, t in sources.items()})
    assert stamped == 1
    assert graph.nodes["tests/test_lint.py"]["always_run_reason"] == _WALK
    # Scanned and ordinary is "", apart from never scanned (None).
    assert graph.nodes["tests/test_a.py"]["always_run_reason"] == ""
    assert "always_run_reason" not in graph.nodes["src/walker.py"]
