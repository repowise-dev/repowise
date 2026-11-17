"""Which modules a Python test replaces outright, from its text, and the reach state it leaves."""

from __future__ import annotations

import networkx as nx
import pytest

from repowise.core.analysis.test_reach import (
    build_walk_maps,
    reach_states,
    replaced_files,
    replaced_modules,
)

_T = "tests/test_x.py"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('from unittest.mock import patch\n@patch("app.helpers")\ndef test(m): ...\n', {"app.helpers"}),
        ('from unittest import mock\nwith mock.patch("app.helpers"): ...\n', {"app.helpers"}),
        ('import unittest.mock\nunittest.mock.patch("app.helpers")\n', {"app.helpers"}),
        ('from app import pkg\nfrom unittest.mock import patch\npatch.object(pkg, "helpers")\n', {"app.pkg.helpers"}),
        ('import app.pkg as p\nfrom unittest import mock\nmock.patch.object(p, "helpers")\n', {"app.pkg.helpers"}),
        ('def test(monkeypatch):\n    monkeypatch.setattr("app.helpers", None)\n', {"app.helpers"}),
        ('import app\ndef test(monkeypatch):\n    monkeypatch.setattr(app, "helpers", None)\n', {"app.helpers"}),
        ('import sys\nfrom unittest.mock import patch\npatch.dict(sys.modules, {"app.helpers": None})\n', {"app.helpers"}),
        ('import sys\ndef test(monkeypatch):\n    monkeypatch.setitem(sys.modules, "app.helpers", None)\n', {"app.helpers"}),
    ],
)
def test_each_patch_form_names_the_binding_it_replaces(source: str, expected: set[str]) -> None:
    assert replaced_modules(_T, source) == expected


@pytest.mark.parametrize(
    "source",
    [
        # The original stays reachable through the double, or may.
        'from unittest.mock import patch\npatch("app.helpers", wraps=object())\n',
        'from unittest.mock import patch\npatch("app.helpers", side_effect=lambda: 1)\n',
        'from unittest.mock import patch\npatch("app.helpers", autospec=True)\n',
        # Targets built at run time.
        'from unittest.mock import patch\nname = "app"\npatch(f"{name}.helpers")\n',
        'from unittest.mock import patch\npatch("app" + ".helpers")\n',
        'from unittest.mock import patch\nT = "app.helpers"\npatch(T)\n',
        # An owner the file does not bind once by an absolute import.
        'def test(monkeypatch, mod):\n    monkeypatch.setattr(mod, "helpers", None)\n',
        'from . import pkg\nfrom unittest.mock import patch\npatch.object(pkg, "helpers")\n',
        'import app\napp = object()\nfrom unittest.mock import patch\npatch.object(app, "helpers")\n',
        # Not a patch, not ``sys.modules``, not a fixture this lexicon knows.
        'def test(client):\n    client.patch("/app.helpers")\n',
        'def test(monkeypatch):\n    monkeypatch.setitem(CONFIG, "app.helpers", None)\n',
        'def test(mp):\n    mp.setattr("app.helpers", None)\n',
        'import sys\nfrom unittest.mock import patch\nkey = "a"\npatch.dict(sys.modules, {key: None})\n',
        # A ``patch`` the file does not import from the mock library.
        'def patch(x): ...\npatch("app.helpers")\n',
        'from requests import patch\npatch("app.helpers")\n',
        'import app\nfrom unittest.mock import patch\ndef app(): ...\npatch.object(app, "helpers")\n',
        # The file imports the module itself, so another test in it may run it.
        'import app.helpers\nfrom unittest.mock import patch\npatch("app.helpers")\n',
        'from app.helpers import run\nfrom unittest.mock import patch\npatch("app.helpers")\n',
        # Unparseable.
        'from unittest.mock import patch\npatch("app.helpers"\n',
    ],
)
def test_anything_unproven_replaces_nothing(source: str) -> None:
    assert replaced_modules(_T, source) == frozenset()


def test_only_python_test_files_are_read() -> None:
    assert replaced_modules("tests/x.test.ts", 'patch("app.helpers")') == frozenset()
    assert replaced_modules(_T, b'from unittest.mock import patch\npatch("app.helpers")\n') == {
        "app.helpers"
    }


def test_a_patch_masks_a_file_only_when_its_binding_is_that_module() -> None:
    files = {"app/__init__.py", "app/helpers.py", "app/core.py", _T}
    texts = {
        # A function inside core.py: the test still runs the rest of core.py.
        _T: 'from unittest.mock import patch\npatch("app.core.run")\npatch("app.helpers")\n',
        "tests/test_y.py": 'from unittest.mock import patch\npatch("app.core.run")\n',
        "tests/test_z.py": 'from unittest.mock import patch\npatch("other.lib")\n',
    }
    assert replaced_files(texts, files) == {_T: frozenset({"app/helpers.py"})}


# ---------------------------------------------------------------------------
# The ``mocked_only`` state
# ---------------------------------------------------------------------------


def _graph(tests: dict[str, str], *, imports: tuple[tuple[str, str], ...] = ()) -> nx.MultiDiGraph:
    """Each test calls ``run`` in the file it maps to; *imports* adds file edges."""
    g = nx.MultiDiGraph()
    for path in {*tests, *tests.values(), "src/types.py", *(p for e in imports for p in e)}:
        g.add_node(path, node_type="file", is_test=path.startswith("tests/"))
    for test, source in tests.items():
        g.add_edge(test, f"{test}::test_it", edge_type="defines")
        g.add_edge(source, f"{source}::run", edge_type="defines")
        g.add_edge(f"{test}::test_it", f"{source}::run", edge_type="calls")
    for src, dst in imports:
        g.add_edge(src, dst, edge_type="imports")
    return g


def test_a_file_only_replacing_tests_reach_is_mocked_only_not_unreached() -> None:
    maps = build_walk_maps(_graph({"tests/test_a.py": "src/a.py", "tests/test_b.py": "src/b.py"}))
    masks = {"tests/test_a.py": frozenset({"src/a.py"})}
    rows = reach_states(maps, ["src/a.py", "src/b.py"], masks=masks)
    assert rows["src/a.py"].state == "mocked_only"
    assert rows["src/a.py"].reached is None
    assert rows["src/a.py"].mocked_by == ("tests/test_a.py",)
    assert rows["src/b.py"].state == "reached"
    # Without masks the same walk reads both as reached.
    assert reach_states(maps, ["src/a.py"])["src/a.py"].state == "reached"


def test_a_masked_tier_falls_through_to_a_test_that_runs_the_file() -> None:
    maps = build_walk_maps(
        _graph({"tests/test_a.py": "src/a.py"}, imports=(("tests/test_c.py", "src/a.py"),))
    )
    rows = reach_states(maps, ["src/a.py"], masks={"tests/test_a.py": {"src/a.py"}})
    assert rows["src/a.py"].state == "reached"
    assert rows["src/a.py"].reached.tests == ["tests/test_c.py"]
    assert rows["src/a.py"].reached.via == "import-graph"


def test_a_replacing_test_is_dropped_from_a_tier_another_test_answers() -> None:
    g = _graph({"tests/test_a.py": "src/a.py", "tests/test_b.py": "src/a.py"})
    rows = reach_states(build_walk_maps(g), ["src/a.py"], masks={"tests/test_a.py": {"src/a.py"}})
    reached = rows["src/a.py"].reached
    assert rows["src/a.py"].state == "reached"
    assert (reached.tests, reached.total, reached.via) == (["tests/test_b.py"], 1, "call-graph")
    assert set(reached.reach) == {"tests/test_b.py"}


def test_type_only_outranks_mocked_only_and_a_replacing_changed_test_is_no_evidence() -> None:
    maps = build_walk_maps(_graph({"tests/test_a.py": "src/a.py", "tests/test_t.py": "src/types.py"}))
    masks = {"tests/test_a.py": {"src/a.py"}, "tests/test_t.py": {"src/types.py"}}
    rows = reach_states(
        maps,
        ["src/a.py", "src/types.py"],
        masks=masks,
        changed_tests=["tests/test_a.py"],
        contents={"src/types.py": "from typing import Protocol\nclass P(Protocol): ...\n"},
    )
    assert rows["src/types.py"].state == "type_only"
    assert rows["src/a.py"].state == "mocked_only"
    assert rows["src/a.py"].mocked_by == ("tests/test_a.py",)
