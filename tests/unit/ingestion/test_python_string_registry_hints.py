"""Modules named by string in lazy registries become ``dynamic_uses`` edges.

Lazy command tables, entry-point specs and templated ``import_module`` calls
load a module by name, so no import edge exists. Each test pins one shape and
one false-edge guard: a string must resolve to a module in the repo, and an
attribute reference must name something that module has.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion.dynamic_hints.python_imports import (
    PythonDynamicHints,
    python_dynamic_refs,
)
from repowise.core.ingestion.languages.python_modules import build_python_module_index


def _refs(files: dict[str, str], rel: str) -> dict[str, tuple[str, ...]]:
    blobs = {path: src.encode() for path, src in files.items()}
    index = build_python_module_index(blobs)
    return python_dynamic_refs(rel, blobs[rel], index, lambda path: blobs.get(path, b""))


_CLI = {
    "src/app/__init__.py": "",
    "src/app/commands/__init__.py": "",
    "src/app/commands/status_cmd.py": "def status_command():\n    pass\n",
    "src/app/commands/init_cmd/__init__.py": "from .command import init_command\n",
    "src/app/commands/init_cmd/command.py": "def init_command():\n    pass\n",
}


def test_relative_lazy_command_table_resolves_under_the_named_package() -> None:
    files = {
        **_CLI,
        "src/app/main.py": (
            '_PKG = "app.commands"\n'
            "_COMMANDS = (\n"
            '    ("status", "status_cmd:status_command"),\n'
            '    ("init", "init_cmd:init_command"),\n'
            '    ("gone", "missing_cmd:gone_command"),\n'
            ")\n"
            "for name, target in _COMMANDS:\n"
            '    register(name, f"{_PKG}.{target}")\n'
        ),
    }
    assert _refs(files, "src/app/main.py") == {
        "src/app/commands/status_cmd.py": ("status_command",),
        # A package re-exporting the command counts as having it.
        "src/app/commands/init_cmd/__init__.py": ("init_command",),
    }


def test_absolute_entry_point_string_needs_no_loader_marker() -> None:
    files = {**_CLI, "src/app/serve.py": 'run("app.commands.status_cmd:status_command")\n'}
    assert _refs(files, "src/app/serve.py") == {
        "src/app/commands/status_cmd.py": ("status_command",)
    }


def test_colon_string_naming_an_attribute_the_module_lacks_adds_nothing() -> None:
    # An event key that happens to share its prefix with a package name.
    files = {
        "agent/__init__.py": "def run():\n    pass\n",
        "gateway.py": 'emit("agent:start")\nemit("agent:run")\n',
    }
    assert _refs(files, "gateway.py") == {"agent/__init__.py": ("run",)}


def test_module_with_lazy_getattr_serves_any_attribute() -> None:
    files = {
        "agent/__init__.py": "def __getattr__(name):\n    return name\n",
        "gateway.py": 'load("agent:start")\n',
    }
    assert _refs(files, "gateway.py") == {"agent/__init__.py": ("start",)}


def test_string_naming_no_repo_module_adds_nothing() -> None:
    files = {**_CLI, "src/app/serve.py": 'run("uvicorn.main:run")\nopen("w:gz")\n'}
    assert _refs(files, "src/app/serve.py") == {}


def test_strings_in_comments_and_docstrings_add_nothing() -> None:
    files = {
        **_CLI,
        "src/app/doc.py": (
            '"""Run ``"app.commands.status_cmd:status_command"`` to see it."""\n'
            "import importlib\n"
            '# load("app.commands.status_cmd:status_command")\n'
            "def f():\n"
            '    """See "app.commands.status_cmd" for details."""\n'
        ),
    }
    assert _refs(files, "src/app/doc.py") == {}


def test_dotted_member_path_in_a_loader_file_names_the_member() -> None:
    files = {
        **_CLI,
        "src/app/loader.py": (
            "from importlib import import_module\n"
            'BACKENDS = ["app.commands.status_cmd.status_command", "app.commands.init_cmd"]\n'
        ),
    }
    assert _refs(files, "src/app/loader.py") == {
        "src/app/commands/status_cmd.py": ("status_command",),
        "src/app/commands/init_cmd/__init__.py": (),
    }


def test_dotted_member_path_in_a_test_is_a_patch_target_not_a_load() -> None:
    files = {
        **_CLI,
        "tests/test_status.py": (
            "import importlib\n"
            'patch("app.commands.status_cmd.status_command")\n'
        ),
    }
    assert _refs(files, "tests/test_status.py") == {}


def test_lazy_group_keyword_gates_dotted_module_attr_strings() -> None:
    files = {
        **_CLI,
        "src/app/cli.py": (
            "@click.group(cls=LazyGroup, lazy_subcommands="
            '{"status": "app.commands.status_cmd.status_command"})\n'
            "def cli():\n    pass\n"
        ),
    }
    assert _refs(files, "src/app/cli.py") == {
        "src/app/commands/status_cmd.py": ("status_command",)
    }


def test_templated_import_reaches_every_matching_module() -> None:
    files = {
        "pkg/__init__.py": "",
        "pkg/plugins/__init__.py": "",
        "pkg/plugins/a.py": "",
        "pkg/plugins/b/__init__.py": "",
        "pkg/plugins/b/oauth.py": "",
        "pkg/other.py": "",
        "pkg/loader.py": (
            "import importlib\n"
            "def load(name):\n"
            '    importlib.import_module(f"pkg.plugins.{name}")\n'
            '    importlib.import_module(f"pkg.plugins.{name}.oauth")\n'
            '    importlib.import_module("pkg.plugins." + name)\n'
            '    importlib.import_module(f"{name}.{name}")\n'
        ),
    }
    assert _refs(files, "pkg/loader.py") == {
        "pkg/plugins/a.py": (),
        "pkg/plugins/b/__init__.py": (),
        "pkg/plugins/b/oauth.py": (),
    }


def test_extractor_reads_attached_source_map_before_disk(tmp_path: Path) -> None:
    for path, src in _CLI.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(src)
    main = tmp_path / "src/app/main.py"
    main.write_text("")  # on disk: nothing; in the source map: the table
    hints = PythonDynamicHints()
    hints._source_map = {"src/app/main.py": b'P = "app.commands"\nT = "status_cmd:status_command"\n'}
    edges = hints.extract(tmp_path)
    assert [(e.source, e.target, e.edge_type, e.imported_names) for e in edges] == [
        (
            "src/app/main.py",
            "src/app/commands/status_cmd.py",
            "dynamic_uses",
            ("status_command",),
        )
    ]
