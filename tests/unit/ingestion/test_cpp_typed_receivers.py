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

import pytest

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


_FACTORIES = (
    "Peer* MakePeer(int n);\n"
    "Stranger& TheStranger();\n"
    "template <typename Stranger> Stranger Wrap(Stranger s) { return s; }\n"
    "inline auto Trailing(int n) -> Peer* { return nullptr; }\n"
    "inline auto Fresh() -> Peer* { return nullptr; }\n"
    "inline auto Deduced(int n) { return Peer(); }\n"
    "using PeerAlias = Peer;\n"
    "inline auto Aliased(int n) -> PeerAlias* { return nullptr; }\n"
)


def _factory_repo(root: Path, caller_body: str) -> None:
    """``_repo`` plus free functions whose return types differ in how they are written."""
    _repo(root, caller_body, extra=_FACTORIES)
    with (root / "lib" / "peer.cc").open("a") as source:
        source.write("Peer* MakePeer(int n) { return nullptr; }\n")


class TestLocalDeclarationForms:
    """A local typed by how C++ writes it: built in place, walked by a range-for, or ``auto``."""

    @pytest.mark.parametrize(
        "body",
        [
            "  Peer peer(1, 2);\n  return peer.sendMessage(1);",
            "  for (Peer* peer : peers) peer->sendMessage(1);\n  return 0;",
            "  for (const Peer& peer : peers) peer.sendMessage(1);\n  return 0;",
            "  auto peer = new Peer(1, 2);\n  return peer->sendMessage(1);",
            "  auto peer = Peer{};\n  return peer.sendMessage(1);",
            "  auto peer = Peer();\n  return peer.sendMessage(1);",
            "  auto peer = Peer(1, 2);\n  return peer.sendMessage(1);",
            "  auto peer = std::unique_ptr<Peer>(new Peer);\n  return peer->sendMessage(1);",
        ],
    )
    def test_a_constructed_local_binds_to_its_type(self, tmp_path: Path, body: str) -> None:
        _repo(tmp_path, body)
        targets = _call_targets(_build(tmp_path), "lib/caller.cc")
        assert "lib/peer.cc::Peer::sendMessage" in targets
        assert not any("Stranger" in t for t in targets)

    def test_a_function_declaration_types_nothing(self, tmp_path: Path) -> None:
        """``Peer peer(int);`` declares a function: the vexing parse."""
        _repo(tmp_path, "  Peer peer(int);\n  return peer.sendMessage(1);")
        assert not any("sendMessage" in t for t in _call_targets(_build(tmp_path), "lib/caller.cc"))

    @pytest.mark.parametrize(
        "body",
        [
            "  auto peer = MakePeer(1);\n  return peer->sendMessage(1);",
            "  auto* peer = Trailing(1);\n  return peer->sendMessage(1);",
            "  auto peer = Aliased(1);\n  return peer->sendMessage(1);",
            "  auto peer = Fresh();\n  return peer->sendMessage(1);",
        ],
    )
    def test_an_auto_local_takes_the_return_type_the_callee_declares(
        self, tmp_path: Path, body: str
    ) -> None:
        """The header declaration carries no return type; its definition does."""
        _factory_repo(tmp_path, body)
        assert "lib/peer.cc::Peer::sendMessage" in _call_targets(_build(tmp_path), "lib/caller.cc")

    @pytest.mark.parametrize(
        "body",
        [
            # No definition writes what it returns.
            "  auto& s = TheStranger();\n  return s.sendMessage(1);",
            # A template parameter is no class, even one spelled like a class.
            "  auto s = Wrap(1);\n  return s.sendMessage(1);",
            # A deduced return type is not written at all.
            "  auto peer = Deduced(1);\n  return peer.sendMessage(1);",
        ],
    )
    def test_an_auto_local_with_no_written_return_type_binds_nothing(
        self, tmp_path: Path, body: str
    ) -> None:
        _factory_repo(tmp_path, body)
        assert not any("sendMessage" in t for t in _call_targets(_build(tmp_path), "lib/caller.cc"))

    def test_a_name_another_block_leaves_untyped_is_unanswerable(self, tmp_path: Path) -> None:
        """The body is one scope: the second ``p`` is not the first block's ``Peer``."""
        _factory_repo(
            tmp_path,
            "  { auto p = MakePeer(1); }\n  { auto p = Deduced(1); p->sendMessage(1); }\n"
            "  return 0;",
        )
        assert not any("sendMessage" in t for t in _call_targets(_build(tmp_path), "lib/caller.cc"))

    @pytest.mark.parametrize(
        "extra",
        [
            # A member typedef is not the same-named class declared elsewhere.
            "struct Other { struct Item { int sendMessage(int n) { return n; } }; };\n"
            "struct Box { typedef Peer Item; Item get() { return Item(); } };\n",
            # A template parameter list several lines long still names a parameter.
            "struct Item { int sendMessage(int n) { return n; } };\n"
            "template <typename Alloc,\n          typename Hash,\n          typename Eq,\n"
            "          typename Item>\n"
            "struct Box { Item get() { return Item(); } };\n",
        ],
    )
    def test_a_return_type_that_names_no_class_binds_nothing(
        self, tmp_path: Path, extra: str
    ) -> None:
        _repo(tmp_path, "  Box b;\n  auto s = b.get();\n  return s.sendMessage(1);", extra=extra)
        assert not any("sendMessage" in t for t in _call_targets(_build(tmp_path), "lib/caller.cc"))


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


def _add_host_field(root: Path, declaration: str) -> None:
    header = root / "lib" / "host.h"
    header.write_text(
        header.read_text().replace("  Host* parent_;", f"  Host* parent_;\n  {declaration}")
    )


class TestFieldDeclarationForms:
    """Field and local spellings beyond ``T name;`` that still name the receiver's class."""

    def test_an_elaborated_class_field_binds_its_class(self, tmp_path: Path) -> None:
        """``class Peer* peer_;`` names ``Peer``; it is no nested class owning the field."""
        _field_repo(tmp_path, "  elaborated_->sendMessage(1);")
        _add_host_field(tmp_path, "class Peer* elaborated_;")
        assert "lib/peer.cc::Peer::sendMessage" in _callees(
            _build(tmp_path), "lib/host.cc::Host::Run"
        )

    def test_a_const_pointer_field_binds_its_class(self, tmp_path: Path) -> None:
        _field_repo(tmp_path, "  fixed_->sendMessage(1);")
        _add_host_field(tmp_path, "Peer* const fixed_;")
        assert "lib/peer.cc::Peer::sendMessage" in _callees(
            _build(tmp_path), "lib/host.cc::Host::Run"
        )

    def test_a_later_declarator_of_a_local_list_binds(self, tmp_path: Path) -> None:
        _repo(tmp_path, "  Peer *first, *second;\n  return second->sendMessage(1);")
        targets = _call_targets(_build(tmp_path), "lib/caller.cc")
        assert any(t.endswith("Peer::sendMessage") for t in targets)


def _port_repo(root: Path, *, include_example: bool) -> None:
    """A ``port::Lock`` two includes away, beside a same-named example port."""
    (root / "port").mkdir(parents=True, exist_ok=True)
    (root / "db").mkdir(parents=True, exist_ok=True)
    for name in ("port_posix", "port_example"):
        (root / "port" / f"{name}.h").write_text(
            "#pragma once\nnamespace store {\nnamespace port {\n"
            "class Lock {\n public:\n  void Acquire();\n};\n}\n}\n"
        )
        (root / "port" / f"{name}.cc").write_text(
            f'#include "port/{name}.h"\n'
            "namespace store {\nnamespace port {\nvoid Lock::Acquire() {}\n}\n}\n"
        )
    extra = '#include "port/port_example.h"\n' if include_example else ""
    (root / "port" / "port.h").write_text('#pragma once\n#include "port/port_posix.h"\n' + extra)
    (root / "db" / "db.h").write_text(
        '#pragma once\n#include "port/port.h"\nnamespace store {\n'
        "class Db {\n public:\n  void Run();\n private:\n  port::Lock lock_;\n};\n}\n"
    )
    (root / "db" / "db.cc").write_text(
        '#include "db/db.h"\nnamespace store {\nvoid Db::Run() { lock_.Acquire(); }\n}\n'
    )


class TestClassReachedThroughIncludes:
    """Two same-named classes refuse the repository-wide tier; the includes can tell them apart."""

    def test_the_one_class_the_includes_reach_binds(self, tmp_path: Path) -> None:
        _port_repo(tmp_path, include_example=False)
        assert _callees(_build(tmp_path), "db/db.cc::Db::Run") == {
            "port/port_posix.cc::Lock::Acquire"
        }

    def test_two_classes_in_reach_refuse(self, tmp_path: Path) -> None:
        _port_repo(tmp_path, include_example=True)
        callees = _callees(_build(tmp_path), "db/db.cc::Db::Run")
        assert not any(t.endswith("::Acquire") for t in callees)

    def test_a_class_only_the_calling_file_reaches_also_counts(self, tmp_path: Path) -> None:
        """The header reaches one ``Lock``; the body includes the other, so both are in reach."""
        _port_repo(tmp_path, include_example=False)
        (tmp_path / "db" / "db.cc").write_text(
            '#include "db/db.h"\n#include "port/port_example.h"\n'
            "namespace store {\nvoid Db::Run() { lock_.Acquire(); }\n}\n"
        )
        callees = _callees(_build(tmp_path), "db/db.cc::Db::Run")
        assert not any(t.endswith("::Acquire") for t in callees)


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


_VIEW = (
    "#pragma once\nnamespace text {\n"
    "template <typename Char> class basic_view {\n public:\n"
    "  int size() const { return 0; }\n};\n"
    "using view = basic_view<char>;\n}\n"
)


def _alias_repo(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)


class TestAliasedReceivers:
    """A receiver whose type is spelled through ``using``/``typedef`` binds the class it names."""

    def test_a_namespace_alias_two_includes_away_binds_its_class_template(
        self, tmp_path: Path
    ) -> None:
        _alias_repo(
            tmp_path,
            {
                "lib/view.h": _VIEW,
                "lib/mid.h": '#pragma once\n#include "view.h"\n',
                "lib/use.h": '#pragma once\n#include "mid.h"\n'
                "namespace text {\ninline int Count(view v) { return v.size(); }\n}\n",
            },
        )
        assert _callees(_build(tmp_path), "lib/use.h::Count") == {"lib/view.h::basic_view::size"}

    def test_a_member_alias_types_a_parameter_of_its_own_class(self, tmp_path: Path) -> None:
        _alias_repo(
            tmp_path,
            {
                "lib/acc.h": "#pragma once\nclass wide {\n public:\n  int high() const { return 1; }\n};\n"
                "struct accessor {\n  using entry = wide;\n"
                "  static int Top(const entry& e) { return e.high(); }\n};\n",
            },
        )
        assert _callees(_build(tmp_path), "lib/acc.h::accessor::Top") == {"lib/acc.h::wide::high"}

    def test_a_member_typedef_of_another_templates_member_typedef_reaches_the_class(
        self, tmp_path: Path
    ) -> None:
        """``typedef Wrap<T*>::Type Entries;`` where ``Wrap<V>::Type`` is ``lib::Map<int, V>``."""
        _alias_repo(
            tmp_path,
            {
                "src/third_party/map.h": "#pragma once\nnamespace lib {\n"
                "template <typename K, typename V> class Map {\n public:\n"
                "  int find(const K& k) { return 0; }\n};\n}\n",
                "src/wrap.h": '#pragma once\n#include "third_party/map.h"\n'
                "template <typename V> struct Wrap {\n  typedef lib::Map<int, V> Type;\n};\n",
                "src/log.h": '#pragma once\n#include "wrap.h"\nstruct Log {\n  void Look(int k);\n'
                "  typedef Wrap<int*>::Type Entries;\n  Entries entries_;\n};\n",
                "src/log.cc": '#include "log.h"\nvoid Log::Look(int k) { entries_.find(k); }\n',
            },
        )
        assert _callees(_build(tmp_path), "src/log.cc::Log::Look") == {
            "src/third_party/map.h::Map::find"
        }

    @pytest.mark.parametrize(
        "files",
        [
            # The alias names a library type: the same-named repository class is not it.
            {
                "lib/view.h": "#pragma once\n#include <string_view>\n"
                "namespace text {\nusing view = std::basic_string_view<char>;\n}\n"
                "class basic_string_view {\n public:\n  int size() const { return 0; }\n};\n",
            },
            # Two repository classes carry the target's name.
            {
                "lib/view.h": _VIEW,
                "lib/other.h": "#pragma once\nnamespace other {\n"
                "template <typename Char> class basic_view {\n public:\n"
                "  int size() const { return 1; }\n};\n}\n",
            },
            # Two reachable aliases of the name disagree.
            {
                "lib/view.h": _VIEW,
                "lib/span.h": "#pragma once\nclass span {\n public:\n  int size() const { return 2; }\n};\n"
                "namespace bytes {\nusing view = span;\n}\n",
            },
        ],
    )
    def test_an_alias_that_names_no_one_repository_class_types_nothing(
        self, tmp_path: Path, files: dict[str, str]
    ) -> None:
        includes = "".join(f'#include "{name.removeprefix("lib/")}"\n' for name in files)
        files["lib/use.h"] = (
            "#pragma once\n" + includes + "inline int Count(text::view v) { return v.size(); }\n"
        )
        _alias_repo(tmp_path, files)
        assert _callees(_build(tmp_path), "lib/use.h::Count") == set()

    def test_a_member_alias_of_a_template_parameter_types_nothing(self, tmp_path: Path) -> None:
        """``using item = Peer;`` where ``Peer`` is the class template's own parameter."""
        _repo(tmp_path, "  return 0;")
        (tmp_path / "lib" / "box.h").write_text(
            '#pragma once\n#include "peer.h"\ntemplate <typename Peer> struct Box {\n'
            "  using item = Peer;\n"
            "  static int Send(const item& v) { return v.sendMessage(1); }\n};\n"
        )
        assert _callees(_build(tmp_path), "lib/box.h::Box::Send") == set()


_PEER_H = "#pragma once\nclass Peer {\n public:\n  int send(int n) { return n; }\n};\n"


@pytest.mark.parametrize(
    ("alias_h", "caller"),
    [
        # A member alias of the class template's own parameter, reached as ``W<int>::type``.
        (
            "template <class T> struct W {\n  using type = T;\n};\nusing View = W<Peer>::type;\n",
            "int Run(View* p) { return p->send(1); }\n",
        ),
        # A qualifier no repository file opens: a library's ``Peer``, not the repository's.
        (
            "namespace net {\nusing View = ext::Peer;\n}\n",
            "int Run(net::View* p) { return p->send(1); }\n",
        ),
        # The caller's own template parameter shadows the alias.
        ("using View = Peer;\n", "template <class View> int Run(View& p) { return p.send(1); }\n"),
    ],
)
def test_an_alias_the_receiver_does_not_mean_types_nothing(
    tmp_path: Path, alias_h: str, caller: str
) -> None:
    _alias_repo(
        tmp_path,
        {
            "lib/peer.h": _PEER_H,
            "lib/alias.h": '#pragma once\n#include "peer.h"\n' + alias_h,
            "lib/mid.h": '#pragma once\n#include "alias.h"\n',
            "lib/use.cc": '#include "mid.h"\n' + caller,
        },
    )
    assert _callees(_build(tmp_path), "lib/use.cc::Run") == set()


def test_a_qualifier_the_class_file_opens_still_binds(tmp_path: Path) -> None:
    """The control for the qualifier rule: ``lib::Peer`` where ``peer.h`` opens ``lib``."""
    _alias_repo(
        tmp_path,
        {
            "lib/peer.h": "#pragma once\nnamespace lib {\n"
            + _PEER_H.removeprefix("#pragma once\n")
            + "}\n",
            "lib/alias.h": '#pragma once\n#include "peer.h"\nusing View = lib::Peer;\n',
            "lib/use.cc": '#include "alias.h"\nint Run(View* p) { return p->send(1); }\n',
        },
    )
    assert _callees(_build(tmp_path), "lib/use.cc::Run") == {"lib/peer.h::Peer::send"}
