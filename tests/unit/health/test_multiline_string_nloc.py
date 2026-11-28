"""large_method counts a multi-line string's continuation lines as data, not code."""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.biomarkers import FileContext
from repowise.core.analysis.health.biomarkers.large_method import LargeMethodDetector
from repowise.core.analysis.health.complexity.models import FunctionComplexity
from repowise.core.analysis.health.complexity.walker import walk_file

_TEXT = "\n".join(f"    line {i} of the template" for i in range(70))
_C_TEXT = "\n".join(f'    "line {i}\\n"' for i in range(70))

# Each function branches twice (CCN 3) around one 70-line string literal.
_CASES = {
    "python": (
        "m.py",
        "def render(a, b):\n    if a:\n        b += 1\n    if b:\n        a += 1\n"
        f'    q = """\n{_TEXT}\n    """\n    return q\n',
    ),
    "javascript": (
        "m.js",
        "function render(a, b) {\n  if (a) { b++; }\n  if (b) { a++; }\n"
        f"  const q = `\n{_TEXT}\n  ${{a.b}}\n`;\n  return q;\n}}\n",
    ),
    "typescript": (
        "m.ts",
        "function render(a: number, b: number) {\n  if (a) { b++; }\n  if (b) { a++; }\n"
        f"  const q = `\n{_TEXT}\n`;\n  return q;\n}}\n",
    ),
    "java": (
        "M.java",
        "class M {\n  String render(int a, int b) {\n    if (a > 0) { b++; }\n"
        f'    if (b > 0) {{ a++; }}\n    String q = """\n{_TEXT}\n    """;\n    return q;\n  }}\n}}\n',
    ),
    "kotlin": (
        "M.kt",
        "fun render(a: Int, b: Int): String {\n  if (a > 0) { println(a) }\n"
        f'  if (b > 0) {{ println(b) }}\n  val q = """\n{_TEXT}\n  """.trimIndent()\n  return q\n}}\n',
    ),
    "go": (
        "m.go",
        "package m\nfunc render(a, b int) string {\n  if a > 0 { b++ }\n  if b > 0 { a++ }\n"
        f"  q := `\n{_TEXT}\n`\n  return q\n}}\n",
    ),
    "rust": (
        "m.rs",
        'fn render(a: i32, b: i32) -> &\'static str {\n  if a > 0 { println!("a"); }\n'
        f'  if b > 0 {{ println!("b"); }}\n  let q = r#"\n{_TEXT}\n"#;\n  q\n}}\n',
    ),
    "c": (
        "m.c",
        "const char *render(int a, int b) {\n  if (a) { b++; }\n  if (b) { a++; }\n"
        f"  const char *q =\n{_C_TEXT};\n  return q;\n}}\n",
    ),
    "cpp": (
        "m.cpp",
        "const char *render(int a, int b) {\n  if (a) { b++; }\n  if (b) { a++; }\n"
        f'  auto q = R"(\n{_TEXT}\n)";\n  return q;\n}}\n',
    ),
    "csharp": (
        "M.cs",
        "class M {\n  string Render(int a, int b) {\n    if (a > 0) { b++; }\n"
        f'    if (b > 0) {{ a++; }}\n    var q = """\n{_TEXT}\n    """;\n    return q;\n  }}\n}}\n',
    ),
    "swift": (
        "M.swift",
        "func render(a: Int, b: Int) -> String {\n  if a > 0 { print(a) }\n"
        f'  if b > 0 {{ print(b) }}\n  let q = """\n{_TEXT}\n  """\n  return q\n}}\n',
    ),
    "ruby": (
        "m.rb",
        "def render(a, b)\n  if a\n    b += 1\n  end\n  if b\n    a += 1\n  end\n"
        f"  q = <<~SQL\n{_TEXT}\n  SQL\n  q\nend\n",
    ),
    "php": (
        "m.php",
        "<?php\nfunction render($a, $b) {\n  if ($a) { $b++; }\n  if ($b) { $a++; }\n"
        f"  $q = <<<SQL\n{_TEXT}\n  SQL;\n  return $q;\n}}\n",
    ),
    "scala": (
        "M.scala",
        "object M {\n  def render(a: Int, b: Int): String = {\n    if (a > 0) { println(a) }\n"
        f'    if (b > 0) {{ println(b) }}\n    val q = """\n{_TEXT}\n    """\n    q\n  }}\n}}\n',
    ),
    "shell": (
        "m.sh",
        'render() {\n  if [ "$1" ]; then\n    echo a\n  fi\n  if [ "$2" ]; then\n    echo b\n  fi\n'
        f"  cat <<EOF\n{_TEXT}\nEOF\n}}\n",
    ),
}


def _only_function(path: str, language: str, source: str) -> FunctionComplexity:
    [fn] = walk_file(path, language, source.encode()).functions
    return fn


def _ctx(fn: FunctionComplexity, path: str, language: str) -> FileContext:
    return FileContext(
        file_path=path,
        language=language,
        nloc=fn.nloc,
        has_test_file=False,
        module=None,
        all_functions=(fn,),
    )


@pytest.mark.parametrize("language", sorted(_CASES))
def test_string_continuation_lines_are_data(language: str) -> None:
    path, source = _CASES[language]
    fn = _only_function(path, language, source)
    assert fn.nloc >= 75
    assert fn.markup_nloc >= 70
    assert LargeMethodDetector().detect(_ctx(fn, path, language)) == []


def test_interpolated_calls_keep_their_lines_code() -> None:
    rows = "\n".join(f"    {{fetch(row_{i})}}" for i in range(70))
    source = f'def render(a, b):\n    if a:\n        b += 1\n    if b:\n        a += 1\n    q = f"""\n{rows}\n    """\n    return q\n'
    fn = _only_function("m.py", "python", source)
    # Only the closing quote line is data; every ``{fetch(...)}`` row is a call.
    assert fn.markup_nloc == 1
    [finding] = LargeMethodDetector().detect(_ctx(fn, "m.py", "python"))
    assert finding.details["markup_nloc"] == 1


def test_names_in_interpolation_stay_data() -> None:
    rows = "\n".join(f"    {{row_{i}}} and {{item.name}}" for i in range(70))
    source = f'def render(a, b):\n    if a:\n        b += 1\n    if b:\n        a += 1\n    q = f"""\n{rows}\n    """\n    return q\n'
    assert _only_function("m.py", "python", source).markup_nloc == 71


def test_long_logic_beside_a_string_is_still_flagged() -> None:
    logic = "\n".join(f"    b = step(b, {i})" for i in range(76))
    source = (
        "def render(a, b):\n    if a:\n        b += 1\n    if b:\n        a += 1\n"
        f'{logic}\n    q = """\n{_TEXT}\n    """\n    return q\n'
    )
    fn = _only_function("m.py", "python", source)
    [finding] = LargeMethodDetector().detect(_ctx(fn, "m.py", "python"))
    assert finding.reason.startswith("render is 82 lines of code besides 71 of markup and data")


def test_a_docstring_is_not_subtracted_twice() -> None:
    doc = "\n".join(f"    doc line {i}" for i in range(70))
    source = f'def render(a):\n    """\n{doc}\n    """\n    if a:\n        return 1\n    return 2\n'
    assert _only_function("m.py", "python", source).markup_nloc == 0


def test_code_sharing_the_closing_line_keeps_it_code() -> None:
    source = 'def render(a):\n    run("""\n    a\n    b\n    """.format(a))\n'
    # ``a`` and ``b`` are data; the opening and ``""".format(a))`` lines are code.
    assert _only_function("m.py", "python", source).markup_nloc == 2
