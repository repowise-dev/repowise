"""A C++ call written through ``this`` must produce a call site.

The member-call pattern captured an ``identifier`` receiver only, and the
grammar spells ``this`` as its own node, so ``this->m()`` and
``this->f_->m()`` produced no call site and no tier could resolve them.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder


def _write(root: Path, body: str) -> None:
    (root / "lib").mkdir(parents=True, exist_ok=True)
    (root / "lib" / "s.cc").write_text(
        "struct Peer { virtual void Run(int) = 0; };\n"
        "class S {\n"
        " public:\n"
        "  Peer* s_;\n"
        "  int other(int n) { return n; }\n"
        "  int step(int n) {\n"
        f"    {body}\n"
        "  }\n"
        "};\n"
    )


def _parse_and_build(root: Path):
    parser = ASTParser()
    builder = GraphBuilder(repo_path=root)
    parsed = []
    for fi in FileTraverser(root).traverse():
        pf = parser.parse_file(fi, Path(fi.abs_path).read_bytes())
        parsed.append(pf)
        builder.add_file(pf)
    calls = [c for pf in parsed for c in pf.calls]
    return calls, builder.build()


def _sites(calls) -> set[tuple[str | None, str]]:
    return {(c.receiver_name, c.target_name) for c in calls}


class TestThisMethodCall:
    def test_it_has_this_as_its_receiver(self, tmp_path: Path) -> None:
        _write(tmp_path, "return this->other(n);")
        calls, _ = _parse_and_build(tmp_path)
        assert ("this", "other") in _sites(calls)

    def test_it_resolves_to_the_callers_own_method(self, tmp_path: Path) -> None:
        _write(tmp_path, "return this->other(n);")
        _, graph = _parse_and_build(tmp_path)
        data = graph.get_edge_data("lib/s.cc::S::step", "lib/s.cc::S::other")
        assert data is not None
        assert data["resolution_origin"] == "self_scope"


class TestThisFieldCall:
    def test_an_arrow_field_keeps_the_field_as_receiver(self, tmp_path: Path) -> None:
        _write(tmp_path, "this->s_->Run(1); return 0;")
        calls, _ = _parse_and_build(tmp_path)
        assert ("s_", "Run") in _sites(calls)

    def test_a_dot_field_keeps_the_field_as_receiver(self, tmp_path: Path) -> None:
        _write(tmp_path, "return this->m_buf.size();")
        calls, _ = _parse_and_build(tmp_path)
        assert ("m_buf", "size") in _sites(calls)

    def test_it_matches_the_call_written_without_this(self, tmp_path: Path) -> None:
        """``this->s_->Run(1)`` is the same call as ``s_->Run(1)``."""
        _write(tmp_path / "a", "this->s_->Run(1); return 0;")
        _write(tmp_path / "b", "s_->Run(1); return 0;")
        with_this, _ = _parse_and_build(tmp_path / "a")
        without, _ = _parse_and_build(tmp_path / "b")
        assert with_this == without


class TestOtherNestedReceiversUnchanged:
    def test_a_field_of_another_object_is_not_captured(self, tmp_path: Path) -> None:
        """``b`` in ``a->b->Run()`` belongs to ``a``'s type, not the caller's class."""
        _write(tmp_path, "a->b->Run(2); return 0;")
        calls, _ = _parse_and_build(tmp_path)
        assert not any(c.target_name == "Run" for c in calls)

    def test_a_field_of_a_this_field_is_not_captured(self, tmp_path: Path) -> None:
        _write(tmp_path, "this->a->b->Run(2); return 0;")
        calls, _ = _parse_and_build(tmp_path)
        assert not any(c.target_name == "Run" for c in calls)

    def test_a_this_call_to_the_caller_itself_draws_a_self_edge(self, tmp_path: Path) -> None:
        _write(tmp_path, "return this->step(n - 1);")
        _, graph = _parse_and_build(tmp_path)
        assert graph.has_edge("lib/s.cc::S::step", "lib/s.cc::S::step")

    def test_a_plain_receiver_is_unchanged(self, tmp_path: Path) -> None:
        _write(tmp_path, "s_->Run(1); return 0;")
        calls, _ = _parse_and_build(tmp_path)
        assert ("s_", "Run") in _sites(calls)
