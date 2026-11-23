"""large_method counts the views of a SwiftUI body, a Compose function and a Flutter build() as markup."""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.biomarkers import FileContext
from repowise.core.analysis.health.biomarkers.large_method import LargeMethodDetector
from repowise.core.analysis.health.complexity.models import FunctionComplexity
from repowise.core.analysis.health.complexity.walker import walk_file

_R = range(30)


def _rows(template: str) -> str:
    return "\n".join(template.format(i=i) for i in _R)


_SWIFT = (
    "struct V: View {\n  @State var show = false\n  var body: some View {\n"
    "    VStack(alignment: .leading, spacing: 8) {\n"
    + _rows('      Text("Row {i}")\n        .font(.title)\n        .padding(.horizontal, {i})')
    + '\n      if show {\n        Button("Go") {\n          model.go()\n        }\n      }\n'
    '      if show && model.ready { Text("a") }\n      if model.count > 3 { Text("b") }\n'
    "    }\n  }\n}\n"
)
_KOTLIN = (
    "@Composable\nfun Screen(vm: Vm) {\n  Column(modifier = Modifier.padding(8.dp)) {\n"
    + _rows('    Text(\n      text = "Row {i}",\n      color = Color.Red,\n    )')
    + '\n    if (vm.show) {\n      Button(onClick = { vm.go() }) { Text("Go") }\n    }\n'
    '    if (vm.a && vm.b) { Text("x") }\n    if (vm.c > 2) { Text("y") }\n  }\n}\n'
)
_DART = (
    "class W extends StatelessWidget {\n  @override\n  Widget build(BuildContext context) {\n"
    "    if (loading) {\n      return const Spinner();\n    }\n"
    "    if (a && b) { return Text('x'); }\n    return Column(\n      children: [\n"
    + _rows(
        "        Padding(\n          padding: const EdgeInsets.all({i}),\n"
        "          child: Text('Row {i}', style: Theme.of(context).textTheme.bodyLarge),\n"
        "        ),"
    )
    + "\n        ElevatedButton(onPressed: () { go(); }, child: Text('Go')),\n"
    "      ],\n    );\n  }\n}\n"
)
_CASES = {
    "swift": ("V.swift", _SWIFT, "body"),
    "kotlin": ("Screen.kt", _KOTLIN, "Screen"),
    "dart": ("w.dart", _DART, "build"),
}


def _function(path: str, language: str, source: str, name: str) -> FunctionComplexity:
    [fn] = [fn for fn in walk_file(path, language, source.encode()).functions if fn.name == name]
    return fn


def _flagged(fn: FunctionComplexity, path: str, language: str) -> bool:
    ctx = FileContext(
        file_path=path,
        language=language,
        nloc=fn.nloc,
        has_test_file=False,
        module=None,
        all_functions=(fn,),
    )
    return bool(LargeMethodDetector().detect(ctx))


@pytest.mark.parametrize("language", sorted(_CASES))
def test_view_tree_lines_are_markup(language: str) -> None:
    path, source, name = _CASES[language]
    fn = _function(path, language, source, name)
    assert fn.nloc >= 100
    assert fn.nloc - fn.markup_nloc <= 12
    assert not _flagged(fn, path, language)


@pytest.mark.parametrize("language", sorted(_CASES))
def test_conditions_and_callbacks_stay_code(language: str) -> None:
    path, source, name = _CASES[language]
    fn = _function(path, language, source, name)
    lines = source.splitlines()
    first = fn.start_line - 1
    # The rows holding a condition or a callback's call are never markup.
    logic = [
        i
        for i, line in enumerate(lines[first : fn.end_line], start=first)
        if "if " in line or "if (" in line or "go()" in line
    ]
    assert len(logic) >= 3
    assert fn.nloc - fn.markup_nloc >= len(logic)


def test_kotlin_without_composable_gets_no_markup() -> None:
    source = _KOTLIN.replace("@Composable\n", "")
    assert _function("Screen.kt", "kotlin", source, "Screen").markup_nloc == 0


def test_swift_function_not_returning_a_view_gets_no_markup() -> None:
    source = _SWIFT.replace("var body: some View {", "var body: String {")
    assert _function("V.swift", "swift", source, "body").markup_nloc == 0


def test_dart_method_not_returning_a_widget_gets_no_markup() -> None:
    source = _DART.replace("Widget build(", "Object build(").replace("StatelessWidget", "Base")
    # Only the `],` closing the children list (a literal table) is data.
    assert _function("w.dart", "dart", source, "build").markup_nloc == 1


def test_long_compose_logic_is_still_flagged() -> None:
    logic = "\n".join(f"  val v{i} = vm.compute({i}, state)" for i in range(60))
    source = f"@Composable\nfun Screen(vm: Vm) {{\n{logic}\n{_KOTLIN.split('{', 1)[1]}"
    fn = _function("Screen.kt", "kotlin", source, "Screen")
    assert fn.nloc - fn.markup_nloc >= 68
    assert _flagged(fn, "Screen.kt", "kotlin")


def test_compose_static_calls_as_statements_stay_code() -> None:
    logs = "\n".join(f'    Log.d("T", "row {i}")' for i in range(70))
    source = f'@Composable\nfun Screen(vm: Vm) {{\n  if (vm.a) {{ vm.b() }}\n  if (vm.c) {{ vm.d() }}\n{logs}\n  Column(modifier = Modifier.padding(8.dp).fillMaxWidth()) {{\n    Text("x")\n  }}\n}}\n'
    fn = _function("Screen.kt", "kotlin", source, "Screen")
    # Only the view rows are markup: ``Column(modifier = Modifier...)``, ``Text``, ``}``.
    assert fn.markup_nloc == 3
    assert _flagged(fn, "Screen.kt", "kotlin")


def test_a_composable_parameter_does_not_make_a_builder() -> None:
    source = _KOTLIN.replace(
        "@Composable\nfun Screen(vm: Vm)", "fun Screen(vm: Vm, content: @Composable () -> Unit)"
    )
    assert _function("Screen.kt", "kotlin", source, "Screen").markup_nloc == 0


def test_a_view_parameter_does_not_make_a_swift_builder() -> None:
    source = _SWIFT.replace("var body: some View {", "func body(slot: some View) -> Int {")
    assert _function("V.swift", "swift", source, "body").markup_nloc == 0
