"""A C++ ``obj->method()`` must resolve against the receiver's declared type.

``_CPP_STRATEGIES`` carried an empty ``member`` and ``member_fallback``, so a
call with a receiver reached no strategy at all: measured over leveldb and
aria2, C++ resolved 13 of 22,112 receiver-carrying sites. Six languages already
resolve exactly that shape by reading the receiver's declaration, and the shape
C++ writes it in is the C family's ``T name`` plus ``::``, a pointer star, and
lowercase heads.

The wrapper case is where the two spellings disagree. ``shared_ptr<Foo> p``
makes ``p->m()`` a call on ``Foo`` and ``p.m()`` a call on the pointer, and the
grammar query captures no operator to tell them apart. The names a dot call can
reach are closed by the language, so those are refused instead.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder


def _build(repo: Path):
    traverser = FileTraverser(repo)
    parser = ASTParser()
    builder = GraphBuilder(repo_path=repo)
    for fi in traverser.traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    return builder.build()


def _call_targets(graph, caller_file: str) -> set[str]:
    return {
        t
        for s, t, d in graph.edges(data=True)
        if d.get("edge_type") == "calls" and str(s).startswith(caller_file + "::")
    }


def _repo(root: Path, caller_body: str, *, extra: str = "") -> None:
    """One class with a method, one decoy class declaring the same name."""
    (root / "lib").mkdir(parents=True, exist_ok=True)
    (root / "lib" / "peer.h").write_text(
        "#pragma once\n"
        "class Peer {\n public:\n  int sendMessage(int n);\n  int get(int n);\n};\n"
        "class Stranger {\n public:\n  int sendMessage(int n);\n};\n"
    )
    (root / "lib" / "peer.cc").write_text(
        '#include "lib/peer.h"\n'
        "int Peer::sendMessage(int n) { return n; }\n"
        "int Peer::get(int n) { return n; }\n"
        "int Stranger::sendMessage(int n) { return n + 1; }\n"
    )
    (root / "lib" / "caller.cc").write_text(
        '#include "lib/peer.h"\n' + extra + "int Run() {\n" + caller_body + "\n}\n"
    )


class TestTypedReceiverResolves:
    def test_a_pointer_receiver_binds_to_its_declared_type(self, tmp_path: Path) -> None:
        _repo(tmp_path, "  Peer* peer;\n  return peer->sendMessage(1);")
        assert any(t.endswith("Peer::sendMessage") for t in _call_targets(_build(tmp_path), "lib/caller.cc"))

    def test_a_value_receiver_binds_too(self, tmp_path: Path) -> None:
        _repo(tmp_path, "  Peer peer;\n  return peer.sendMessage(1);")
        assert any(t.endswith("Peer::sendMessage") for t in _call_targets(_build(tmp_path), "lib/caller.cc"))

    def test_it_does_not_answer_with_another_class_declaring_the_name(
        self, tmp_path: Path
    ) -> None:
        """The decoy control: ``Stranger`` declares ``sendMessage`` as well."""
        _repo(tmp_path, "  Peer* peer;\n  return peer->sendMessage(1);")
        assert not any("Stranger" in t for t in _call_targets(_build(tmp_path), "lib/caller.cc"))

    def test_an_undeclared_receiver_resolves_nothing(self, tmp_path: Path) -> None:
        """The control the mechanism has to fail: nothing types ``peer`` here."""
        _repo(tmp_path, "  return peer->sendMessage(1);")
        assert not any(t.endswith("Peer::sendMessage") for t in _call_targets(_build(tmp_path), "lib/caller.cc"))


class TestSmartPointerReceivers:
    def test_a_shared_ptr_binds_to_what_it_holds(self, tmp_path: Path) -> None:
        """The peer's normaliser strips the whole ``<...>`` and loses ``Peer``."""
        _repo(tmp_path, "  std::shared_ptr<Peer> peer;\n  return peer->sendMessage(1);")
        assert any(t.endswith("Peer::sendMessage") for t in _call_targets(_build(tmp_path), "lib/caller.cc"))

    def test_a_unique_ptr_binds_past_its_deleter(self, tmp_path: Path) -> None:
        _repo(tmp_path, "  std::unique_ptr<Peer, D> peer;\n  return peer->sendMessage(1);")
        assert any(t.endswith("Peer::sendMessage") for t in _call_targets(_build(tmp_path), "lib/caller.cc"))

    def test_a_name_the_pointer_itself_declares_is_refused(self, tmp_path: Path) -> None:
        """``peer.get()`` is ``shared_ptr::get``, and ``Peer`` declares a ``get`` too.

        The operator is not captured, so the dot call and the arrow call arrive
        identical. Refusing the closed member set is what keeps this off
        ``Peer::get``; it costs the arrow calls that really did mean one.
        """
        _repo(tmp_path, "  std::shared_ptr<Peer> peer;\n  return peer->get(1);")
        assert not any(t.endswith("Peer::get") for t in _call_targets(_build(tmp_path), "lib/caller.cc"))

    def test_the_refusal_does_not_reach_a_plainly_declared_receiver(
        self, tmp_path: Path
    ) -> None:
        """The control the refusal has to fail: ``Peer* peer`` unwraps nothing."""
        _repo(tmp_path, "  Peer* peer;\n  return peer->get(1);")
        assert any(t.endswith("Peer::get") for t in _call_targets(_build(tmp_path), "lib/caller.cc"))

    def test_a_container_receiver_is_not_read_as_its_element(self, tmp_path: Path) -> None:
        """``items.sendMessage()`` is not ``Peer::sendMessage`` on a vector."""
        _repo(tmp_path, "  std::vector<Peer> items;\n  return items.sendMessage(1);")
        assert not any(t.endswith("Peer::sendMessage") for t in _call_targets(_build(tmp_path), "lib/caller.cc"))
