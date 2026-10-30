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


def _field_repo(root: Path, run_body: str) -> None:
    """``Peer``/``Stranger`` plus a ``Host`` class holding fields, its method out of line."""
    _repo(root, "  return 0;")
    (root / "lib" / "sink.h").write_text(
        "#pragma once\nclass Sink {\n public:\n  virtual ~Sink() {}\n"
        "  virtual void sendMessage(int n) = 0;\n};\n"
    )
    (root / "lib" / "host.h").write_text(
        '#pragma once\n#include <memory>\n#include "peer.h"\n#include "sink.h"\n'
        "class Host {\n public:\n  void Run();\n  int Inline() { return peer_.sendMessage(1); }\n"
        " private:\n  Peer peer_;\n  Peer* peer_ptr_;\n  Sink* sink_;\n"
        "  std::unique_ptr<Peer> owned_;\n  Host* parent_;\n};\n"
    )
    (root / "lib" / "host.cc").write_text(
        '#include "host.h"\nvoid Host::Run() {\n' + run_body + "\n}\n"
    )


def _callees(graph, caller: str) -> set[str]:
    return {
        t for s, t, d in graph.edges(data=True) if d.get("edge_type") == "calls" and s == caller
    }


class TestFieldReceivers:
    def test_an_out_of_line_body_reads_the_field_its_header_declares(self, tmp_path: Path) -> None:
        _field_repo(tmp_path, "  peer_.sendMessage(1);\n  peer_ptr_->sendMessage(2);")
        callees = _callees(_build(tmp_path), "lib/host.cc::Host::Run")
        assert "lib/peer.cc::Peer::sendMessage" in callees
        assert not any("Stranger" in t for t in callees)

    def test_an_inline_body_reads_its_own_class_field(self, tmp_path: Path) -> None:
        _field_repo(tmp_path, "")
        assert "lib/peer.cc::Peer::sendMessage" in _callees(
            _build(tmp_path), "lib/host.h::Host::Inline"
        )

    def test_an_interface_pointer_field_binds_the_pure_virtual(self, tmp_path: Path) -> None:
        """The interface declares the method and nothing defines it: that is the callee."""
        _field_repo(tmp_path, "  sink_->sendMessage(1);")
        callees = _callees(_build(tmp_path), "lib/host.cc::Host::Run")
        assert callees == {"lib/sink.h::Sink::sendMessage"}

    def test_a_smart_pointer_field_binds_what_it_holds(self, tmp_path: Path) -> None:
        _field_repo(tmp_path, "  owned_->sendMessage(1);")
        assert "lib/peer.cc::Peer::sendMessage" in _callees(
            _build(tmp_path), "lib/host.cc::Host::Run"
        )

    def test_a_name_the_pointer_field_declares_is_refused(self, tmp_path: Path) -> None:
        """``owned_.get()`` is ``unique_ptr::get``, though ``Peer`` declares ``get``."""
        _field_repo(tmp_path, "  owned_.get(1);")
        assert "lib/peer.cc::Peer::get" not in _callees(_build(tmp_path), "lib/host.cc::Host::Run")

    def test_an_auto_local_hides_the_field(self, tmp_path: Path) -> None:
        _field_repo(tmp_path, "  auto peer_ = Make();\n  peer_.sendMessage(1);")
        assert "lib/peer.cc::Peer::sendMessage" not in _callees(
            _build(tmp_path), "lib/host.cc::Host::Run"
        )

    def test_two_included_classes_of_the_name_refuse(self, tmp_path: Path) -> None:
        """Which ``Host`` the body belongs to is unsettled, so no field is read."""
        _field_repo(tmp_path, "  peer_.sendMessage(1);")
        (tmp_path / "lib" / "other.h").write_text(
            "#pragma once\nclass Host {\n Stranger peer_;\n};\n"
        )
        (tmp_path / "lib" / "host.cc").write_text(
            '#include "host.h"\n#include "other.h"\nvoid Host::Run() {\n'
            "  peer_.sendMessage(1);\n}\n"
        )
        callees = _callees(_build(tmp_path), "lib/host.cc::Host::Run")
        assert not any("sendMessage" in t for t in callees)

    def test_a_declaration_two_bodies_define_is_refused(self, tmp_path: Path) -> None:
        """Platform variants each define ``Peer::get``: which one runs is unknown."""
        _field_repo(tmp_path, "  peer_ptr_->get(1);")
        for name in ("peer-posix.cc", "peer-win32.cc"):
            (tmp_path / "lib" / name).write_text(
                '#include "peer.h"\nint Peer::get(int n) { return n; }\n'
            )
        (tmp_path / "lib" / "peer.cc").write_text(
            '#include "peer.h"\nint Peer::sendMessage(int n) { return n; }\n'
        )
        callees = _callees(_build(tmp_path), "lib/host.cc::Host::Run")
        assert not any(t.endswith("::get") for t in callees)

    def test_a_call_into_its_own_body_lands_on_no_declaration(self, tmp_path: Path) -> None:
        """``parent_->Run()`` inside ``Host::Run`` recurses; the header line is not the callee."""
        _field_repo(tmp_path, "  parent_->Run();")
        callees = _callees(_build(tmp_path), "lib/host.cc::Host::Run")
        assert "lib/host.h::Host::Run" not in callees

    def test_a_typed_local_hides_the_field(self, tmp_path: Path) -> None:
        _field_repo(tmp_path, "  Stranger peer_;\n  peer_.sendMessage(1);")
        callees = _callees(_build(tmp_path), "lib/host.cc::Host::Run")
        assert "lib/peer.cc::Stranger::sendMessage" in callees
        assert "lib/peer.cc::Peer::sendMessage" not in callees


class TestTypedCalleeOnHeaderDeclarations:
    """A typed call lands on a header declaration only where no body could be missing."""

    @staticmethod
    def _callee(symbols, decl_to_def, sym_id, caller_id, text=""):
        from types import SimpleNamespace

        from repowise.core.ingestion.call_receiver_typing.cpp import CppFieldOwners

        owner = SimpleNamespace(
            _symbols_by_id=symbols,
            _decl_to_def=decl_to_def,
            _pure_virtuals={},
            _symbol_paths_by_id={sym_id: "a.h"},
            _text_of=lambda _path: text,
        )
        return CppFieldOwners._typed_callee(owner, sym_id, caller_id)

    @staticmethod
    def _decl(sym_id):
        from types import SimpleNamespace

        return SimpleNamespace(is_declaration=True, language="cpp", start_line=1, end_line=1)

    def test_an_unpaired_non_virtual_declaration_is_no_callee(self) -> None:
        sym = "a.h::P::f"
        assert self._callee({sym: self._decl(sym)}, {}, sym, "c.cc::Run", "int f(int n);") is None

    def test_an_unpaired_pure_virtual_is_the_callee(self) -> None:
        sym = "a.h::P::f"
        text = "virtual int f(int n) = 0;"
        assert self._callee({sym: self._decl(sym)}, {}, sym, "c.cc::Run", text) == sym

    def test_a_declaration_paired_with_the_callers_own_body_is_the_caller(self) -> None:
        sym = "a.h::P::f"
        assert (
            self._callee({sym: self._decl(sym)}, {sym: "c.cc::Run"}, sym, "c.cc::Run")
            == "c.cc::Run"
        )

    def test_a_declaration_paired_with_another_body_is_kept(self) -> None:
        sym = "a.h::P::f"
        assert self._callee({sym: self._decl(sym)}, {sym: "p.cc::P::f"}, sym, "c.cc::Run") == sym

    def test_a_typed_call_onto_an_unpaired_declaration_yields_no_edge(self, tmp_path: Path) -> None:
        (tmp_path / "lone.h").write_text("#pragma once\nclass Lone {\n public:\n  int run(int n);\n};\n")
        (tmp_path / "caller.cc").write_text(
            '#include "lone.h"\nint Go() {\n  Lone* l;\n  return l->run(1);\n}\n'
        )
        assert _call_targets(_build(tmp_path), "caller.cc") == set()

    def test_a_typed_call_onto_a_paired_declaration_lands_on_the_definition(
        self, tmp_path: Path
    ) -> None:
        _repo(tmp_path, "  Peer* peer;\n  return peer->get(1);")
        assert _call_targets(_build(tmp_path), "lib/caller.cc") == {"lib/peer.cc::Peer::get"}


def _receiver_named_like_a_class_repo(root: Path, declaration: str) -> None:
    """A ``Sink`` parameter named ``buffer``, and a class ``buffer`` declaring ``flush``."""
    (root / "lib").mkdir(parents=True, exist_ok=True)
    (root / "lib" / "buffer.h").write_text(
        "#pragma once\nclass buffer {\n public:\n  " + declaration + "\n};\n"
        "class Sink {\n public:\n  void write();\n};\n"
    )
    (root / "lib" / "sink.cc").write_text('#include "lib/buffer.h"\nvoid Sink::write() {}\n')
    (root / "lib" / "caller.cc").write_text(
        '#include "lib/buffer.h"\nvoid Run(Sink& buffer) {\n  buffer.flush();\n}\n'
    )


class TestBodilessMemberNames:
    """A method name only a bodiless C++ declaration carries reaches a member call as a pure virtual only."""

    def test_a_declared_never_defined_method_gets_no_edge_from_an_unrelated_receiver(
        self, tmp_path: Path
    ) -> None:
        """``buffer`` is a ``Sink``; the receiver-name tier must not land on ``buffer::flush``."""
        _receiver_named_like_a_class_repo(tmp_path, "void flush();")
        assert _call_targets(_build(tmp_path), "lib/caller.cc") == set()

    def test_a_pure_virtual_name_is_still_admitted(self, tmp_path: Path) -> None:
        """The control: an interface pointer still binds the pure virtual nothing defines."""
        _receiver_named_like_a_class_repo(tmp_path, "virtual void flush() = 0;")
        (tmp_path / "lib" / "caller.cc").write_text(
            '#include "lib/buffer.h"\nvoid Run(buffer* out) {\n  out->flush();\n}\n'
        )
        assert _call_targets(_build(tmp_path), "lib/caller.cc") == {"lib/buffer.h::buffer::flush"}
