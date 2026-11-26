"""Import time as rows: a Python module body's time is the row ``<path>::<module>``."""

from __future__ import annotations

import json
import marshal

import pytest

from repowise.core.evidence import parse_evidence
from repowise.core.evidence.format_v1 import is_import_row
from repowise.core.evidence.ingest import IndexedSymbol, join_entries, symbol_lookup
from repowise.core.evidence.next_step import next_step
from repowise.core.evidence.profile_convert import convert_profile, package_name
from repowise.core.evidence.profile_formats import profile_formats
from tests.unit.evidence.test_profile_formats import ROOT, SYMBOLS, _f

_SITE = "C:\\venv\\Lib\\site-packages"
# Indexed files with no symbols: only a top level.
_INDEXED = {**SYMBOLS, "pkg/db.py": [], "pkg/other.py": []}

FRAMES = [
    {"name": "<module>", "file": "C:\\venv\\Scripts\\app.py", "line": 1},  # 0 entry script
    {"name": "<module>", "file": _f("pkg\\app.py"), "line": 1},  # 1
    {"name": "_find_and_load", "file": "<frozen importlib._bootstrap>", "line": 1},  # 2
    {"name": "<module>", "file": _f("pkg\\db.py"), "line": 1},  # 3
    {"name": "<module>", "file": f"{_SITE}\\sqlalchemy\\__init__.py", "line": 1},  # 4
    {"name": "Mapper.configure", "file": f"{_SITE}\\sqlalchemy\\orm\\mapper.py", "line": 9},  # 5
    {"name": "get_code", "file": "<frozen importlib._bootstrap_external>", "line": 1},  # 6
    {"name": "<module>", "file": "C:\\Python\\Lib\\json\\__init__.py", "line": 1},  # 7
    {"name": "App.run", "file": _f("pkg\\app.py"), "line": 10},  # 8
    {"name": "<module>", "file": _f("pkg\\other.py"), "line": 1},  # 9
    {"name": "loads", "file": "C:\\Python\\Lib\\json\\decoder.py", "line": 3},  # 10
]
SAMPLES = [
    ([0, 1, 2, 3, 2, 4, 5], 6),  # app imports db, which imports sqlalchemy
    ([0, 1, 2, 3], 2),  # db's own top-level code
    ([0, 1, 2, 6], 1),  # the machinery loading db's code
    ([0, 1, 2, 7], 1),  # app imports json
    ([0, 1, 8, 2, 9], 3),  # App.run imports other lazily: an import of its own
    ([0, 1, 8, 2, 10], 2),  # App.run imports a library itself: its own cost
    ([0, 1, 8], 5),  # App.run's own code
]


def _convert(frames=FRAMES, samples=SAMPLES):
    doc = {
        "$schema": "https://www.speedscope.app/file-format-schema.json",
        "exporter": "py-spy@0.4.3",
        "shared": {"frames": frames},
        "profiles": [
            {
                "type": "sampled",
                "unit": "seconds",
                "samples": [s for s, _ in samples],
                "weights": [w for _, w in samples],
            }
        ],
    }
    run = profile_formats.get("speedscope").read(json.dumps(doc).encode())
    return convert_profile(run, root=ROOT, symbols_by_file=_INDEXED, value_kind="wall")


def _entries(conv) -> dict[tuple[str, str], dict]:
    return {(e["path"], e["function"]): e for e in conv.document["symbols"]}


def test_an_import_and_the_repo_modules_it_imports_are_one_row_of_self_time() -> None:
    conv = _convert()
    entries = _entries(conv)
    app = entries[("pkg/app.py", "<module>")]
    # db.py is imported by app.py with only import machinery between: one import.
    assert ("pkg/db.py", "<module>") not in entries
    assert "line" not in app
    assert app["self"] == 10 and app["total"] == 20
    # Libraries by top-level package (the standard library too), never by frame.
    assert app["inside"] == [
        {"name": "pkg.app.App.run", "kind": "repo", "share": 0.5},
        {"name": "sqlalchemy", "kind": "library", "share": 0.3},
        {"name": "(own code)", "kind": "own", "share": 0.1},
    ]
    # A module a function imports starts its own row, driven by that function.
    other = entries[("pkg/other.py", "<module>")]
    assert other["self"] == 3
    assert other["drivers"] == [
        {"function": "pkg.app.App.run", "path": "pkg/app.py", "line": 3, "share": 1.0}
    ]
    # A library a function imports stays that function's cost.
    run = entries[("pkg/app.py", "pkg.app.App.run")]
    assert run["self"] == 7
    assert ("_find_and_load", "import", 0.5) in [
        (c["name"], c["kind"], c["share"]) for c in run["inside"]
    ]
    # Import time is now charged to rows; the run split is unchanged.
    assert conv.self_unattributed == {}
    assert conv.repo_self == 7


def test_import_rows_rank_with_functions_by_self_time() -> None:
    order = [(e["path"], e["function"]) for e in _convert().document["symbols"]]
    assert order == [
        ("pkg/app.py", "<module>"),
        ("pkg/app.py", "pkg.app.App.run"),
        ("pkg/other.py", "<module>"),
    ]


def test_the_rest_of_an_import_is_the_machinery_and_own_code() -> None:
    conv = _convert(samples=[([0, 1, 2, 3], 1), ([0, 1, 2, 6], 3), ([0, 1, 2, 7], 1)])
    inside = _entries(conv)[("pkg/app.py", "<module>")]["inside"]
    assert inside == [
        {"name": "_find_and_load", "kind": "import", "share": 0.6},
        {"name": "(own code)", "kind": "own", "share": 0.2},
        {"name": "json", "kind": "library", "share": 0.2},
    ]


def test_a_module_body_outside_python_still_charges_no_row() -> None:
    frames = [{"name": "main.init", "file": _f("pkg\\app.go"), "line": 3}]
    symbols = {"pkg/app.go": []}
    doc = {
        "$schema": "https://www.speedscope.app/file-format-schema.json",
        "shared": {"frames": frames},
        "profiles": [{"type": "sampled", "unit": "none", "samples": [[0]], "weights": [4]}],
    }
    run = profile_formats.get("speedscope").read(json.dumps(doc).encode())
    conv = convert_profile(run, root=ROOT, symbols_by_file=symbols, value_kind="cpu")
    assert conv.document["symbols"] == []
    assert conv.self_unattributed == {"module_body": 4.0}


def test_package_name_is_the_top_level_import_name() -> None:
    assert package_name(f"{_SITE}\\sqlalchemy\\orm\\__init__.py") == "sqlalchemy"
    assert package_name("/usr/lib/python3/dist-packages/yaml/loader.py") == "yaml"
    assert package_name("C:\\Python\\Lib\\json\\decoder.py") == "json"
    assert package_name("/usr/lib/python3.12/typing.py") == "typing"
    assert package_name("/usr/lib64/python3.12/json/decoder.py") == "json"
    assert package_name("<frozen posixpath>") == "posixpath"
    assert package_name("/opt/tools/helpers/__init__.py") == "helpers"
    assert package_name("/opt/tools/six.py") == "six"


def test_an_import_row_joins_to_its_reserved_id_when_the_file_is_indexed() -> None:
    doc = parse_evidence(
        json.dumps(
            {
                "format": "repowise-evidence-v1",
                "kind": "profile",
                "value_kind": "wall",
                "unit": "seconds",
                "workload": {"name": "cli", "class": "entry_point"},
                "symbols": [
                    {"path": "src/app.py", "function": "<module>", "self": 3, "total": 4},
                    {"path": "src/gone.py", "function": "<module>", "self": 1, "total": 1},
                ],
            }
        )
    )
    by_file = {"src/app.py": symbol_lookup([IndexedSymbol("src/app.py::f", "f", "f", 3)])}
    joined = join_entries(doc, by_file, {"src/app.py": "production"}, frozenset())
    assert [(r["symbol_id"], r["line"]) for r in joined.rows] == [("src/app.py::<module>", None)]
    assert joined.skipped == {"unknown_file": 1}
    assert is_import_row("src/app.py::<module>") and not is_import_row("src/app.py::f")


def test_an_import_row_takes_no_next_step() -> None:
    row = {
        "symbol_id": "src/app.py::<module>",
        "inside": [{"name": "_find_and_load", "kind": "import", "share": 0.9}],
        "drivers": [],
        "workload_name": "app status",
        "workload_class": "entry_point",
    }
    assert next_step(row, "python") is None
    # The same callees on a function still say what to do.
    assert next_step({**row, "symbol_id": "src/app.py::load"}, "python").rule == "import"


def test_a_cprofile_import_is_the_same_row() -> None:
    main = (_f("pkg\\app.py"), 1, "<module>")
    boot = ("<frozen importlib._bootstrap>", 1, "_find_and_load")
    lib = (f"{_SITE}\\sqlalchemy\\__init__.py", 1, "<module>")
    raw = {
        main: (1, 1, 0.02, 0.5, {}),
        boot: (1, 1, 0.01, 0.48, {main: (1, 1, 0.01, 0.48)}),
        lib: (1, 1, 0.3, 0.47, {boot: (1, 1, 0.3, 0.47)}),
    }
    run = profile_formats.get("pstats").read(marshal.dumps(raw))
    conv = convert_profile(run, root=ROOT, symbols_by_file=_INDEXED)
    [row] = conv.document["symbols"]
    assert (row["path"], row["function"]) == ("pkg/app.py", "<module>")
    assert row["self"] == pytest.approx(0.33)
    assert row["inside"][0]["name"] == "sqlalchemy"


def test_a_module_level_lambda_a_function_calls_is_not_import_time() -> None:
    frames = [
        FRAMES[8],
        {"name": "<lambda>", "file": FRAMES[3]["file"], "line": 4},
    ]
    conv = _convert(frames, [([0, 1], 2)])
    assert [(e["path"], e["function"]) for e in conv.document["symbols"]] == [
        ("pkg/app.py", "pkg.app.App.run")
    ]
    assert conv.self_unattributed == {"module_body": 2.0}
    # Run while importing, the same lambda is that import's time.
    genexpr = {"name": "<genexpr>", "file": FRAMES[1]["file"], "line": 50}
    frames = [FRAMES[1], FRAMES[2], genexpr]
    [row] = _convert(frames, [([0, 1, 2], 3)]).document["symbols"]
    assert (row["path"], row["function"], row["self"]) == ("pkg/app.py", "<module>", 3)


def test_measured_hotspots_mint_no_item_for_import_time() -> None:
    from repowise.core.analysis.health.perf.opportunities import measured_hotspots

    module, fn = "src/app.py::<module>", "src/app.py::load"
    items = measured_hotspots({}, {module: "15% of run", fn: "9% of run"}, frozenset({module, fn}))
    assert [i.evidence[0]["function_name"] for i in items] == ["load"]
