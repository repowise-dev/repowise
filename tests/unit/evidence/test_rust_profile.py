"""Rust profiles: pprof-rs protobuf profile and Rust frames joined to functions/methods."""

from __future__ import annotations

from pathlib import Path

from repowise.core.evidence.profile_convert import convert_profile
from repowise.core.evidence.profile_formats import detect_profile_format, profile_formats
from repowise.core.frame_resolver import resolve_frames
from repowise.core.ingestion.parser import ASTParser
from repowise.core.workspace.repo_index import IndexedSymbol
from tests.unit.ingestion.parser._helpers import _make_file_info

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "evidence" / "pprof_rust"
REL = "src/cart.rs"
ROOT = "/repo"


def _symbols(rel: str = REL) -> dict[str, list[IndexedSymbol]]:
    src = (FIXTURE_DIR / rel).read_bytes()
    parsed = ASTParser().parse_file(_make_file_info(rel, "rust"), src)
    return {
        rel: [
            IndexedSymbol(
                symbol_id=s.id,
                name=s.name,
                qualified_name=s.qualified_name,
                kind=s.kind,
                signature="",
                file_path=rel,
                start_line=s.start_line,
                end_line=s.end_line,
                visibility="public",
            )
            for s in parsed.symbols
        ]
    }


def _run(root: str = ROOT):
    data = (FIXTURE_DIR / "profile.pb.gz").read_bytes()
    run = profile_formats.get("pprof").read(data)
    # The frames in the fixture are already created with /repo/src/cart.rs
    return run


def test_rust_pprof_profile_detected() -> None:
    data = (FIXTURE_DIR / "profile.pb.gz").read_bytes()
    assert detect_profile_format(data) == "pprof"


def test_rust_frames_resolve_and_fold_closures() -> None:
    syms = _symbols()
    run = _run()
    res = resolve_frames(run.frames, root=ROOT, symbols_by_file=syms)

    # All frames in the checkout should resolve
    assert res.skipped == {}
    assert len(res.resolved) == len(run.frames)

    # Frame mapping verification:
    # 0: cart::Cart::new -> new (folded=False)
    # 1: cart::Cart::add -> add (folded=False)
    # 2: cart::Cart::total -> total (folded=False)
    # 3: cart::Cart::total::{{closure}} -> total (folded=True)
    # 4: cart::helper -> helper (folded=False)

    f0 = res.resolved[0]
    assert f0.symbol is not None and f0.symbol.name == "new" and not f0.folded

    f1 = res.resolved[1]
    assert f1.symbol is not None and f1.symbol.name == "add" and not f1.folded

    f2 = res.resolved[2]
    assert f2.symbol is not None and f2.symbol.name == "total" and not f2.folded

    f3 = res.resolved[3]
    assert f3.symbol is not None and f3.symbol.name == "total" and f3.folded

    f4 = res.resolved[4]
    assert f4.symbol is not None and f4.symbol.name == "helper" and not f4.folded


def test_rust_pprof_converts_to_evidence_run() -> None:
    syms = _symbols()
    run = _run()
    conv = convert_profile(run, symbols_by_file=syms, root=ROOT)

    # At least 95% of repo samples join to a function or method
    assert conv.skip_rate is not None and conv.skip_rate <= 0.05

    # Check that total, add, new and helper receive self/total time
    rows = {e["function"]: e for e in conv.document["symbols"]}
    assert any("total" in k for k in rows)
    assert any("add" in k for k in rows)
    assert any("new" in k for k in rows)
    assert any("helper" in k for k in rows)
