"""large_method counts the continuation rows of a builder chain with literal arguments as data."""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.biomarkers import FileContext
from repowise.core.analysis.health.biomarkers.large_method import LargeMethodDetector
from repowise.core.analysis.health.complexity.models import FunctionComplexity
from repowise.core.analysis.health.complexity.walker import walk_file

_R = range(24)


def _links(template: str) -> str:
    return "\n".join(template.format(i=i) for i in _R)


# Each function branches twice (CCN 3) before one 24-link builder chain of 72 rows.
_CASES = {
    "rust": (
        "m.rs",
        "fn build(a: bool, b: bool) -> Command {\n  if a { println!(\"a\"); }\n"
        '  if b { println!("b"); }\n  Command::new("app")\n'
        + _links(
            '    .arg(\n      Arg::new("opt{i}").long("opt{i}").action(ArgAction::SetTrue)\n'
            '        .help("Option {i}."))'
        )
        + "\n}\n",
    ),
    "javascript": (
        "m.js",
        "function build(a, b) {\n  if (a) { a++; }\n  if (b) { b++; }\n  return program\n"
        + _links('    .option(\n      "--opt{i}",\n      "Option {i}", false)')
        + ";\n}\n",
    ),
    "typescript": (
        "m.ts",
        "function build(a: number, b: number) {\n  if (a) { a++; }\n  if (b) { b++; }\n  return program\n"
        + _links('    .option(\n      "--opt{i}",\n      "Option {i}", Kind.Flag)')
        + ";\n}\n",
    ),
    "java": (
        "M.java",
        "class M {\n  Options build(int a, int b) {\n    if (a > 0) { a++; }\n    if (b > 0) { b++; }\n"
        "    return Options.builder()\n"
        + _links('      .add(\n        Option.of("opt{i}").desc("Option {i}"),\n        {i})')
        + "\n      .build();\n  }\n}\n",
    ),
    "kotlin": (
        "M.kt",
        "fun build(a: Int, b: Int): Options {\n  if (a > 0) { println(a) }\n  if (b > 0) { println(b) }\n"
        "  return Options.builder()\n"
        + _links('    .add(\n      Option.of("opt{i}").desc("Option {i}"),\n      {i})')
        + "\n    .build()\n}\n",
    ),
    "python": (
        "m.py",
        "def build(a, b):\n    if a:\n        a += 1\n    if b:\n        b += 1\n    return (\n"
        "        Parser(\"app\")\n"
        + _links('        .option(\n            "--opt{i}",\n            help="Option {i}")')
        + "\n    )\n",
    ),
    "csharp": (
        "M.cs",
        "class M {\n  Options Build(int a, int b) {\n    if (a > 0) { a++; }\n    if (b > 0) { b++; }\n"
        "    return Options.Builder()\n"
        + _links('      .Add(\n        Option.Of("opt{i}").Desc("Option {i}"),\n        {i})')
        + "\n      .Build();\n  }\n}\n",
    ),
    "go": (
        "m.go",
        "package m\nfunc build(a, b int) *Cmd {\n  if a > 0 { a++ }\n  if b > 0 { b++ }\n"
        '  return NewCmd("app").\n'
        + _links('    Flag(\n      "opt{i}",\n      "Option {i}").')
        + "\n    Done()\n}\n",
    ),
    "ruby": (
        "m.rb",
        "def build(a, b)\n  if a\n    a += 1\n  end\n  if b\n    b += 1\n  end\n  Parser.new(\"app\")\n"
        + _links('    .option(\n      "--opt{i}",\n      "Option {i}")')
        + "\nend\n",
    ),
    "php": (
        "m.php",
        "<?php\nfunction build($a, $b) {\n  if ($a) { $a++; }\n  if ($b) { $b++; }\n"
        "  return $this->builder()\n"
        + _links("    ->option(\n      'opt{i}',\n      'Option {i}')")
        + ";\n}\n",
    ),
    "scala": (
        "M.scala",
        "object M {\n  def build(a: Int, b: Int): Parser = {\n    if (a > 0) { println(a) }\n"
        "    if (b > 0) { println(b) }\n    Parser(\"app\")\n"
        + _links('      .opt(\n        "opt{i}",\n        "Option {i}")')
        + "\n  }\n}\n",
    ),
}


def _only_function(path: str, language: str, source: str) -> FunctionComplexity:
    [fn] = walk_file(path, language, source.encode()).functions
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
def test_builder_chain_rows_are_data(language: str) -> None:
    path, source = _CASES[language]
    fn = _only_function(path, language, source)
    assert fn.nloc >= 75
    assert fn.markup_nloc >= 70
    assert not _flagged(fn, path, language)


def _js(links: str) -> FunctionComplexity:
    source = (
        "function f(a, b) {\n  if (a) { a++; }\n  if (b) { b++; }\n"
        f"  return items\n{links};\n}}\n"
    )
    return _only_function("m.js", "javascript", source)


def test_a_chain_of_callbacks_stays_code() -> None:
    fn = _js(_links("    .map((x) => x + {i})\n    .filter((x) => x > {i})\n    .sort()"))
    assert fn.markup_nloc == 0
    assert _flagged(fn, "m.js", "javascript")


def test_a_chain_without_literal_arguments_stays_code() -> None:
    fn = _js(_links("    .then(step{i})\n    .catch(fail{i})\n    .finally(done)"))
    assert fn.markup_nloc == 0
    assert _flagged(fn, "m.js", "javascript")


def test_a_computing_argument_keeps_its_rows_code() -> None:
    links = (
        '    .option("--a", "A")\n'
        '    .option("--b", compute(level))\n'
        '    .option("--c", "C", level + 1)\n'
        "    .option(VERSION.trim())\n" + _links('    .option("--d{i}", "D")')
    )
    # ``compute(level)``, ``level + 1`` and ``.trim()`` are code; ``--a`` and ``--d*`` rows are data.
    assert _js(links).markup_nloc == 25


def test_a_short_chain_is_left_alone() -> None:
    links = '    .option("--a", "A")\n    .option("--b", "B")'
    assert _js(links).markup_nloc == 0


def test_a_transform_chain_with_a_callback_stays_code() -> None:
    source = (
        "fun f(a: Int, p: String): String {\n  if (a > 0) { println(a) }\n  if (a > 2) { println(a) }\n"
        '  return p\n    .replace("\\\\", "/")\n    .substringAfter(":")\n    .removePrefix("/")\n'
        '    .prefixIfNot("**/")\n    .also { log(it) }\n}\n'
    )
    assert _only_function("M.kt", "kotlin", source).markup_nloc == 0


def test_a_rust_option_chain_with_a_closure_stays_code() -> None:
    source = (
        "fn f(m: &Matches) -> u32 {\n  m\n    .get_one::<String>(\"tabs\")\n    .map(String::from)\n"
        '    .and_then(|t| t.parse().ok())\n    .unwrap_or(4)\n}\n'
    )
    assert _only_function("m.rs", "rust", source).markup_nloc == 0


_FLUENT = [
    (
        "m.js",
        "javascript",
        "function f(s) {\n",
        '  return s.replace("a", "b").replace("c", "d")\n    .replace("e", "f")\n'
        "    .trim()\n    .toLowerCase();\n}\n",
    ),
    (
        "m.py",
        "python",
        "def f(df):\n",
        '    return (df.groupby("a")\n        .agg("sum")\n        .rename(columns={"a": "b"})\n'
        '        .sort_values("b")\n        .reset_index(drop=True))\n',
    ),
    (
        "M.java",
        "java",
        "class M {\n  void f(String x) {\n",
        '    assertThat(x)\n        .isEqualTo("a")\n        .hasSize(3)\n        .contains("b")\n'
        '        .doesNotContain("c");\n  }\n}\n',
    ),
]


@pytest.mark.parametrize(("path", "language", "head", "chain"), _FLUENT)
def test_a_short_fluent_transform_or_assertion_stays_code(
    path: str, language: str, head: str, chain: str
) -> None:
    assert _only_function(path, language, head + chain).markup_nloc == 0


def test_a_plain_call_with_many_arguments_is_no_chain() -> None:
    source = (
        "char *f(char *s, struct c *c) {\n  s = catfmt(s,\n"
        + _links('    " k{i}=%d", get(c, {i}),')
        + '\n    " end");\n  return s;\n}\n'
    )
    assert _only_function("m.c", "c", source).markup_nloc == 0


def test_a_ruby_chain_handed_a_symbol_proc_stays_code() -> None:
    source = (
        "def f(a, b)\n  if a\n    a += 1\n  end\n  if b\n    b += 1\n  end\n  items\n"
        + _links('    .where("k{i}")')
        + "\n    .map(&:name)\nend\n"
    )
    assert _only_function("m.rb", "ruby", source).markup_nloc == 0
