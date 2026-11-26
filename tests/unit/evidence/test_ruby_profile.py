"""Ruby profiles: Speedscope from rbspy and Ruby frames joined to methods."""

from __future__ import annotations

import json
from pathlib import Path

from repowise.core.evidence.profile_convert import convert_profile
from repowise.core.evidence.profile_formats import detect_profile_format, profile_formats
from repowise.core.frame_resolver import resolve_frames
from repowise.core.ingestion.parser import ASTParser
from repowise.core.workspace.repo_index import IndexedSymbol
from tests.unit.ingestion.parser._helpers import _make_file_info

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "evidence" / "rbspy"
REL = "lib/cart.rb"
ROOT = "/repo"


def _symbols(rel: str = REL) -> dict[str, list[IndexedSymbol]]:
    src = (FIXTURE_DIR / "cart.rb").read_bytes()
    parsed = ASTParser().parse_file(_make_file_info(rel, "ruby"), src)
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
    data = (FIXTURE_DIR / "profile.speedscope.json").read_bytes()
    doc = json.loads(data.decode("utf-8"))
    for f in doc["shared"]["frames"]:
        f["file"] = f"{root}/{f['file']}"
    return profile_formats.get("speedscope").read(json.dumps(doc).encode("utf-8"))


def test_speedscope_rbspy_profile_detected() -> None:
    data = (FIXTURE_DIR / "profile.speedscope.json").read_bytes()
    assert detect_profile_format(data) == "speedscope"


def test_ruby_frames_resolve_and_fold_blocks() -> None:
    syms = _symbols()
    run = _run()
    res = resolve_frames(run.frames, root=ROOT, symbols_by_file=syms)

    # All frames in the checkout should resolve
    assert res.skipped == {}
    assert len(res.resolved) == len(run.frames)

    # Frame mapping verification:
    # 0: <main> -> file (None, False)
    # 1: <class:Cart> -> file (None, False)
    # 2: Cart#initialize -> initialize
    # 3: Cart.empty -> empty
    # 4: Cart#add -> add
    # 5: Cart#total -> total (folded=False)
    # 6: block in Cart#total -> total (folded=True)
    # 7: rescue in Cart#total -> total (folded=True)

    f0 = res.resolved[0]
    assert f0.symbol is None and not f0.folded

    f1 = res.resolved[1]
    assert f1.symbol is None and not f1.folded

    f2 = res.resolved[2]
    assert f2.symbol is not None and f2.symbol.name == "initialize" and not f2.folded

    f3 = res.resolved[3]
    assert f3.symbol is not None and f3.symbol.name == "empty" and not f3.folded

    f4 = res.resolved[4]
    assert f4.symbol is not None and f4.symbol.name == "add" and not f4.folded

    f5 = res.resolved[5]
    assert f5.symbol is not None and f5.symbol.name == "total" and not f5.folded

    f6 = res.resolved[6]
    assert f6.symbol is not None and f6.symbol.name == "total" and f6.folded

    f7 = res.resolved[7]
    assert f7.symbol is not None and f7.symbol.name == "total" and f7.folded


def test_ruby_speedscope_converts_to_evidence_run() -> None:
    syms = _symbols()
    run = _run()
    conv = convert_profile(run, symbols_by_file=syms, root=ROOT, value_kind="cpu")

    # At least 95% of repo samples join to a method or file
    assert conv.skip_rate is not None and conv.skip_rate <= 0.05

    # Check that total, empty, add and initialize receive self/total time
    rows = {e["function"]: e for e in conv.document["symbols"]}
    assert any("total" in k for k in rows)
    assert any("empty" in k for k in rows)
    assert any("add" in k for k in rows)
