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
    ],
)
def test_an_import_or_a_module_name_moves_the_edges(path, before, after) -> None:
    assert edges_may_differ(path, before, after) is True


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
