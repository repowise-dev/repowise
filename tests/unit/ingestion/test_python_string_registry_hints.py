"""Modules named by string in lazy registries become ``dynamic_uses`` edges.

Lazy command tables, entry-point specs and templated ``import_module`` calls
load a module by name, so no import edge exists. Each test pins one shape and
one false-edge guard: a string must resolve to a module in the repo, and an
attribute reference must name something that module has.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion.dynamic_hints.base import DynamicEdge
from repowise.core.ingestion.dynamic_hints.python_imports import (
    ModuleStringResolver,
    PythonDynamicHints,
    python_dynamic_refs,
)
from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.languages.python_modules import build_python_module_index


def _refs(files: dict[str, str], rel: str) -> dict[str, tuple[str, ...]]:
    blobs = {path: src.encode() for path, src in files.items()}
    resolver = ModuleStringResolver(
        build_python_module_index(blobs), lambda path: blobs.get(path, b"")
    )
    return python_dynamic_refs(rel, blobs[rel], resolver)


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


def test_templated_import_in_a_comment_or_docstring_adds_nothing() -> None:
    files = {
        "pkg/__init__.py": "",
        "pkg/plugins/__init__.py": "",
        "pkg/plugins/a.py": "",
        "pkg/loader.py": (
            '"""Loads plugins with import_module(f"pkg.plugins.{name}")."""\n'
            "import importlib\n"
            "def load(name):\n"
            '    # importlib.import_module(f"pkg.plugins.{name}")\n'
            "    return name\n"
        ),
    }
    assert _refs(files, "pkg/loader.py") == {}


def test_wide_or_one_segment_template_links_the_package_only() -> None:
    plugins = {f"pkg/plugins/p{i}.py": "" for i in range(51)}
    files = {
        "pkg/__init__.py": "",
        "pkg/plugins/__init__.py": "",
        **plugins,
        "pkg/loader.py": (
            "import importlib\n"
            "def load(name):\n"
            '    importlib.import_module(f"pkg.plugins.{name}")\n'
            '    importlib.import_module(f"pkg.{name}")\n'
        ),
    }
    assert _refs(files, "pkg/loader.py") == {
        "pkg/plugins/__init__.py": (),
        "pkg/__init__.py": (),
    }


_SECTIONS = {
    "src/app/__init__.py": "",
    "src/app/sections/a.py": "",
    "src/app/sections/b.py": "",
    "src/app/sections/sub/__init__.py": "",
    "src/app/sections/sub/deep.py": "",
    "src/app/other.py": "",
}
_SECTIONS_PKG = "src/app/sections/__init__.py"
_CHILDREN = {
    "src/app/sections/a.py": (),
    "src/app/sections/b.py": (),
    "src/app/sections/sub/__init__.py": (),
}


def _sections(init: str, **extra: str) -> dict[str, str]:
    return {**_SECTIONS, _SECTIONS_PKG: "import importlib\nimport pkgutil\n" + init, **extra}


def test_package_importing_its_own_path_listing_reaches_every_child() -> None:
    for load in (
        'importlib.import_module(f"{__name__}.{_m.name}")',
        'importlib.import_module(__name__ + "." + _m.name)',
        'importlib.import_module(f".{_m.name}", package=__name__)',
        'importlib.import_module("." + _m.name, __name__)',
        'importlib.import_module(f"{__package__}.{_m.name}")',
    ):
        files = _sections(f"for _m in pkgutil.iter_modules(__path__):\n    {load}\n")
        assert _refs(files, _SECTIONS_PKG) == _CHILDREN, load


def test_walk_packages_with_name_prefix_reaches_every_descendant() -> None:
    files = _sections(
        'for _m in pkgutil.walk_packages(__path__, __name__ + "."):\n'
        "    importlib.import_module(_m.name)\n"
    )
    assert _refs(files, _SECTIONS_PKG) == {**_CHILDREN, "src/app/sections/sub/deep.py": ()}


def test_own_path_listing_is_not_capped() -> None:
    many = {f"src/app/sections/p{i}.py": "" for i in range(60)}
    files = _sections(
        "for _m in pkgutil.iter_modules(__path__):\n"
        '    importlib.import_module(f"{__name__}.{_m.name}")\n',
        **many,
    )
    assert set(many) <= set(_refs(files, _SECTIONS_PKG))


def test_listing_loop_that_skips_private_names_does_not_link_them() -> None:
    private = {"src/app/sections/_helpers.py": "", "src/app/sections/sub/_impl.py": ""}
    skipping = _sections(
        "for _m in pkgutil.iter_modules(__path__):\n"
        '    if not _m.name.startswith("_"):\n'
        '        importlib.import_module(f".{_m.name}", __package__)\n',
        **private,
    )
    assert _refs(skipping, _SECTIONS_PKG) == _CHILDREN
    # With no such filter the loop loads them too.
    loading_all = _sections(
        "for _m in pkgutil.iter_modules(__path__):\n"
        '    importlib.import_module(f"{__name__}.{_m.name}")\n',
        **private,
    )
    assert _refs(loading_all, _SECTIONS_PKG) == {**_CHILDREN, "src/app/sections/_helpers.py": ()}


def test_a_load_outside_the_listing_loop_links_nothing() -> None:
    for init in (
        # The listing feeds a name list; a separate loop loads its own names.
        "NAMES = [m.name for m in pkgutil.iter_modules(__path__)]\n"
        'for n in ["a"]:\n'
        '    importlib.import_module(f"{__name__}.{n}")\n',
        # A lazy table in the same file as a listing.
        "NAMES = [m.name for m in pkgutil.iter_modules(__path__)]\n"
        '_LAZY = {"A": "a"}\n'
        "def __getattr__(name):\n"
        '    return importlib.import_module(f"{__name__}.{_LAZY[name]}")\n',
        # Inside the loop, but not the loop's name.
        "for _m in pkgutil.iter_modules(__path__):\n"
        '    importlib.import_module(f"{__name__}.{OTHER}")\n',
    ):
        assert _refs(_sections(init), _SECTIONS_PKG) == {}, init


def test_a_name_guard_links_only_the_names_it_passes() -> None:
    files = _sections(
        "for _m in pkgutil.iter_modules(__path__):\n"
        '    if _m.name == "a":\n'
        '        importlib.import_module(f"{__name__}.{_m.name}")\n'
    )
    assert _refs(files, _SECTIONS_PKG) == {"src/app/sections/a.py": ()}


def test_an_unrelated_import_beside_a_full_name_listing_links_nothing() -> None:
    files = _sections(
        'for _m in pkgutil.walk_packages(__path__, __name__ + "."):\n'
        "    print(_m.name)\n"
        'importlib.import_module("json")\n'
    )
    assert _refs(files, _SECTIONS_PKG) == {}


_EXTRA = {"src/app/sections/_helpers.py": "", "src/app/sections/test_a.py": ""}
_LOOP = "for _m in pkgutil.iter_modules(__path__):\n"
_LOAD = 'importlib.import_module(f"{__name__}.{_m.name}")\n'
_ALL = {**_CHILDREN, **{path: () for path in _EXTRA}}


def test_skip_filters_are_read_from_the_loop_body() -> None:
    for body, expected in (
        (f'    if _m.name[0] != "_":\n        {_LOAD}', {
            k: v for k, v in _ALL.items() if "/_helpers" not in k
        }),
        (f'    if _m.name.startswith("__"):\n        continue\n    {_LOAD}', _ALL),
        (f'    if not _m.name.startswith("test"):\n        {_LOAD}', {
            k: v for k, v in _ALL.items() if "/test_a" not in k
        }),
        (f"    if _m.ispkg:\n        {_LOAD}", {"src/app/sections/sub/__init__.py": ()}),
        # A filter the reader cannot evaluate links nothing from that load.
        (f"    if _m.name not in SKIP:\n        {_LOAD}", {}),
        # A skip in a comment or string is not a filter.
        (f'    # if _m.name.startswith("_"): continue\n    "startswith(\'_\')"\n    {_LOAD}', _ALL),
    ):
        assert _refs(_sections(_LOOP + body, **_EXTRA), _SECTIONS_PKG) == expected, body


def test_a_filtered_comprehension_listing_links_what_passes() -> None:
    files = _sections(
        "for name in sorted(m.name for m in pkgutil.iter_modules(__path__) if m.name != 'b'):\n"
        "    importlib.import_module(f'.{name}', __package__)\n"
    )
    assert _refs(files, _SECTIONS_PKG) == {
        "src/app/sections/a.py": (),
        "src/app/sections/sub/__init__.py": (),
    }


def test_relative_load_anchored_on_something_else_links_nothing() -> None:
    files = _sections(
        _LOOP + "    importlib.import_module(f'.{_m.name}', __name__.rpartition('.')[0])\n"
    )
    assert _refs(files, _SECTIONS_PKG) == {}


def test_walk_packages_without_a_prefix_yields_children_only() -> None:
    # Recursing imports ``sub`` by its bare name, which fails, so
    # ``sub.deep`` is never listed.
    files = _sections(
        "for _m in pkgutil.walk_packages(__path__):\n"
        '    importlib.import_module(f"{__name__}.{_m.name}")\n'
    )
    assert _refs(files, _SECTIONS_PKG) == _CHILDREN


def test_listing_some_other_path_keeps_the_cap() -> None:
    many = {f"src/app/sections/p{i}.py": "" for i in range(60)}
    files = _sections(
        "for _m in pkgutil.iter_modules([PLUGIN_DIR]):\n"
        '    importlib.import_module(f"{__name__}.{_m.name}")\n',
        **many,
    )
    # Over the cap with no listing to vouch for it: the package only, which
    # is this file, so nothing.
    assert _refs(files, _SECTIONS_PKG) == {}


def test_own_path_listing_without_an_import_adds_nothing() -> None:
    files = _sections(
        "NAMES = [m.name for m in pkgutil.iter_modules(__path__)]\n"
        'NAMED = list(pkgutil.walk_packages(__path__, __name__ + "."))\n'
    )
    assert _refs(files, _SECTIONS_PKG) == {}


def test_own_path_listing_in_a_comment_adds_nothing() -> None:
    files = _sections(
        "# for _m in pkgutil.walk_packages(__path__, __name__ + '.'):\n"
        "importlib.import_module(NAME)\n"
    )
    assert _refs(files, _SECTIONS_PKG) == {}


def test_lazy_getattr_table_on_the_own_name_adds_nothing() -> None:
    # Spells the same load but reaches only the modules its table names.
    files = _sections(
        '_LAZY = {"A": "a"}\n'
        "def __getattr__(name):\n"
        '    return getattr(importlib.import_module(f"{__name__}.{_LAZY[name]}"), name)\n'
    )
    assert _refs(files, _SECTIONS_PKG) == {}


def test_own_name_template_in_a_plain_module_adds_nothing() -> None:
    loader = "src/app/sections/loader.py"
    files = _sections(
        "",
        **{
            loader: (
                "import importlib\n"
                "def load(name):\n"
                '    importlib.import_module(f"{__name__}.{name}")\n'
                '    importlib.import_module(f"{__package__}.{name}")\n'
            )
        },
    )
    assert _refs(files, loader) == {}


def test_relative_template_without_an_anchor_adds_nothing() -> None:
    files = _sections('importlib.import_module(f".{NAME}")\n')
    assert _refs(files, _SECTIONS_PKG) == {}


def _hint(source: str, target: str, names: tuple[str, ...] = ()) -> DynamicEdge:
    return DynamicEdge(source, target, "dynamic_uses", "python_dynamic_import", imported_names=names)


def _builder_with_import(names: list[str]) -> GraphBuilder:
    builder = GraphBuilder()
    graph = builder._graph
    graph.add_node("a.py")
    graph.add_node("b.py")
    graph.add_edge("a.py", "b.py", edge_type="imports", imported_names=names)
    return builder


def test_hint_on_an_imported_pair_keeps_the_import_and_unions_names() -> None:
    builder = _builder_with_import(["Foo"])
    builder.add_dynamic_edges([_hint("a.py", "b.py", ("app",))])
    data = builder._graph.get_edge_data("a.py", "b.py")
    assert data["edge_type"] == "imports"
    assert data["imported_names"] == ["Foo", "app"]
    assert "dynamic_hint" not in data


def test_whole_module_hint_on_an_imported_pair_is_kept_as_a_flag() -> None:
    builder = _builder_with_import(["Foo"])
    builder.add_dynamic_edges([_hint("a.py", "b.py")])
    data = builder._graph.get_edge_data("a.py", "b.py")
    assert (data["edge_type"], data["imported_names"]) == ("imports", ["Foo"])
    assert data["dynamic_hint"] == "python_dynamic_import"


def test_second_hint_on_a_pair_merges_and_whole_module_wins() -> None:
    builder = GraphBuilder()
    builder._graph.add_node("a.py")
    builder.add_dynamic_edges([_hint("a.py", "b.py", ("x",)), _hint("a.py", "b.py", ("y",))])
    assert builder._graph.get_edge_data("a.py", "b.py")["imported_names"] == ["x", "y"]
    builder.add_dynamic_edges([_hint("a.py", "b.py")])
    assert "imported_names" not in builder._graph.get_edge_data("a.py", "b.py")
