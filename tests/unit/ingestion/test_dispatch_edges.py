"""Interface and override dispatch — both directions, end to end.

Forward: a base method gains a ``dispatches_to`` edge to each same-named
method a subtype declares, so a traversal that reaches the base reaches the
code that runs.

Reverse: a call the caller's own class cannot answer is looked for on its
ancestors, for an explicit ``self``/``this`` receiver and for the implicit
receiver of a bare call.

The tests weigh toward the refusals. A walk that links a base to an unrelated
same-named method is worse than one that links nothing.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from repowise.core.ingestion.dispatch_edges import resolve_override_dispatch
from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser

_PARSER = ASTParser()


def _build(tmp_path: Path, sources: dict[str, str], language: str):
    builder = GraphBuilder(tmp_path)
    for rel, text in sources.items():
        abs_path = tmp_path / rel
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(text, encoding="utf-8")
        info = FileInfo(
            path=rel,
            abs_path=str(abs_path),
            language=language,
            size_bytes=len(text),
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        )
        builder.add_file(_PARSER.parse_file(info, text.encode("utf-8")))
    return builder.build()


def _dispatch_pairs(graph) -> set[tuple[str, str]]:
    return {
        (u, v) for u, v, d in graph.edges(data=True) if d.get("edge_type") == "dispatches_to"
    }


def _calls_by_origin(graph, origin: str) -> set[tuple[str, str]]:
    return {
        (u, v)
        for u, v, d in graph.edges(data=True)
        if d.get("edge_type") == "calls" and d.get("resolution_origin") == origin
    }


_PY_HIERARCHY = {
    "base.py": (
        "class Handler:\n"
        "    def handle(self):\n"
        "        return self.finish()\n"
        "    def finish(self):\n"
        "        return 1\n"
    ),
    "impl.py": (
        "from base import Handler\n\n\n"
        "class JsonHandler(Handler):\n"
        "    def handle(self):\n"
        "        return 2\n"
    ),
}


def test_a_subtype_method_answers_for_the_base_it_overrides(tmp_path: Path) -> None:
    graph = _build(tmp_path, _PY_HIERARCHY, "python")
    assert ("base.py::Handler::handle", "impl.py::JsonHandler::handle") in _dispatch_pairs(graph)


def test_a_base_method_no_subtype_declares_gains_nothing(tmp_path: Path) -> None:
    graph = _build(tmp_path, _PY_HIERARCHY, "python")
    assert not [
        pair for pair in _dispatch_pairs(graph) if pair[0].endswith("Handler::finish")
    ]


def test_two_classes_sharing_a_method_name_but_no_heritage_are_not_linked(
    tmp_path: Path,
) -> None:
    graph = _build(
        tmp_path,
        {
            "a.py": "class Alpha:\n    def run(self):\n        return 1\n",
            "b.py": "class Beta:\n    def run(self):\n        return 2\n",
        },
        "python",
    )
    assert not _dispatch_pairs(graph)


def test_a_constructor_is_never_a_dispatch_target(tmp_path: Path) -> None:
    """Calling the base's ``__init__`` runs the base's, never the subtype's."""
    graph = _build(
        tmp_path,
        {
            "base.py": "class Base:\n    def __init__(self):\n        self.x = 1\n",
            "impl.py": (
                "from base import Base\n\n\n"
                "class Child(Base):\n"
                "    def __init__(self):\n"
                "        self.x = 2\n"
            ),
        },
        "python",
    )
    assert not _dispatch_pairs(graph)


def test_a_grandchild_answers_for_the_root_it_never_names(tmp_path: Path) -> None:
    graph = _build(
        tmp_path,
        {
            "a.py": "class Root:\n    def run(self):\n        return 0\n",
            "b.py": "from a import Root\n\n\nclass Mid(Root):\n    pass\n",
            "c.py": "from b import Mid\n\n\nclass Leaf(Mid):\n    def run(self):\n        return 1\n",
        },
        "python",
    )
    assert ("a.py::Root::run", "c.py::Leaf::run") in _dispatch_pairs(graph)


def test_an_unregistered_language_gains_no_dispatch_edges(tmp_path: Path) -> None:
    graph = _build(tmp_path, _PY_HIERARCHY, "python")
    assert resolve_override_dispatch(graph, languages=frozenset()) == 0


def test_a_base_with_more_implementations_than_the_cap_is_refused_whole(
    tmp_path: Path,
) -> None:
    sources = {"base.py": "class Base:\n    def run(self):\n        return 0\n"}
    for n in range(4):
        sources[f"i{n}.py"] = (
            f"from base import Base\n\n\nclass Impl{n}(Base):\n    def run(self):\n        return {n}\n"
        )
    graph = _build(tmp_path, sources, "python")
    before = len(_dispatch_pairs(graph))
    assert before == 4
    for u, v in list(_dispatch_pairs(graph)):
        graph.remove_edge(u, v)
    assert resolve_override_dispatch(graph, max_implementations=3) == 0


def test_a_private_implementation_overrides_nothing(tmp_path: Path) -> None:
    """Four of six wrong C# edges were a `private` test helper sharing a name
    with a `virtual` base. Private is not dispatched to."""
    graph = _build(
        tmp_path,
        {
            "Base.cs": (
                "namespace App;\n\npublic class Base\n{\n"
                "    public virtual void Run() { }\n}\n"
            ),
            "Child.cs": (
                "namespace App;\n\npublic class Child : Base\n{\n"
                "    private void Run(int times) { }\n}\n"
            ),
        },
        "csharp",
    )
    assert not _dispatch_pairs(graph)


def test_a_private_python_method_is_still_a_dispatch_target(tmp_path: Path) -> None:
    """Python has no enforced private, so the same refusal there would drop
    real overrides."""
    graph = _build(
        tmp_path,
        {
            "base.py": "class Base:\n    def _run(self):\n        return 0\n",
            "impl.py": (
                "from base import Base\n\n\nclass Child(Base):\n    def _run(self):\n        return 1\n"
            ),
        },
        "python",
    )
    assert ("base.py::Base::_run", "impl.py::Child::_run") in _dispatch_pairs(graph)


# --------------------------------------------------------------------------
# Reverse: the call the caller's own class cannot answer
# --------------------------------------------------------------------------


def test_a_self_call_reaches_a_method_the_base_declares(tmp_path: Path) -> None:
    graph = _build(
        tmp_path,
        {
            "base.py": "class Base:\n    def helper(self):\n        return 1\n",
            "impl.py": (
                "from base import Base\n\n\n"
                "class Child(Base):\n"
                "    def run(self):\n"
                "        return self.helper()\n"
            ),
        },
        "python",
    )
    assert ("impl.py::Child::run", "base.py::Base::helper") in _calls_by_origin(
        graph, "self_inherited"
    )


def test_two_ancestors_declaring_the_name_resolve_to_neither(tmp_path: Path) -> None:
    """Ambiguity is terminal: guessing a branch mints an edge to a class the
    call may never reach."""
    graph = _build(
        tmp_path,
        {
            "l.py": "class Left:\n    def helper(self):\n        return 1\n",
            "r.py": "class Right:\n    def helper(self):\n        return 2\n",
            "impl.py": (
                "from l import Left\n"
                "from r import Right\n\n\n"
                "class Child(Left, Right):\n"
                "    def run(self):\n"
                "        return self.helper()\n"
            ),
        },
        "python",
    )
    assert not _calls_by_origin(graph, "self_inherited")


def test_a_self_call_the_caller_own_class_declares_keeps_its_own_origin(
    tmp_path: Path,
) -> None:
    """The inherited tier is asked last, so it cannot displace ``self_scope``."""
    graph = _build(
        tmp_path,
        {
            "base.py": "class Base:\n    def helper(self):\n        return 1\n",
            "impl.py": (
                "from base import Base\n\n\n"
                "class Child(Base):\n"
                "    def helper(self):\n"
                "        return 3\n"
                "    def run(self):\n"
                "        return self.helper()\n"
            ),
        },
        "python",
    )
    assert not _calls_by_origin(graph, "self_inherited")
    assert ("impl.py::Child::run", "impl.py::Child::helper") in _calls_by_origin(
        graph, "self_scope"
    )


def test_a_bare_call_reaches_an_inherited_method_in_an_implicit_receiver_language(
    tmp_path: Path,
) -> None:
    # A decoy declares the same name, so the global-unique guess cannot answer
    # first. That is the condition under which the real population misses at
    # all — where an earlier tier answers, this one is never reached.
    graph = _build(
        tmp_path,
        {
            "Base.cs": (
                "namespace App.Core;\n\n"
                "public class Base\n{\n"
                "    protected int Helper() { return 1; }\n"
                "}\n"
            ),
            # Two hops, so the file declaring Helper is not one this file
            # imports — where it is, the merged-import tier answers first.
            "Mid.cs": (
                "using App.Core;\n\nnamespace App.Mid;\n\npublic class Mid : Base\n{\n}\n"
            ),
            "Child.cs": (
                "using App.Mid;\n\n"
                "namespace App.Web;\n\n"
                "public class Child : Mid\n{\n"
                "    public int Run() { return Helper(); }\n"
                "}\n"
            ),
            "Decoy.cs": (
                "namespace App.Other;\n\n"
                "public class Decoy\n{\n"
                "    private int Helper() { return 9; }\n"
                "}\n"
            ),
        },
        "csharp",
    )
    assert ("Child.cs::Child::Run", "Base.cs::Base::Helper") in _calls_by_origin(
        graph, "enclosing_inherited"
    )


def test_a_python_bare_call_is_not_read_as_an_implicit_receiver(tmp_path: Path) -> None:
    """Python spells the receiver, so a bare ``helper()`` is a free function
    and binding it to the class hierarchy would be wrong."""
    graph = _build(
        tmp_path,
        {
            "base.py": "class Base:\n    def helper(self):\n        return 1\n",
            "impl.py": (
                "from base import Base\n\n\n"
                "class Child(Base):\n"
                "    def run(self):\n"
                "        return helper()\n"
            ),
        },
        "python",
    )
    assert not _calls_by_origin(graph, "enclosing_inherited")
