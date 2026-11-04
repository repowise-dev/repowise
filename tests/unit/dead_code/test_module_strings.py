"""A Python module named by a module-path string is used.

From repowise itself: ``cli/main.py`` loads ``"uninstall_cmd:uninstall_command"``
from ``cli/commands`` through ``import_module`` only when the command runs, and
``cli/ui/__init__.py`` maps names to ``"result_panels"`` for a lazy
``__getattr__``. Both modules were reported unreachable.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.analysis.dead_code.models import DeadCodeFindingData
from repowise.core.analysis.dead_code.module_strings import drop_named_modules
from tests.unit.dead_code._helpers import _build_graph

_MAIN = b"""from importlib import import_module

_LAZY = (
    ("uninstall", "uninstall_cmd:uninstall_command"),
)

def load(name):
    module, attr = dict(_LAZY)[name].split(":")
    return getattr(import_module(f"repowise.cli.commands.{module}"), attr)
"""


def _finding(kind: DeadCodeKind, path: str, name: str | None = None) -> DeadCodeFindingData:
    return DeadCodeFindingData(
        kind=kind,
        file_path=path,
        symbol_name=name,
        symbol_kind="function" if name else None,
        confidence=0.4,
        reason="test",
        last_commit_at=None,
        commit_count_90d=0,
        lines=1,
        evidence=[],
        safe_to_delete=False,
        primary_owner=None,
        age_days=None,
    )


def _kept(findings, source, importers=()):
    return {(f.file_path, f.symbol_name) for f in drop_named_modules(findings, source, importers)}


_CMD = "pkg/repowise/cli/commands/uninstall_cmd.py"


def test_lazy_table_entry_in_a_runtime_importer_names_module_and_attribute():
    findings = [
        _finding(DeadCodeKind.UNREACHABLE_FILE, _CMD),
        _finding(DeadCodeKind.UNUSED_EXPORT, _CMD, "uninstall_command"),
        _finding(DeadCodeKind.UNUSED_EXPORT, _CMD, "helper"),
    ]
    source = {"pkg/repowise/cli/main.py": _MAIN, _CMD: b"def uninstall_command(): ...\n"}
    kept = _kept(findings, source, {"pkg/repowise/cli/main.py"})
    assert kept == {(_CMD, "helper")}


def test_a_bare_name_outside_a_runtime_importer_is_not_read_as_a_module():
    findings = [_finding(DeadCodeKind.UNREACHABLE_FILE, _CMD)]
    source = {"pkg/repowise/cli/main.py": b'LABEL = "uninstall_cmd"\n'}
    assert _kept(findings, source) == {(_CMD, None)}


def test_a_bare_name_only_reaches_modules_under_the_importer():
    other = "pkg/repowise/server/uninstall_cmd.py"
    findings = [_finding(DeadCodeKind.UNREACHABLE_FILE, other)]
    assert _kept(findings, {"pkg/repowise/cli/main.py": _MAIN}, {"pkg/repowise/cli/main.py"}) == {
        (other, None)
    }


def test_a_dotted_entry_point_in_pyproject_names_the_module():
    findings = [_finding(DeadCodeKind.UNREACHABLE_FILE, "src/tool/cli.py")]
    source = {"pyproject.toml": b'[project.scripts]\ntool = "tool.cli:main"\n'}
    assert _kept(findings, source) == set()


def test_a_single_word_string_elsewhere_names_nothing():
    findings = [_finding(DeadCodeKind.UNREACHABLE_FILE, "src/tool/cli.py")]
    assert _kept(findings, {"README.md": b'run "cli" to start\n'}) == {("src/tool/cli.py", None)}


def test_the_analyzer_reads_a_string_map_in_a_lazy_package():
    init = b"""from importlib import import_module

_SUBMODULES = {"build_completion_panel": "result_panels"}

def __getattr__(name):
    return getattr(import_module(f"{__name__}.{_SUBMODULES[name]}"), name)
"""
    graph = _build_graph(
        {
            "cli/ui/__init__.py": {"symbols": []},
            "cli/ui/result_panels.py": {"symbols": []},
            "cli/ui/orphan.py": {"symbols": []},
        }
    )
    source = {
        "cli/ui/__init__.py": init,
        "cli/ui/result_panels.py": b"def build_completion_panel(): ...\n",
        "cli/ui/orphan.py": b"X = 1\n",
    }
    parsed = {path: SimpleNamespace(file_info=SimpleNamespace(abs_path=path)) for path in source}
    report = DeadCodeAnalyzer(graph, parsed_files=parsed, source_map=source).analyze(
        {"detect_unused_exports": False, "detect_zombie_packages": False, "min_confidence": 0.0}
    )
    files = {f.file_path for f in report.findings if f.kind is DeadCodeKind.UNREACHABLE_FILE}
    assert files == {"cli/ui/orphan.py"}


_BACKENDS = "hc/accounts/backends.py"
_BACKENDS_SRC = b"class EmailBackend:\n    pass\n\n\ndef helper():\n    pass\n"


def _backend_findings():
    return [
        _finding(DeadCodeKind.UNREACHABLE_FILE, _BACKENDS),
        _finding(DeadCodeKind.UNUSED_EXPORT, _BACKENDS, "EmailBackend"),
        _finding(DeadCodeKind.UNUSED_EXPORT, _BACKENDS, "helper"),
    ]


def test_a_django_setting_naming_a_class_uses_its_module_and_the_class():
    settings = b'AUTHENTICATION_BACKENDS = [\n    "hc.accounts.backends.EmailBackend",\n]\n'
    source = {"hc/settings.py": settings, _BACKENDS: _BACKENDS_SRC}
    assert _kept(_backend_findings(), source) == {(_BACKENDS, "helper")}


def test_a_colon_entry_names_the_module_and_the_attribute():
    redis = "celery/backends/redis.py"
    findings = [
        _finding(DeadCodeKind.UNREACHABLE_FILE, redis),
        _finding(DeadCodeKind.UNUSED_EXPORT, redis, "RedisBackend"),
    ]
    source = {
        "celery/app/backends.py": b"BACKEND_ALIASES = {'redis': 'celery.backends.redis:RedisBackend'}\n",
        redis: b"class RedisBackend: ...\n",
    }
    assert _kept(findings, source) == set()


def test_a_dotted_name_the_module_does_not_define_names_nothing():
    settings = b'MIDDLEWARE = ["hc.accounts.backends.Missing"]\n'
    source = {"hc/settings.py": settings, _BACKENDS: _BACKENDS_SRC}
    kept = _kept(_backend_findings(), source)
    assert kept == {(_BACKENDS, None), (_BACKENDS, "EmailBackend"), (_BACKENDS, "helper")}


def test_a_docstring_comment_or_test_patch_target_names_nothing():
    doc = b'"""Mirrors "hc.accounts.backends.EmailBackend"."""\n# see "hc.accounts.backends"\nX = 1\n'
    test = b'@patch("hc.accounts.backends.EmailBackend")\ndef test_login(): ...\n'
    toml = b'# tool = "hc.accounts.backends:EmailBackend"\n'
    source = {
        "hc/other.py": doc,
        "hc/tests/test_backends.py": test,
        "pyproject.toml": toml,
        _BACKENDS: _BACKENDS_SRC,
    }
    assert _kept(_backend_findings(), source) == {
        (_BACKENDS, None),
        (_BACKENDS, "EmailBackend"),
        (_BACKENDS, "helper"),
    }


def test_a_test_loading_a_fixture_app_by_module_path_uses_it():
    app = "t/unit/bin/proj/app.py"
    findings = [_finding(DeadCodeKind.UNREACHABLE_FILE, app)]
    source = {"t/unit/bin/test_control.py": b"OPTS = ['-A', 't.unit.bin.proj.app']\n", app: b"app = 1\n"}
    assert _kept(findings, source) == set()


def test_python_that_does_not_decode_falls_back_to_the_whole_file():
    settings = b'# -*- coding: utf-8 -*-\nB = "hc.accounts.backends.EmailBackend"\nX = "\xff"\n'
    source = {"hc/settings.py": settings, _BACKENDS: _BACKENDS_SRC}
    assert _kept(_backend_findings(), source) == {(_BACKENDS, "helper")}


_PAIR_MODULE = "pkg/_graph_utils.py"
_PAIR_SOURCE = b"def build_ctx(x):\n    return {'ctx': x}\n\ndef helper(): ...\n"
_PAIR_LOADER = b"""import importlib
_LAZY_ATTRS = {"_build_ctx": ("_graph_utils", "build_ctx")}
def __getattr__(name):
    module_name, attr = _LAZY_ATTRS[name]
    return getattr(importlib.import_module(f"{__name__}.{module_name}"), attr)
"""


@pytest.mark.parametrize("marker", [
    b"def __getattr__(name): ...\n",
    b"async def __getattr__(name): ...\n",
    b"if enabled:\n    def __getattr__(name): ...\n",
    b"import_module(name)\n",
])
@pytest.mark.parametrize("pair", [
    b'("_graph_utils", "build_ctx")',
    b'(\n "_graph_utils", # module\n "build_ctx",\n)',
])
def test_loader_pair_names_only_the_defined_member(marker, pair):
    findings = [_finding(DeadCodeKind.UNUSED_EXPORT, _PAIR_MODULE, name)
                for name in ("build_ctx", "helper")]
    source = {"pkg/__init__.py": marker + b"TABLE = {\"alias\": " + pair + b"}\n",
              _PAIR_MODULE: _PAIR_SOURCE}
    assert _kept(findings, source) == {(_PAIR_MODULE, "helper")}


@pytest.mark.parametrize("pair", [
    b'("_graph_utils", "missing")',
    b'("_graph_utils", "build_ctx", "extra")',
    b'("_graph_utils", "build_" "ctx")',
    b'["_graph_utils", "build_ctx"]',
    b'call("_graph_utils", "build_ctx")',
    b'callbacks[0]("_graph_utils", "build_ctx")',
    b'"""("_graph_utils", "build_ctx")"""',
    b'# ("_graph_utils", "build_ctx")',
])
def test_invalid_or_nonexecuting_pairs_do_not_name_an_export(pair):
    findings = [_finding(DeadCodeKind.UNUSED_EXPORT, _PAIR_MODULE, "build_ctx")]
    source = {"pkg/__init__.py": b"def __getattr__(name): ...\n" + pair + b"\n",
              _PAIR_MODULE: _PAIR_SOURCE}
    assert _kept(findings, source) == {(_PAIR_MODULE, "build_ctx")}


@pytest.mark.parametrize("path", ["pkg/table.py", "pkg/tests/test_loader.py"])
def test_nonloader_or_test_pairs_do_not_name_an_export(path):
    findings = [_finding(DeadCodeKind.UNUSED_EXPORT, _PAIR_MODULE, "build_ctx")]
    blob = _PAIR_LOADER if "tests/" in path else b'TABLE = ("pkg._graph_utils", "build_ctx")\n'
    assert _kept(findings, {path: blob, _PAIR_MODULE: _PAIR_SOURCE}) == {
        (_PAIR_MODULE, "build_ctx")
    }


@pytest.mark.parametrize("table", [
    b'{"_build_ctx": ("_graph_utils", "build_ctx")}',
    b'dict([("_build_ctx", ("_graph_utils", "build_ctx"))])',
])
def test_renamed_lazy_reexport_with_the_real_graph_builder(tmp_path, table):
    from repowise.core.pipeline.incremental import build_repo_graph

    source = {
        "pkg/__init__.py": _PAIR_LOADER.replace(
            b'{"_build_ctx": ("_graph_utils", "build_ctx")}', table
        ),
        _PAIR_MODULE: _PAIR_SOURCE,
        "app/main.py": b"from pkg import _build_ctx\n\ndef run():\n    return _build_ctx(1)\n\n"
                       b"if __name__ == '__main__':\n    run()\n",
    }
    for path, blob in source.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
    _, sources, builder, _, _ = build_repo_graph(
        tmp_path, [], collect_sources=True, read_only=True
    )
    report = DeadCodeAnalyzer(
        builder.graph(), {}, parsed_files=builder._parsed_files,
        source_map=sources, repo_root=tmp_path
    ).analyze(None)
    unused = {(f.file_path, f.symbol_name) for f in report.findings
              if f.kind is DeadCodeKind.UNUSED_EXPORT}
    assert (_PAIR_MODULE, "helper") in unused
    assert (_PAIR_MODULE, "build_ctx") not in unused


def test_pair_cannot_reach_a_bare_module_outside_the_loader_directory():
    other = "other/_graph_utils.py"
    findings = [_finding(DeadCodeKind.UNUSED_EXPORT, other, "build_ctx")]
    assert _kept(findings, {"pkg/__init__.py": _PAIR_LOADER, other: _PAIR_SOURCE}) == {
        (other, "build_ctx")
    }


@pytest.mark.parametrize("marker", [
    b'LABEL = "__getattr__"\n',
    b'def import_module(name): ...\n',
    b'class import_module(object): ...\n',
    b'class Proxy:\n    def __getattr__(self, name): ...\n',
    b'def factory():\n    def __getattr__(name): ...\n',
    b'class Proxy:\n    async def __getattr__(self, name): ...\n',
])
def test_a_loader_marker_that_is_not_a_dynamic_load_does_not_enable_pairing(marker):
    finding = _finding(DeadCodeKind.UNUSED_EXPORT, _PAIR_MODULE, "build_ctx")
    source = {
        "pkg/__init__.py": marker + b'TABLE = ("_graph_utils", "build_ctx")\n',
        _PAIR_MODULE: _PAIR_SOURCE,
    }
    assert drop_named_modules([finding], source, ()) == [finding]
    assert not finding.safe_to_delete
