"""What a measured function's time went to (``inside``) and what drove it (``drivers``).

One fixture per stack format with splits worked out by hand, the shares a
share of the function's inclusive time (``go tool pprof -peek``'s reading).
"""

from __future__ import annotations

import json

from repowise.core.evidence import parse_evidence
from repowise.core.evidence.profile_convert import (
    convert_profile,
    is_library_frame,
    is_native_frame,
)
from repowise.core.evidence.profile_formats import profile_formats
from repowise.core.frame_resolver import ProfileFrame
from tests.unit.evidence.test_cpuprofile import URL as JS_URL
from tests.unit.evidence.test_cpuprofile import _convert as _js_convert
from tests.unit.evidence.test_cpuprofile import _profile as _cpuprofile
from tests.unit.evidence.test_profile_formats import ROOT, SYMBOLS, _bytes, _f, _pprof, _sym


def _entries(conv) -> dict[str, dict]:
    return {e["function"]: e for e in conv.document["symbols"]}


def _convert(fmt: str, data: bytes, **kw):
    kw.setdefault("value_kind", "wall")
    run = profile_formats.get(fmt).read(data)
    return convert_profile(run, root=ROOT, symbols_by_file=kw.pop("symbols", SYMBOLS), **kw)


# -- speedscope: a wait spine, a library with its leaf, an import ------------------

SPEEDSCOPE = {
    "$schema": "https://www.speedscope.app/file-format-schema.json",
    "exporter": "py-spy@0.4.3",
    "shared": {
        "frames": [
            {"name": "<module>", "file": "C:\\Python\\runpy.py", "line": 88},  # 0
            {"name": "App.run", "file": _f("pkg\\app.py"), "line": 10},  # 1
            {"name": "App.load", "file": _f("pkg\\app.py"), "line": 25},  # 2
            {"name": "run", "file": "C:\\Python\\Lib\\asyncio\\runners.py", "line": 44},  # 3
            {
                "name": "run_forever",
                "file": "C:\\Python\\Lib\\asyncio\\base_events.py",
                "line": 6,
            },  # 4
            {
                "name": "EpollSelector.select",
                "file": "C:\\Python\\Lib\\selectors.py",
                "line": 9,
            },  # 5
            {"name": "Path.is_file", "file": "C:\\Python\\Lib\\pathlib.py", "line": 7},  # 6
            {"name": "posix.stat", "file": "<built-in>", "line": 0},  # 7
            {"name": "_find_and_load", "file": "<frozen importlib._bootstrap>", "line": 1},  # 8
            {
                "name": "execute",
                "file": "C:\\venv\\Lib\\site-packages\\sa\\engine.py",
                "line": 3,
            },  # 9
        ]
    },
    "profiles": [
        {
            "type": "sampled",
            "unit": "seconds",
            "samples": [
                [0, 1, 2, 6, 7],  # load -> is_file -> stat
                [0, 1, 2, 6],  # load -> is_file
                [0, 1, 2, 8, 9],  # load imports a module whose body runs a library
                [0, 1, 2],  # load's own code
                [0, 1, 3, 4, 5],  # run waits in the event loop
                [0, 1, 3, 4, 2],  # the loop runs load for run
            ],
            "weights": [5, 1, 2, 2, 8, 3],
        }
    ],
}


def test_speedscope_splits_by_the_callee_below_the_innermost_occurrence() -> None:
    got = _entries(_convert("speedscope", _bytes(SPEEDSCOPE)))
    load = got["pkg.app.App.load"]
    assert load["total"] == 13
    assert load["inside"] == [
        {
            "name": "Path.is_file",
            "kind": "library",
            "share": round(6 / 13, 4),
            "leaf": {"name": "posix.stat", "kind": "native", "share": round(5 / 13, 4)},
        },
        {"name": "(own code)", "kind": "own", "share": round(5 / 13, 4)},
        {
            "name": "_find_and_load",
            "kind": "import",
            "share": round(2 / 13, 4),
            "leaf": {"name": "execute", "kind": "library", "share": round(2 / 13, 4)},
        },
    ]
    # Through the event loop, the repo caller drives it, not the loop's frames.
    assert load["drivers"] == [
        {"function": "pkg.app.App.run", "path": "pkg/app.py", "line": 3, "share": 1.0},
    ]


def test_an_event_loop_below_a_function_is_a_wait_spine() -> None:
    run = _entries(_convert("speedscope", _bytes(SPEEDSCOPE)))["pkg.app.App.run"]
    assert run["total"] == 21
    # The loop runs load for 3 of its 11: still the caller waiting on the loop.
    assert run["inside"] == [
        {
            "name": "run",
            "kind": "wait",
            "share": round(11 / 21, 4),
            "leaf": {"name": "EpollSelector.select", "kind": "wait", "share": round(8 / 21, 4)},
        },
        {
            "name": "pkg.app.App.load",
            "kind": "repo",
            "share": round(10 / 21, 4),
            "leaf": {"name": "posix.stat", "kind": "native", "share": round(5 / 21, 4)},
        },
    ]
    # Nothing maintained above it: the frame directly above, with no file.
    assert run["drivers"] == [{"function": "<module>", "share": 1.0}]


def test_the_document_carries_inside_and_drivers_through_the_v1_parser() -> None:
    conv = _convert("speedscope", _bytes(SPEEDSCOPE))
    doc = parse_evidence(json.dumps(conv.document))
    load = next(e for e in doc.entries if e["function"] == "pkg.app.App.load")
    assert load["inside"] == _entries(conv)["pkg.app.App.load"]["inside"]
    assert load["drivers"][0]["path"] == "pkg/app.py"


# -- collapsed: recursion and more callees than are kept -------------------------

_APP = f"{ROOT}\\pkg\\app.py"
COLLAPSED = "\n".join(
    [
        # run -> load -> run -> dumps: run's callee is below its innermost occurrence.
        f"<module> (C:\\Python\\run.py:1);App.run ({_APP}:10);App.load ({_APP}:25);App.run ({_APP}:11);dumps (C:\\Python\\json.py:5) 6",
        f"<module> (C:\\Python\\run.py:1);App.run ({_APP}:10);loads (C:\\Python\\json.py:9) 2",
        f"<module> (C:\\Python\\run.py:1);App.run ({_APP}:10);sleep (<built-in>:0) 1",
        f"<module> (C:\\Python\\run.py:1);App.run ({_APP}:10);sub (C:\\Python\\re.py:3) 1",
        f"<module> (C:\\Python\\run.py:1);App.run ({_APP}:10) 4",
    ]
)


def test_collapsed_recursion_counts_each_stack_once_and_keeps_the_three_heaviest() -> None:
    got = _entries(_convert("collapsed", COLLAPSED.encode()))
    run, load = got["pkg.app.App.run"], got["pkg.app.App.load"]
    assert run["total"] == 14
    assert run["inside"] == [
        {"name": "dumps", "kind": "library", "share": round(6 / 14, 4)},
        {"name": "(own code)", "kind": "own", "share": round(4 / 14, 4)},
        {"name": "loads", "kind": "library", "share": round(2 / 14, 4)},
    ]
    # load's callee is run (repo) and its driver is the outer run; run's own
    # driver is the frame above its outermost occurrence.
    assert load["inside"] == [
        {
            "name": "pkg.app.App.run",
            "kind": "repo",
            "share": 1.0,
            "leaf": {"name": "dumps", "kind": "library", "share": 1.0},
        },
    ]
    assert load["drivers"] == [
        {"function": "pkg.app.App.run", "path": "pkg/app.py", "line": 3, "share": 1.0},
    ]
    assert run["drivers"] == [{"function": "<module>", "share": 1.0}]
    assert sum(i["share"] for i in run["inside"]) <= 1.0


def test_a_sleep_is_a_wait_and_a_builtin_is_native() -> None:
    text = f"<module> (C:\\Python\\run.py:1);App.run ({_APP}:10);time.sleep (<built-in>:0) 3\n"
    text += f"<module> (C:\\Python\\run.py:1);App.run ({_APP}:10);len (<built-in>:0) 1\n"
    run = _entries(_convert("collapsed", text.encode()))["pkg.app.App.run"]
    assert [(i["name"], i["kind"]) for i in run["inside"]] == [
        ("time.sleep", "wait"),
        ("len", "native"),
    ]
    # A wait is in the split but never in the self time it ranks by.
    assert (run["self"], run["total"]) == (1, 4)


# -- pprof: Go runtime, the module cache and GOROOT -------------------------------

GO_ROOT = "D:\\src\\svc"
GO_FILE = f"{GO_ROOT}\\store\\store.go"
GO_SYMBOLS = {
    "store/store.go": [
        _sym("store/store.go", "store.Find", 5, 20, "function"),
        _sym("store/store.go", "store.Handler", 22, 30, "function"),
    ],
}
GORM = "C:\\Users\\dev\\go\\pkg\\mod\\gorm.io\\gorm@v1.25.12\\finisher_api.go"
GOROOT = "C:\\Program Files\\Go\\src"


def _go_profile() -> bytes:
    functions = {
        1: ("example.com/svc/store.Handler", GO_FILE, 22),
        2: ("example.com/svc/store.Find", GO_FILE, 5),
        3: ("gorm.io/gorm.(*DB).First", GORM, 120),
        4: ("runtime.cgocall", f"{GOROOT}\\runtime\\cgocall.go", 150),
        5: ("runtime.mapassign_faststr", f"{GOROOT}\\runtime\\map_faststr.go", 200),
        6: ("strings.Index", f"{GOROOT}\\strings\\strings.go", 90),
        7: ("memeqbody", f"{GOROOT}\\internal\\bytealg\\equal_amd64.s", 1),
    }
    locations = {
        1: [(1, 25)],
        2: [(2, 8)],
        3: [(3, 125)],
        4: [(4, 160)],
        5: [(5, 210)],
        6: [(6, 95)],
        7: [(7, 3)],
    }
    samples = [  # (location ids leaf first, [count, ns])
        ([4, 3, 2, 1], [6, 60]),  # Find -> gorm First -> cgocall
        ([5, 2, 1], [2, 20]),  # Find -> map assign
        ([7, 6, 2, 1], [1, 10]),  # Find -> strings.Index -> memeqbody
        ([2, 1], [1, 10]),  # Find's own code
    ]
    return _pprof(samples, locations, functions)


def test_pprof_names_runtime_frames_native_and_module_cache_frames_library() -> None:
    run = profile_formats.get("pprof").read(_go_profile())
    conv = convert_profile(run, root=GO_ROOT, symbols_by_file=GO_SYMBOLS)
    got = _entries(conv)
    find = got["store.Find"]
    assert find["inside"] == [
        {
            "name": "gorm.io/gorm.(*DB).First",
            "kind": "library",
            "share": 0.6,
            "leaf": {"name": "runtime.cgocall", "kind": "native", "share": 0.6},
        },
        {"name": "runtime.mapassign_faststr", "kind": "native", "share": 0.2},
        {
            "name": "strings.Index",
            "kind": "library",
            "share": 0.1,
            "leaf": {"name": "memeqbody", "kind": "native", "share": 0.1},
        },
    ]
    assert find["drivers"] == [
        {"function": "store.Handler", "path": "store/store.go", "line": 22, "share": 1.0},
    ]
    assert got["store.Handler"]["inside"] == [
        {
            "name": "store.Find",
            "kind": "repo",
            "share": 1.0,
            "leaf": {"name": "runtime.cgocall", "kind": "native", "share": 0.6},
        },
    ]
    # The run's split books a library the way ``inside`` names one: the module
    # cache and GOROOT's strings.Index are libraries, the runtime stays with Find.
    assert (conv.libraries, conv.repo_self) == (70, 30)


def _frame(path: str, name: str, language: str = "go") -> ProfileFrame:
    return ProfileFrame(path=path, line=1, language=language, function=name)


def test_library_frames_are_dependencies_and_the_standard_library() -> None:
    def library(path: str, name: str, language: str = "go") -> bool:
        return is_library_frame(_frame(path, name, language), "outside_root")

    assert library(GORM, "gorm.io/gorm.(*DB).First")
    assert library("/venv/lib/python3.12/site-packages/sa/engine.py", "execute", "python")
    assert library(f"{GOROOT}\\net\\http\\server.go", "net/http.(*conn).serve")
    toolchain = "/home/u/go/pkg/mod/golang.org/toolchain@v0.0.1-go1.22.4.linux-amd64/src"
    assert library(f"{toolchain}/net/http/server.go", "net/http.(*conn).serve")
    assert library("C:\\Python312\\Lib\\json\\decoder.py", "JSONDecoder.decode", "python")
    assert library("node:fs", "readFileSync", "javascript")
    for jdk in (
        "java/util/HashMap.get",
        "java.util.HashMap.get",
        "jdk.internal.misc.Unsafe.park",
        "sun.nio.ch.SocketDispatcher.read",
        "javax.crypto.Cipher.doFinal",
    ):
        assert library("", jdk, "unknown"), jdk
    # The runtime, built-ins and import machinery (import time) are not; a frame
    # the checkout maintains never is.
    assert not library("<frozen importlib._bootstrap>", "_find_and_load", "python")
    assert not library("node:internal/modules/cjs/loader", "Module._load", "javascript")
    assert not library(f"{GOROOT}\\runtime\\cgocall.go", "runtime.cgocall")
    assert not library("<built-in>", "posix.stat", "python")
    assert not library("~", "<built-in method builtins.len>", "unknown")  # cProfile
    # A pathless name places a frame only when it is the JDK's: an application's
    # own frames in a collapsed JVM stack, or a pathless dependency, are not known.
    assert not library("", "com/acme/App.run", "unknown")
    assert not library("", "com.acme.App.run", "unknown")
    assert not library("", "gorm.(*DB).First", "unknown")
    assert not library("", "(program)", "unknown")
    assert is_library_frame(_frame("vendor/x/y.go", "y.F"), "vendored")
    assert not is_library_frame(_frame("pkg/app.py", "App.run", "python"), "no_span")
    assert not is_library_frame(_frame("pkg/app.py", "App.run", "python"), None)


def test_native_frames_by_path() -> None:
    frame = _frame
    assert is_native_frame(frame(f"{GOROOT}\\runtime\\cgocall.go", "runtime.cgocall"))
    assert is_native_frame(frame(f"{GOROOT}\\internal\\bytealg\\equal_amd64.s", "memeqbody"))
    assert is_native_frame(frame("", "(garbage collector)", "unknown"))
    assert is_native_frame(frame("<built-in>", "posix.stat", "unknown"))
    assert not is_native_frame(frame(f"{GOROOT}\\strings\\strings.go", "strings.Index"))


# -- cpuprofile: Node internals, natives and node_modules ---------------------------


def test_cpuprofile_splits_node_internals_natives_and_dependencies() -> None:
    data = _cpuprofile(
        [
            (1, "(root)", "", -1, -1, [2]),
            (2, "main", f"{JS_URL}app.js", 31, 13, [3, 8]),
            (3, "hashAll", f"{JS_URL}app.js", 15, 16, [4, 6]),
            (4, "digest", "node:internal/crypto/hash", 149, 39, [5]),
            (5, "update", "", -1, -1, []),
            (6, "mapAll", f"{JS_URL}node_modules/tinydep/index.js", 2, 15, []),
            (8, "readFileSync", "node:fs", 0, 0, []),
        ],
        [5, 5, 5, 4, 6, 6, 3, 8],
    )
    conv = _js_convert(profile_formats.get("cpuprofile").read(data))
    got = _entries(conv)
    hash_all = got["app.hashAll"]
    assert hash_all["inside"] == [
        {
            "name": "digest",
            "kind": "library",
            "share": round(4 / 7, 4),
            "leaf": {"name": "update", "kind": "native", "share": round(3 / 7, 4)},
        },
        {"name": "mapAll", "kind": "library", "share": round(2 / 7, 4)},
        {"name": "(own code)", "kind": "own", "share": round(1 / 7, 4)},
    ]
    assert hash_all["drivers"] == [
        {"function": "app.main", "path": "app.js", "line": 32, "share": 1.0},
    ]
    assert got["app.main"]["inside"][0] == {
        "name": "app.hashAll",
        "kind": "repo",
        "share": round(7 / 8, 4),
        "leaf": {"name": "update", "kind": "native", "share": round(3 / 8, 4)},
    }


def test_a_bare_library_name_in_a_collapsed_stack_is_a_library() -> None:
    def frame(name: str) -> ProfileFrame:
        return ProfileFrame(path="", line=0, language="unknown", function=name)

    assert not is_native_frame(frame("java/util/HashMap.get"))
    assert not is_native_frame(frame("std::sort"))
    assert is_native_frame(frame("(garbage collector)"))
    assert is_native_frame(frame("writev"))


def test_a_name_the_v1_parser_would_refuse_is_written_so_it_reads() -> None:
    long_name = "x" * 600
    text = f"App.run ({_APP}:10);{long_name}\x07 (C:/Python/lib.py:3) 2\n"
    conv = _convert("collapsed", text.encode())
    (entry,) = parse_evidence(json.dumps(conv.document)).entries
    assert entry["inside"][0]["name"] == "x" * 512


def test_a_driver_in_a_changed_or_vendored_file_keeps_its_path_not_a_symbol() -> None:
    from repowise.core.evidence.ingest import IndexedSymbol as Indexed
    from repowise.core.evidence.ingest import join_entries, symbol_lookup

    def doc(path: str) -> object:
        return parse_evidence(
            json.dumps(
                {
                    "format": "repowise-evidence-v1",
                    "kind": "profile",
                    "value_kind": "wall",
                    "unit": "samples",
                    "symbols": [
                        {
                            "path": "a.py",
                            "function": "f",
                            "line": 1,
                            "self": 1,
                            "total": 2,
                            "drivers": [
                                {"function": "main", "path": path, "line": 1, "share": 1.0}
                            ],
                        }
                    ],
                }
            )
        )

    by_file = {
        "a.py": symbol_lookup([Indexed("a.py::f", "f", "f", 1)]),
        "b.py": symbol_lookup([Indexed("b.py::main", "main", "main", 1)]),
    }
    origins = {"a.py": "production", "b.py": "production"}

    def drivers(changed: frozenset[str], origin: str = "production") -> list:
        (row,) = join_entries(doc("b.py"), by_file, {**origins, "b.py": origin}, changed).rows
        return json.loads(row["drivers_json"])

    assert drivers(frozenset()) == [{"name": "main", "share": 1.0, "symbol": "b.py::main"}]
    assert drivers(frozenset({"b.py"})) == [{"name": "main", "path": "b.py", "share": 1.0}]
    assert drivers(frozenset(), "vendored") == [{"name": "main", "path": "b.py", "share": 1.0}]


def test_a_dotted_bare_name_is_a_library_and_a_runtime_marker_is_native() -> None:
    def frame(name: str) -> ProfileFrame:
        return ProfileFrame(path="", line=0, language="unknown", function=name)

    assert not is_native_frame(frame("gorm.(*DB).First"))
    assert not is_native_frame(frame("requests.get"))
    assert is_native_frame(frame("runtime.mallocgc"))
    assert is_native_frame(frame("(program)"))


def test_a_pathless_frame_counts_as_a_library_in_the_split_only_when_it_is_the_jdks() -> None:
    text = "\n".join(
        [
            f"App.run ({_APP}:10);java/util/HashMap.get 3",
            f"App.run ({_APP}:10);com/acme/Helper.work 2",
            "com/acme/Main.main;com/acme/Helper.work 5",
        ]
    )
    conv = _convert("collapsed", text.encode())
    # The JDK call is a library; the application's own pathless frames are not
    # known to be, so run keeps them and the stack with no repo frame is neither.
    assert (conv.libraries, conv.repo_self) == (3, 2)
    inside = {i["name"]: i["kind"] for i in _entries(conv)["pkg.app.App.run"]["inside"]}
    assert inside == {"java/util/HashMap.get": "library", "com/acme/Helper.work": "library"}
