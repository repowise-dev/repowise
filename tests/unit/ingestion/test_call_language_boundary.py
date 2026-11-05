"""Call resolution refuses unrelated languages without losing supported interop."""

from pathlib import Path

import pytest

from tests.unit.ingestion.test_call_resolver_strategies import _edges, _link_imports, _parse_all


@pytest.mark.parametrize("body", ["func run() { Widget.go() }", "Widget.go()"])
def test_swift_receiver_does_not_resolve_to_kotlin(tmp_path: Path, body: str) -> None:
    parsed = _parse_all(
        tmp_path,
        {
            "app.swift": ("swift", body),
            "widget.kt": ("kotlin", "class Widget { fun go() {} }"),
        },
    )
    assert parsed["app.swift"].calls
    assert _edges(parsed, tmp_path) == []


@pytest.mark.parametrize("call", ["Widget.go()", "helper()", "new Widget()"])
def test_imports_cannot_make_an_unrelated_language_callable(tmp_path: Path, call: str) -> None:
    parsed = _parse_all(
        tmp_path,
        {
            "app.ts": (
                "typescript",
                f"import {{ Widget, helper }} from './widget'; function run() {{ {call}; }}",
            ),
            "widget.py": (
                "python",
                "class Widget:\n    def go(self): pass\ndef helper(): pass\n",
            ),
        },
    )
    # A coarse import match must not turn a Python namesake into a TS callee.
    _link_imports(parsed, {"app.ts": {"./widget": "widget.py"}})
    assert parsed["app.ts"].calls
    assert _edges(parsed, tmp_path, {"app.ts": {"widget.py"}}) == []


@pytest.mark.parametrize("call", ["helper()", "Widget()"])
def test_global_free_calls_still_refuse_unrelated_languages(tmp_path: Path, call: str) -> None:
    parsed = _parse_all(
        tmp_path,
        {
            "app.swift": ("swift", f"func run() {{ {call} }}"),
            "widget.kt": ("kotlin", "class Widget {}\nfun helper() {}"),
        },
    )
    assert parsed["app.swift"].calls
    assert _edges(parsed, tmp_path) == []


@pytest.mark.parametrize("language,extension", [("typescript", "ts"), ("javascript", "js")])
def test_js_family_imports_keep_resolving(tmp_path: Path, language: str, extension: str) -> None:
    caller = f"app.{extension}"
    parsed = _parse_all(
        tmp_path,
        {
            caller: (
                language,
                "import { Widget, helper } from './widget';\n"
                "function run() { Widget.go(); helper(); new Widget(); }",
            ),
            "widget.js": (
                "javascript",
                "export class Widget { static go() {} }\nexport function helper() {}",
            ),
        },
    )
    _link_imports(parsed, {caller: {"./widget": "widget.js"}})
    assert {edge[1] for edge in _edges(parsed, tmp_path, {caller: {"widget.js"}})} == {
        "widget.js::Widget::go", "widget.js::helper", "widget.js::Widget",
    }


def test_kotlin_calls_into_java_keep_resolving(tmp_path: Path) -> None:
    parsed = _parse_all(
        tmp_path,
        {
            "app.kt": ("kotlin", "package app\nfun run() { Widget.go() }"),
            "Widget.java": (
                "java", "package app; public class Widget { public static void go() {} }",
            ),
        },
    )
    assert [(edge[0], edge[1]) for edge in _edges(parsed, tmp_path)] == [
        ("app.kt::run", "Widget.java::Widget::go"),
    ]


def test_cpp_call_keeps_its_c_definition(tmp_path: Path) -> None:
    parsed = _parse_all(
        tmp_path,
        {
            "app.cpp": ("cpp", '#include "lib.h"\nvoid run() { helper(); }'),
            "lib.h": ("c", "void helper(void);"),
            "lib.c": ("c", '#include "lib.h"\nvoid helper(void) {}'),
        },
    )
    _link_imports(parsed, {"app.cpp": {"lib.h": "lib.h"}, "lib.c": {"lib.h": "lib.h"}})
    assert [(edge[0], edge[1]) for edge in _edges(
        parsed, tmp_path, {"app.cpp": {"lib.h"}, "lib.c": {"lib.h"}},
    )] == [("app.cpp::run", "lib.c::helper")]


def test_foreign_inner_call_cannot_type_a_local_chain(tmp_path: Path) -> None:
    parsed = _parse_all(
        tmp_path,
        {
            "app.ts": (
                "typescript",
                "import { Factory } from './factory';\n"
                "class Result { finish() {} }\n"
                "function run() { Factory.make().finish(); }",
            ),
            "factory.java": (
                "java", "class Factory { static Result make() { return null; } }",
            ),
        },
    )
    _link_imports(parsed, {"app.ts": {"./factory": "factory.java"}})
    assert {call.target_name for call in parsed["app.ts"].calls} == {"make", "finish"}
    # A foreign method's return type must not invent an edge to local Result.finish.
    assert _edges(parsed, tmp_path, {"app.ts": {"factory.java"}}) == []


@pytest.mark.parametrize(
    "language,source",
    [
        ("vue", "<script>\n%s\n</script>"),
        ("svelte", "<script>\n%s\n</script>"),
        ("astro", "---\n%s\n---\n<div />"),
    ],
)
def test_component_scripts_can_call_javascript(tmp_path: Path, language: str, source: str) -> None:
    caller = f"App.{language}"
    parsed = _parse_all(
        tmp_path,
        {
            caller: (language, source % "import { helper } from './helper';\nhelper();"),
            "helper.js": ("javascript", "export function helper() {}"),
        },
    )
    _link_imports(parsed, {caller: {"./helper": "helper.js"}})
    assert [edge[1] for edge in _edges(parsed, tmp_path, {caller: {"helper.js"}})] == [
        "helper.js::helper",
    ]


def test_csharp_calls_into_vbnet_keep_resolving(tmp_path: Path) -> None:
    parsed = _parse_all(
        tmp_path,
        {
            "App.cs": ("csharp", "class App {\n static void Run() { Widget.Go(); }\n}"),
            "Widget.vb": (
                "vbnet", "Public Class Widget\nPublic Shared Sub Go()\nEnd Sub\nEnd Class",
            ),
        },
    )
    assert [(edge[0], edge[1]) for edge in _edges(parsed, tmp_path)] == [
        ("App.cs::App::Run", "Widget.vb::Widget::Go"),
    ]


@pytest.mark.parametrize(
    "module,call,expected",
    [
        ("@salesforce/apex/Widget.go", "loadWidget()", True),
        ("@salesforce/apexContinuation/Widget.go", "loadWidget()", True),
        ("@salesforce/apex/Widget.go", "Widget.go()", False),
        ("./Widget", "Widget.go()", False),
        ("./Widget", "loadWidget()", False),
    ],
)
@pytest.mark.parametrize("language,extension", [("javascript", "js"), ("typescript", "ts")])
def test_only_explicit_salesforce_imports_bridge_to_apex(
    tmp_path: Path, module: str, call: str, expected: bool, language: str, extension: str,
) -> None:
    caller = f"app.{extension}"
    parsed = _parse_all(
        tmp_path,
        {
            caller: (
                language, f"import loadWidget from '{module}';\nfunction run() {{ {call}; }}",
            ),
            "Widget.cls": (
                "apex", "public class Widget {\n public static void go() {}\n}",
            ),
        },
    )
    _link_imports(parsed, {caller: {module: "Widget.cls"}})
    targets = [edge[1] for edge in _edges(parsed, tmp_path, {caller: {"Widget.cls"}})]
    assert targets == (["Widget.cls::Widget::go"] if expected else [])
