"""A Kotlin call through a ``typealias`` of a repo class reaches that class (#3760).

``Repo()`` where ``typealias Repo = DefaultRepository`` constructs
``DefaultRepository``. The alias is followed from its own file, the way a call
written there would resolve, and only a repo class counts: a function type, a
primitive or a qualified external target still gives no callee (see
``test_kotlin_multiplatform_calls.py`` for ``actual typealias``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

_SRC = "src/main/kotlin/"
FILES = {
    _SRC + "a/Repository.kt": (
        "package a\n\n"
        "class DefaultRepository {\n    fun load(): Int = 0\n}\n\n"
        "typealias Repo = DefaultRepository\n\n"
        "class Holder<T>\n\n"
        "typealias Repos = Holder<String>\n\n"
        "typealias Handler = (String) -> Unit\n\n"
        "typealias Fd = Int\n\n"
        "fun sameFile() = Repo()\n"
    ),
    _SRC + "a/Use.kt": (
        "package a\n\n"
        "fun direct() = DefaultRepository()\n\n"
        "fun samePackage() = Repo()\n\n"
        "fun generic() = Repos()\n\n"
        "fun functionType() = Handler()\n\n"
        "fun primitive() = Fd()\n"
    ),
    _SRC + "b/Imported.kt": "package b\n\nimport a.Repo\n\nfun imported() = Repo()\n",
}

_REPOSITORY = _SRC + "a/Repository.kt"


@pytest.fixture(scope="module")
def calls(tmp_path_factory: pytest.TempPathFactory) -> dict[str, set[str]]:
    repo = tmp_path_factory.mktemp("alias")
    for rel, text in FILES.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    parser = ASTParser()
    builder = GraphBuilder(repo_path=repo)
    for fi in FileTraverser(repo).traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    out: dict[str, set[str]] = {}
    for caller, callee, data in builder.build().edges(data=True):
        if data.get("edge_type") == "calls":
            out.setdefault(caller.rsplit("::", 1)[-1], set()).add(callee)
    return out


@pytest.mark.parametrize("caller", ["direct", "sameFile", "samePackage", "imported"])
def test_a_call_through_an_alias_reaches_the_class(calls: dict[str, set[str]], caller: str) -> None:
    assert calls[caller] == {_REPOSITORY + "::DefaultRepository"}


def test_a_generic_alias_reaches_its_class(calls: dict[str, set[str]]) -> None:
    assert calls["generic"] == {_REPOSITORY + "::Holder"}


@pytest.mark.parametrize("caller", ["functionType", "primitive"])
def test_an_alias_of_no_repo_class_has_no_callee(calls: dict[str, set[str]], caller: str) -> None:
    assert caller not in calls


def test_the_alias_signature_carries_its_target(tmp_path: Path) -> None:
    path = tmp_path / "A.kt"
    path.write_text("package a\n\ntypealias Repos = Holder<String>\n")
    (fi,) = FileTraverser(tmp_path).traverse()
    parsed = ASTParser().parse_file(fi, path.read_bytes())

    (alias,) = [s for s in parsed.symbols if s.kind == "type_alias"]
    assert alias.signature == "typealias Repos = Holder<String>"
