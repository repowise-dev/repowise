"""A bare call whose name the calling function binds itself.

``function make(Class) { return new Class() }`` calls whatever class the
caller passes in, not the module's ``Class``: the parameter shadows every
same-named import and top-level declaration. The same holds for a local and
for a parameter of a function enclosing the caller, in TypeScript,
JavaScript and Python alike.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from repowise.core.ingestion.call_resolver import CallResolver
from repowise.core.ingestion.languages.receiver_types import scan_bindings
from repowise.core.ingestion.models import FileInfo, ParsedFile
from repowise.core.ingestion.parser import parse_file


def _parse_all(tmp_path: Path, files: dict[str, str], language: str) -> dict[str, ParsedFile]:
    out: dict[str, ParsedFile] = {}
    for rel, content in files.items():
        abs_ = tmp_path / rel
        abs_.parent.mkdir(parents=True, exist_ok=True)
        abs_.write_text(content, encoding="utf-8")
        info = FileInfo(
            path=rel,
            abs_path=str(abs_),
            language=language,
            size_bytes=abs_.stat().st_size,
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        )
        out[rel] = parse_file(info, content.encode("utf-8"))
    return out


def _callees(tmp_path: Path, files: dict[str, str], language: str, caller: str) -> set[str]:
    """Every callee of *caller* in ``api.*``, which imports ``util.*``."""
    parsed = _parse_all(tmp_path, files, language)
    api, util = sorted(parsed, key=lambda rel: not rel.startswith("api"))
    for imp in parsed[api].imports:
        imp.resolved_file = util
        for binding in imp.bindings:
            binding.source_file = util
    resolver = CallResolver(parsed, {api: {util}}, repo_path=str(tmp_path))
    return {
        rc.callee_id
        for rc in resolver.resolve_file(api, parsed[api].calls)
        if rc.caller_id == f"{api}::{caller}"
    }


TS_UTIL = """\
export abstract class Class {}
export function helper(x: number): number {
  return x + 1;
}
"""

TS_API = """\
import * as util from "./util";
import { Class, helper } from "./util";

export function _union(
  Class: util.SchemaClass<util.Class>,
  options: unknown,
) {
  return new Class(options);
}

export function withLocal(n: number) {
  const helper = (v: number) => v * 2;
  return helper(n);
}

export function plain(n: number) {
  return new Class(helper(n));
}

export function typed(a: Class, b = helper) {
  return helper(a, b);
}

export function laterCallback(xs: number[]) {
  const y = helper(1);
  return xs.map((helper) => helper + y);
}
"""


class TestTypeScript:
    @pytest.fixture
    def files(self) -> dict[str, str]:
        return {"util.ts": TS_UTIL, "api.ts": TS_API}

    def test_parameter_shadows_the_imported_class(self, tmp_path: Path, files) -> None:
        assert _callees(tmp_path, files, "typescript", "_union") == set()

    def test_local_shadows_the_imported_function(self, tmp_path: Path, files) -> None:
        assert _callees(tmp_path, files, "typescript", "withLocal") == set()

    def test_unshadowed_names_still_resolve(self, tmp_path: Path, files) -> None:
        assert _callees(tmp_path, files, "typescript", "plain") == {
            "util.ts::Class",
            "util.ts::helper",
        }

    def test_annotation_and_default_bind_nothing(self, tmp_path: Path, files) -> None:
        # `Class` is only a type and `helper` only a default here.
        assert _callees(tmp_path, files, "typescript", "typed") == {"util.ts::helper"}

    def test_a_later_callback_parameter_does_not_shadow(self, tmp_path: Path, files) -> None:
        assert _callees(tmp_path, files, "typescript", "laterCallback") == {"util.ts::helper"}


JS_UTIL = """\
export function helper(x) {
  return x + 1;
}
"""

JS_API = """\
import { helper } from "./util.js";

export function shadowed(helper, n) {
  return helper(n);
}

export function plain(n) {
  return helper(n);
}
"""


class TestJavaScript:
    @pytest.fixture
    def files(self) -> dict[str, str]:
        return {"util.js": JS_UTIL, "api.js": JS_API}

    def test_parameter_shadows_the_import(self, tmp_path: Path, files) -> None:
        assert _callees(tmp_path, files, "javascript", "shadowed") == set()

    def test_unshadowed_name_still_resolves(self, tmp_path: Path, files) -> None:
        assert _callees(tmp_path, files, "javascript", "plain") == {"util.js::helper"}


PY_UTIL = """\
def helper(x):
    return x + 1
"""

PY_API = """\
from util import helper


def shadowed(helper, n):
    return helper(n)


def plain(n, fallback: int = 0):
    return helper(n)
"""


class TestPython:
    @pytest.fixture
    def files(self) -> dict[str, str]:
        return {"util.py": PY_UTIL, "api.py": PY_API}

    def test_parameter_shadows_the_import(self, tmp_path: Path, files) -> None:
        assert _callees(tmp_path, files, "python", "shadowed") == set()

    def test_unshadowed_name_still_resolves(self, tmp_path: Path, files) -> None:
        assert _callees(tmp_path, files, "python", "plain") == {"util.py::helper"}


class TestBindingScan:
    def test_typescript_parameter_types_and_defaults_are_not_bindings(self) -> None:
        names = {n for _, n in scan_bindings("function f(a: Foo<K, V>, b = make) {}", "typescript")}
        assert names == {"f", "a", "b"}

    def test_an_arrow_return_type_is_not_a_binding(self) -> None:
        text = "const create = (params?: P): ZodString => new ZodString(params);"
        names = {n for _, n in scan_bindings(text, "typescript")}
        assert "ZodString" not in names
        assert {"create", "params"} <= names

    def test_a_function_type_parameter_is_not_a_binding(self) -> None:
        text = "const f = (c: C, cb: (stream: S) => void): R => {\n  return stream(c, cb)\n}"
        assert "stream" not in {n for _, n in scan_bindings(text, "typescript")}

    def test_a_destructuring_pattern_ends_at_its_own_bracket(self) -> None:
        text = "for (const [k, v] of xs) {\n  out.push(serialize(k, v))\n}\nh['x'] = 1\n"
        bound = scan_bindings(text, "typescript")
        assert "serialize" not in {n for _, n in bound}
        assert {(1, "k"), (1, "v")} <= set(bound)

    def test_destructured_parameters_are_bindings(self) -> None:
        text = "function f({ a, b: c }: Props, [d]: T[]) {}"
        names = {n for _, n in scan_bindings(text, "typescript")}
        assert {"a", "b", "c", "d"} <= names
        assert "Props" not in names

    def test_python_annotations_and_defaults_are_not_bindings(self) -> None:
        names = {n for _, n in scan_bindings("def f(a: int, b=make):\n    pass\n", "python")}
        assert names == {"a", "b"}

    def test_javascript_reads_with_the_typescript_shapes(self) -> None:
        names = {n for _, n in scan_bindings("const f = (x, y) => x;", "javascript")}
        assert {"f", "x", "y"} <= names
