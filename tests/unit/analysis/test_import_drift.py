"""Whether a file's edges can have moved between two versions of its text."""

from __future__ import annotations

import pytest

from repowise.core.analysis.import_drift import edges_may_differ, may_carry_edges

_PY = b"from a.b import (\n    c,\n)\n\n\ndef f():\n    return c(1)\n"
_TS = b'import { a } from "./a";\n\nexport function f() {\n  return a(1);\n}\n'


@pytest.mark.parametrize(
    ("path", "before", "after"),
    [
        ("m.py", _PY, _PY.replace(b"c(1)", b"c(2) + 3")),
        ("m.py", _PY, _PY + b'\nlog("done with this step")\n'),
        ("m.ts", _TS, _TS.replace(b"a(1)", b"a(2)")),
        ("m.ts", _TS, _TS + b"// a comment\n"),
        ("m.py", _PY, _PY + b'\nNAMES = ["extra", "with spaces in it"]\n'),
        ("m.go", b"package m\n\nfunc F() {}\n", b"package m\n\nfunc F()  {}\n"),
        # Reformatted, so parsed, but the same import.
        ("m.ts", _TS, _TS.replace(b'import { a } from "./a";', b'import {\n  a,\n} from "./a";')),
    ],
)
def test_a_body_change_keeps_the_edges(path, before, after) -> None:
    assert edges_may_differ(path, before, after) is False


@pytest.mark.parametrize(
    ("path", "before", "after"),
    [
        ("m.py", _PY, _PY.replace(b"    c,\n", b"    c,\n    d,\n")),
        ("m.py", _PY, _PY + b"\ndef g():\n    from x import y\n"),
        ("m.py", _PY, _PY + b'\nPLUGINS = ["pkg.plugins.extra"]\n'),
        # A bracket in a comment must not close the statement early.
        (
            "m.py",
            b"from a import (  # )\n    c,\n)\n",
            b"from a import (  # )\n    c,\n    d,\n)\n",
        ),
        ("m.ts", b'import {\n  a,\n} from "./a";\n', b'import {\n  a,\n  b,\n} from "./a";\n'),
        ("m.ts", _TS, _TS + b'export * from "./b";\n'),
        ("m.ts", _TS, _TS.replace(b"a(1)", b'(await import("./c")).a(1)')),
        ("m.ts", _TS, _TS + b'vi.mock("./d");\n'),
        ("m.go", b"package m\n", b"package m\n\nfunc F() { G() }\n"),
        # Astro is compared as text, so a markup-only import still moves the edges.
        ("index.astro", b"<h1>Hi</h1>\n", b'<h1>Hi</h1>\n<script src="./x.ts"></script>\n'),
    ],
)
def test_an_import_or_a_module_name_moves_the_edges(path, before, after) -> None:
    assert edges_may_differ(path, before, after) is True


_SETTINGS = b'INSTALLED_APPS = [\n    "django.contrib.admin",\n    "polls",\n]\n'


@pytest.mark.parametrize(
    ("path", "before", "after"),
    [
        # A lazy registry's module:attr string.
        (
            "pkg/cli/__init__.py",
            b'CMDS = {"run": "pkg.cli.run:main"}\n',
            b'CMDS = {"run": "pkg.cli.other:main"}\n',
        ),
        # The same string moving out of a comment into code.
        (
            "pkg/cli/__init__.py",
            b'CMDS = {\n    # "run": "pkg.cli.run:main",\n}\n',
            b'CMDS = {\n    "run": "pkg.cli.run:main",\n}\n',
        ),
        # A dotted string starts to count once the file loads modules by name.
        (
            "pkg/loader.py",
            b'NAME = "pkg.plugins.a"\n',
            b'NAME = "pkg.plugins.a"\nload = lazy_subcommands\n',
        ),
        # A templated load.
        (
            "pkg/loader.py",
            b'importlib.import_module(f"pkg.plugins.{name}")\n',
            b'importlib.import_module(f"pkg.handlers.{name}")\n',
        ),
        # A test naming a helper it runs by a path relative to itself.
        (
            "tests/test_server.py",
            b'H = Path(__file__).parent / "1_server.py"\n',
            b'H = Path(__file__).parent / "2_server.py"\n',
        ),
        ("tests/cli.test.ts", b'spawn("~/run.ts");\n', b'spawn("~/serve.ts");\n'),
        # Django settings and URLconfs name apps and modules by bare name.
        ("mysite/settings.py", _SETTINGS, _SETTINGS.replace(b'"polls"', b'"blog"')),
        ("mysite/settings/base.py", _SETTINGS, _SETTINGS.replace(b'    "polls",\n', b"")),
        ("mysite/settings.py", b'ROOT_URLCONF = "urls"\n', b'ROOT_URLCONF = "routes"\n'),
        ("mysite/settings.py", b'MIDDLEWARE = ["audit"]\n', b'MIDDLEWARE = ["audit", "trace"]\n'),
        # Parsed at the indexed revision, unparseable now: its edges are gone.
        ("mysite/settings.py", _SETTINGS, _SETTINGS.replace(b"]\n", b"\n")),
        (
            "mysite/urls.py",
            b'urlpatterns = [path("", include("polls"))]\n',
            b'urlpatterns = [path("", include("blog"))]\n',
        ),
    ],
)
def test_a_string_an_edge_producer_resolves_moves_the_edges(path, before, after) -> None:
    assert edges_may_differ(path, before, after) is True


def test_a_bare_name_outside_django_wiring_is_not_compared() -> None:
    before = b'MODES = ["polls"]\n'
    assert edges_may_differ("mysite/views.py", before, before.replace(b"polls", b"blog")) is False


def test_a_file_on_one_side_only_moves_no_route() -> None:
    assert edges_may_differ("m.py", None, _PY) is False
    assert edges_may_differ("m.py", _PY, None) is False
    assert edges_may_differ("m.go", b"package m\n", b"package m\n") is False


@pytest.mark.parametrize(
    ("path", "carries"),
    [
        ("a.py", True),
        ("a.tsx", True),
        ("a.go", True),
        ("a.json", False),
        ("a.yaml", False),
        ("README.md", False),
        ("app.css", False),
        ("glossary.jsonl", False),
    ],
)
def test_only_code_carries_edges(path, carries) -> None:
    assert may_carry_edges(path) is carries


# -- what a rewired file may newly reach --------------------------------------


def _resolve_in(files):
    from repowise.core.analysis.import_drift import import_resolver

    return import_resolver(".", files)


def test_a_gained_import_resolves_to_its_file_and_a_kept_one_does_not() -> None:
    from repowise.core.analysis.import_drift import rewiring

    files = {"src/m.py", "src/a.py", "src/b.py", "src/__init__.py"}
    before = b"from src import a\n"
    after = b"from src import a\nfrom src import b\nimport os\n"
    found = rewiring("src/m.py", before, after, _resolve_in(files))
    # ``os`` is outside the repository, so it reaches nothing a test could.
    assert found is not None and found.imports == {"src/b.py"}
    assert found.names == set() and found.dirs == set()


def test_a_gained_string_gives_its_words_and_a_path_its_directories() -> None:
    from repowise.core.analysis.import_drift import rewiring

    after = _PY + b'\nPLUGINS = ["pkg.plugins.extra"]\nDATA = "fixtures/repo/a.py"\n'
    found = rewiring("m.py", _PY, after, _resolve_in({"m.py"}))
    assert found is not None
    assert {"pkg", "plugins", "extra", "fixtures", "repo", "a"} <= found.names
    assert {"fixtures", "repo"} <= found.dirs and "plugins" not in found.dirs


@pytest.mark.parametrize(
    ("path", "after"),
    [
        # Another language reaches code without naming it.
        ("m.go", b"package m\n\nfunc G() {}\n"),
        # A dynamic-load marker makes strings already there resolve.
        ("m.py", _PY + b'\nimport importlib\nNAME = "pkg.mod"\nimportlib.import_module(NAME)\n'),
    ],
)
def test_a_rewiring_that_cannot_be_told_is_unknown(path, after) -> None:
    from repowise.core.analysis.import_drift import rewiring

    before = b"package m\n" if path.endswith(".go") else _PY
    assert rewiring(path, before, after, _resolve_in({path})) is None


def test_an_import_that_will_not_resolve_is_unknown() -> None:
    from repowise.core.analysis.import_drift import rewiring

    def fail(*_):
        raise ValueError("no resolver")

    assert rewiring("m.py", _PY, _PY + b"import x\n", fail) is None


def test_a_gained_dynamic_load_in_typescript_is_unknown() -> None:
    from repowise.core.analysis.import_drift import rewiring

    after = _TS + b'const mods = import.meta.glob("./mods/*.ts");\n'
    assert rewiring("m.ts", _TS, after, _resolve_in({"m.ts", "a.ts"})) is None


def test_an_unchanged_import_or_string_onto_a_file_added_since_counts() -> None:
    from repowise.core.analysis.import_drift import rewiring

    # src/a.py was added since the index; the import of it did not change.
    files = {"src/m.py", "src/a.py", "src/b.py"}
    before = b'from src import a\nNAME = "src.b"\n'
    after = before + b"import os\n"
    gained = rewiring("src/m.py", before, after, _resolve_in(files))
    assert gained is not None and gained.imports == set() and gained.held == set()
    found = rewiring("src/m.py", before, after, _resolve_in(files), {"src/a.py"})
    # Only the added file counts; every string's words are kept for it.
    assert found is not None and found.imports == {"src/a.py"} and "b" in found.held
