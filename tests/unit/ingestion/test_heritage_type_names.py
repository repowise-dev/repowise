"""Characterisation: the parent name each language's heritage extractor emits.

Every extractor answers one question — given a parent type written in source,
what is the bare type name — and each answered it its own way. This table pins
the answer per language for four spellings of the same parent: bare, with type
arguments, qualified, and both.

Every row now yields the bare name, because all fourteen extractors ask one
module for it. The rows that moved to get here are what the table was committed
to expose: type arguments survived into the parent name in six languages, a
qualifier was emitted as a parent in its own right in three, and two dropped a
qualified parent entirely.

Pascal still produces no edge, and that is not a type-name defect: its grammar
is not installed here.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser

# (language, extension, source template with {p}, parent spelling, expected
# (parent_name, kind) pairs). Templates declare one class so a failure names
# one case.
CASES: list[tuple[str, str, str, str, list[tuple[str, str]]]] = [
    # --- python ------------------------------------------------------------
    ("python", "py", "class Child({p}):\n    pass\n", "Bare", [("Bare", "extends")]),
    ("python", "py", "class Child({p}):\n    pass\n", "Gen[Arg]", [("Gen", "extends")]),
    ("python", "py", "class Child({p}):\n    pass\n", "ns.Qual", [("Qual", "extends")]),
    ("python", "py", "class Child({p}):\n    pass\n", "ns.Both[Arg]", [("Both", "extends")]),
    # --- java --------------------------------------------------------------
    ("java", "java", "class Child extends {p} {{}}\n", "Bare", [("Bare", "extends")]),
    ("java", "java", "class Child extends {p} {{}}\n", "Gen<Arg>", [("Gen", "extends")]),
    ("java", "java", "class Child extends {p} {{}}\n", "ns.Qual", [("Qual", "extends")]),
    ("java", "java", "class Child extends {p} {{}}\n", "ns.Both<Arg>", [("Both", "extends")]),
    # --- kotlin ------------------------------------------------------------
    ("kotlin", "kt", "class Child : {p}()\n", "Bare", [("Bare", "extends")]),
    ("kotlin", "kt", "class Child : {p}()\n", "Gen<Arg>", [("Gen", "extends")]),
    ("kotlin", "kt", "class Child : {p}()\n", "ns.Qual", [("Qual", "extends")]),
    ("kotlin", "kt", "class Child : {p}()\n", "ns.Both<Arg>", [("Both", "extends")]),
    # --- csharp ------------------------------------------------------------
    ("csharp", "cs", "class Child : {p} {{}}\n", "Bare", [("Bare", "extends")]),
    ("csharp", "cs", "class Child : {p} {{}}\n", "Gen<Arg>", [("Gen", "extends")]),
    ("csharp", "cs", "class Child : {p} {{}}\n", "ns.Qual", [("Qual", "extends")]),
    ("csharp", "cs", "class Child : {p} {{}}\n", "ns.Both<Arg>", [("Both", "extends")]),
    # --- cpp ---------------------------------------------------------------
    ("cpp", "cpp", "class Child : public {p} {{}};\n", "Bare", [("Bare", "extends")]),
    ("cpp", "cpp", "class Child : public {p} {{}};\n", "Gen<Arg>", [("Gen", "extends")]),
    ("cpp", "cpp", "class Child : public {p} {{}};\n", "ns::Qual", [("Qual", "extends")]),
    ("cpp", "cpp", "class Child : public {p} {{}};\n", "ns::Both<Arg>", [("Both", "extends")]),
    # --- go: struct embedding ----------------------------------------------
    # Go keeps the package qualifier on an embed: ``io.Reader`` must stay
    # ``io.Reader``, not ``Reader``, or an embed of a stdlib type binds to
    # whatever repo-local type shares the short name (and can inherit from
    # itself). Type arguments are still stripped.
    ("go", "go", "type Child struct {{\n\t{p}\n}}\n", "Bare", [("Bare", "mixin")]),
    ("go", "go", "type Child struct {{\n\t{p}\n}}\n", "Gen[Arg]", [("Gen", "mixin")]),
    ("go", "go", "type Child struct {{\n\t{p}\n}}\n", "ns.Qual", [("ns.Qual", "mixin")]),
    (
        "go",
        "go",
        "type Child struct {{\n\t{p}\n}}\n",
        "ns.Both[Arg]",
        [("ns.Both", "mixin")],
    ),
    # --- go: interface embedding -------------------------------------------
    # The same relation through a different node — the grammar wraps each
    # embed in a `type_elem`.
    ("go", "go", "type Child interface {{\n\t{p}\n}}\n", "Bare", [("Bare", "extends")]),
    ("go", "go", "type Child interface {{\n\t{p}\n}}\n", "Gen[Arg]", [("Gen", "extends")]),
    (
        "go",
        "go",
        "type Child interface {{\n\t{p}\n}}\n",
        "ns.Qual",
        [("ns.Qual", "extends")],
    ),
    (
        "go",
        "go",
        "type Child interface {{\n\t{p}\n}}\n",
        "ns.Both[Arg]",
        [("ns.Both", "extends")],
    ),
    # A type set is a generic bound, not an embed: it carries no methods.
    ("go", "go", "type Child interface {{\n\t{p}\n}}\n", "~int | string", []),
    # ...and skipping one must not cost the embed sitting beside it.
    (
        "go",
        "go",
        "type Child interface {{\n\t~int | string\n\t{p}\n}}\n",
        "Bare",
        [("Bare", "extends")],
    ),
    # --- rust --------------------------------------------------------------
    ("rust", "rs", "struct Child;\nimpl {p} for Child {{}}\n", "Bare", [("Bare", "trait_impl")]),
    (
        "rust",
        "rs",
        "struct Child;\nimpl {p} for Child {{}}\n",
        "Gen<Arg>",
        [("Gen", "trait_impl")],
    ),
    (
        "rust",
        "rs",
        "struct Child;\nimpl {p} for Child {{}}\n",
        "ns::Qual",
        [("Qual", "trait_impl")],
    ),
    (
        "rust",
        "rs",
        "struct Child;\nimpl {p} for Child {{}}\n",
        "ns::Both<Arg>",
        [("Both", "trait_impl")],
    ),
    # --- ruby: no generic syntax to mishandle ------------------------------
    ("ruby", "rb", "class Child < {p}\nend\n", "Bare", [("Bare", "extends")]),
    ("ruby", "rb", "class Child < {p}\nend\n", "Ns::Qual", [("Qual", "extends")]),
    # --- typescript --------------------------------------------------------
    ("typescript", "ts", "class Child extends {p} {{}}\n", "Bare", [("Bare", "extends")]),
    ("typescript", "ts", "class Child extends {p} {{}}\n", "Gen<Arg>", [("Gen", "extends")]),
    ("typescript", "ts", "class Child extends {p} {{}}\n", "ns.Qual", [("Qual", "extends")]),
    (
        "typescript",
        "ts",
        "class Child extends {p} {{}}\n",
        "ns.Both<Arg>",
        [("Both", "extends")],
    ),
    # --- javascript: no type arguments to mishandle -------------------------
    ("javascript", "js", "class Child extends {p} {{}}\n", "Bare", [("Bare", "extends")]),
    ("javascript", "js", "class Child extends {p} {{}}\n", "ns.Qual", [("Qual", "extends")]),
    # --- swift -------------------------------------------------------------
    ("swift", "swift", "class Child: {p} {{}}\n", "Bare", [("Bare", "extends")]),
    ("swift", "swift", "class Child: {p} {{}}\n", "Gen<Arg>", [("Gen", "extends")]),
    ("swift", "swift", "class Child: {p} {{}}\n", "Ns.Qual", [("Qual", "extends")]),
    ("swift", "swift", "class Child: {p} {{}}\n", "Ns.Both<Arg>", [("Both", "extends")]),
    # --- dart --------------------------------------------------------------
    ("dart", "dart", "class Child extends {p} {{}}\n", "Bare", [("Bare", "extends")]),
    ("dart", "dart", "class Child extends {p} {{}}\n", "Gen<Arg>", [("Gen", "extends")]),
    ("dart", "dart", "class Child extends {p} {{}}\n", "ns.Qual", [("Qual", "extends")]),
    ("dart", "dart", "class Child extends {p} {{}}\n", "ns.Both<Arg>", [("Both", "extends")]),
    # --- scala -------------------------------------------------------------
    ("scala", "scala", "class Child extends {p}\n", "Bare", [("Bare", "extends")]),
    ("scala", "scala", "class Child extends {p}\n", "Gen[Arg]", [("Gen", "extends")]),
    ("scala", "scala", "class Child extends {p}\n", "ns.Qual", [("Qual", "extends")]),
    ("scala", "scala", "class Child extends {p}\n", "ns.Both[Arg]", [("Both", "extends")]),
    # --- php ---------------------------------------------------------------
    ("php", "php", "<?php\nclass Child extends {p} {{}}\n", "Bare", [("Bare", "extends")]),
    ("php", "php", "<?php\nclass Child extends {p} {{}}\n", "\\Ns\\Qual", [("Qual", "extends")]),
]


def _parse(language: str, ext: str, source: str):
    info = FileInfo(
        path=f"probe.{ext}",
        abs_path=f"/tmp/probe.{ext}",
        language=language,
        size_bytes=len(source),
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    return ASTParser().parse_file(info, source.encode())


@pytest.mark.parametrize(
    ("language", "ext", "template", "spelling", "expected"),
    CASES,
    ids=[f"{c[0]}-{c[3]}" for c in CASES],
)
def test_parent_name_for_spelling(
    language: str,
    ext: str,
    template: str,
    spelling: str,
    expected: list[tuple[str, str]],
) -> None:
    parsed = _parse(language, ext, template.format(p=spelling))
    got = [(h.parent_name, h.kind) for h in parsed.heritage]
    assert sorted(got) == sorted(expected)
