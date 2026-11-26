"""JVM profiles: JFR text, collapsed stacks, and Java frames joined to their methods."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from repowise.core.evidence.profile_convert import convert_profile
from repowise.core.evidence.profile_formats import detect_profile_format, profile_formats
from repowise.core.evidence.profile_formats.model import frame_language
from repowise.core.frame_resolver import (
    ProfileFrame,
    parse_jvm_frame,
    place_jvm_frames,
    resolve_frames,
)
from repowise.core.ingestion.parser import ASTParser
from repowise.core.workspace.repo_index import IndexedSymbol
from tests.unit.ingestion.parser._helpers import _make_file_info

# A real recording of the fixture, run from D:\day-ev2\scratch\feat-jvm-profile-join\fixture
# with the classes compiled by `javac -g -d classes src/main/java/calc/Calc.java`:
#   java -XX:StartFlightRecording=filename=calc.jfr,settings=fast.jfc -cp classes calc.Calc 2500
#   jfr print --events jdk.ExecutionSample --stack-depth 64 calc.jfr > calc.jfr.txt
# fast.jfc is the JDK's `profile` settings with jdk.ExecutionSample#period=1ms (JDK 21.0.12).
# Frames carry no file: the root is wherever the index is.
FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "jfr_java"
REL = "src/main/java/calc/Calc.java"
ROOT = "D:/src/jfrcalc"


def _symbols(rel: str = REL, source: bytes | None = None) -> dict[str, list[IndexedSymbol]]:
    """The fixture's symbols as the Java parser ids them (overloads carry ``#N(Types)``)."""
    src = source if source is not None else (FIXTURE / rel).read_bytes()
    parsed = ASTParser().parse_file(_make_file_info(rel, "java"), src)
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


def _run():
    data = (FIXTURE / "calc.jfr.txt").read_bytes()
    return profile_formats.get("jfr").read(data)


def _joined(run, symbols=None, root=ROOT):
    symbols = symbols or _symbols()
    frames = place_jvm_frames(run.frames, root=root, files=list(symbols))
    res = resolve_frames(frames, root=root, symbols_by_file=symbols)
    return frames, res


def _local(symbol_id: str) -> str:
    return symbol_id.split("::", 1)[1]


def test_jfr_print_text_is_sniffed_and_read_as_cpu_samples() -> None:
    data = (FIXTURE / "calc.jfr.txt").read_bytes()
    assert detect_profile_format(data[:4096]) == "jfr"
    run = _run()
    assert (run.unit, run.value_kind, run.tool_name) == ("samples", "cpu", "jfr")
    assert sum(s.samples or 0 for s in run.stacks) == 547
    main = next(f for f in run.frames if f.function == "calc.Calc.main(String[])" and f.line == 123)
    assert (main.path, main.language) == ("", "java")
    # Root first: every stack starts in main, but one of the recorder's own thread.
    roots = {run.frames[s.frames[0]].function for s in run.stacks}
    assert roots == {
        "calc.Calc.main(String[])",
        "jdk.jfr.internal.dcmd.AbstractDCmd.execute(String, String, char)",
    }


def test_powershell_utf16_output_reads_the_same() -> None:
    text = (FIXTURE / "calc.jfr.txt").read_bytes().decode("utf-8")
    data = b"\xff\xfe" + text.encode("utf-16-le")
    assert detect_profile_format(data[:4096]) == "jfr"
    assert profile_formats.get("jfr").read(data) == _run()


def test_every_fixture_frame_joins_by_line_to_the_method_that_ran_it() -> None:
    frames, res = _joined(_run())
    hits = {r.index: r for r in res.resolved}
    got: dict[tuple[str, int], tuple[str | None, bool] | str] = {}
    for i, f in enumerate(frames):
        if f.path:
            hit = hits.get(i)
            got[(f.function, f.line)] = (
                (_local(hit.symbol.symbol_id) if hit.symbol else None, hit.folded)
                if hit
                else res.reasons[i]
            )
    # Overloads: each sum frame lands on its own declaration.
    assert got[("calc.Calc.sum(int)", 37)] == ("Calc::sum#1(int)", False)
    assert got[("calc.Calc.sum(long)", 45)] == ("Calc::sum#1(long)", False)
    assert got[("calc.Calc.sum(int, int)", 53)] == ("Calc::sum#2", False)
    # A method of an anonymous class, held under the method that declares it.
    assert got[("calc.Calc$1.compare(String, String)", 87)] == (
        "Calc::sorted::Comparator::compare",
        False,
    )
    # javac's bridge compare(Object, Object) sits on the anonymous class's line.
    assert got[("calc.Calc$1.compare(Object, Object)", 81)] == "generated"
    # A lambda folds into the method it is written in.
    assert got[("calc.Calc.lambda$mapAll$0(int)", 63)] == ("Calc::mapAll", True)
    # A field initializer runs in the constructor, on the field's line.
    assert got[("calc.Calc.<init>(int)", 11)] == ("Calc::Calc#1", False)
    # JDK frames are not in the checkout.
    assert all(not f.path for f in frames if f.function.startswith(("java.", "jdk.")))


def test_the_fixture_converts_with_jdk_time_charged_to_the_repo_caller() -> None:
    run = _run()
    symbols = _symbols()
    frames = place_jvm_frames(run.frames, root=ROOT, files=list(symbols))
    conv = convert_profile(
        type(run)(**{**run.__dict__, "frames": frames}), root=ROOT, symbols_by_file=symbols
    )
    assert conv.skip_rate <= 0.05
    rows = {
        e["function"].removeprefix("src.main.java.calc.Calc."): e for e in conv.document["symbols"]
    }
    assert rows["Calc.mapAll"]["self_samples"] == 356  # the inlined lambda's time included
    # The comparator's time is its own, under the library sort that called it.
    assert rows["Calc.sorted.Comparator.compare"]["self_samples"] == 35
    assert rows["Calc.sorted"]["inside"][0]["name"] == "java.util.ArrayList.sort(Comparator)"
    assert rows["Calc.main"]["total_samples"] == 546
    # The one ambiguous frame is gone with the field initializer's constructor joined.
    assert "ambiguous" not in conv.document["skipped"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "com.acme.Cache$Entry.get(Object, int[])",
            (("com", "acme"), "Cache$Entry", "get", ("Object", "int[]"), 0),
        ),
        (
            "com/acme/Cache.get(Ljava/lang/Object;[ILjava/util/Map$Entry;)V",
            (("com", "acme"), "Cache", "get", ("Object", "int[]", "Entry"), 0),
        ),
        ("com/acme/Cache.get:42_[j]", (("com", "acme"), "Cache", "get", None, 42)),
        ("com.acme.Cache.get_[j]:42", (("com", "acme"), "Cache", "get", None, 42)),
        ("com.acme.Cache.<init>()", (("com", "acme"), "Cache", "<init>", (), 0)),
        ("Main.main(String...)", ((), "Main", "main", ("String[]",), 0)),
    ],
)
def test_jvm_frame_spellings_parse(text: str, expected: tuple) -> None:
    n = parse_jvm_frame(text)
    assert n is not None
    assert (n.package, n.cls, n.method, n.params, n.line) == expected


@pytest.mark.parametrize(
    "text",
    [
        "main.main",
        "runtime.mallocgc",
        "gorm.(*DB).First",
        "std::sort",
        "(program)",
        "writev",
        "Bench$$Lambda/0x000002dc90087ce0.run()V",
    ],
)
def test_names_of_other_runtimes_are_not_jvm_frames(text: str) -> None:
    assert parse_jvm_frame(text) is None
    assert frame_language("", text) == "unknown"


def _collapsed(*lines: str):
    return profile_formats.get("collapsed").read("\n".join(lines).encode())


def test_collapsed_with_lines_or_signatures_joins_each_overload() -> None:
    run = _collapsed(
        "calc/Calc.main:123_[j];calc/Calc.sum:45_[j] 3",
        "calc/Calc.main([Ljava/lang/String;)V;calc/Calc.sum(J)J 5",
        "calc/Calc.main([Ljava/lang/String;)V;calc/Calc.sum(II)J 7",
        "calc/Calc.main([Ljava/lang/String;)V;calc/Calc.sorted(I)Ljava/util/List;;java/util/ArrayList.sort(Ljava/util/Comparator;)V 2",
    )
    frames, res = _joined(run)
    by = {(f.function, f.line): r for f, r in zip(frames, res.reasons, strict=True)}
    hits = {frames[r.index].function: _local(r.symbol.symbol_id) for r in res.resolved}
    assert hits["calc/Calc.sum:45_[j]"] == "Calc::sum#1(long)"
    assert hits["calc/Calc.sum(J)J"] == "Calc::sum#1(long)"
    assert hits["calc/Calc.sum(II)J"] == "Calc::sum#2"
    assert hits["calc/Calc.sorted(I)Ljava/util/List;"] == "Calc::sorted"
    assert by[("java/util/ArrayList.sort(Ljava/util/Comparator;)V", 0)] == "outside_root"


def test_a_name_alone_never_picks_one_of_several_overloads() -> None:
    run = _collapsed("calc/Calc.main;calc/Calc.sum;calc/Calc.mapAll 4")
    frames, res = _joined(run)
    reasons = {f.function: r for f, r in zip(frames, res.reasons, strict=True)}
    assert reasons["calc/Calc.sum"] == "ambiguous"
    hits = {frames[r.index].function: _local(r.symbol.symbol_id) for r in res.resolved}
    # A method declared once joins by its class and name.
    assert hits == {"calc/Calc.main": "Calc::main", "calc/Calc.mapAll": "Calc::mapAll"}


def test_a_count_shared_by_two_overloads_needs_the_types() -> None:
    hits = {}
    for sig in ("calc/Calc.sum(I)J", "calc/Calc.sum(J)J", "calc/Calc.sum(D)J"):
        _, res = _joined(_collapsed(f"{sig} 1"))
        hits[sig] = _local(res.resolved[0].symbol.symbol_id) if res.resolved else res.reasons[0]
    assert hits == {
        "calc/Calc.sum(I)J": "Calc::sum#1(int)",
        "calc/Calc.sum(J)J": "Calc::sum#1(long)",
        "calc/Calc.sum(D)J": "ambiguous",
    }


INNER = b"""package calc;

public class Outer {
  class Inner {
    Inner(int a) {}

    Inner(int a, int b) {}
  }

  enum Mode {
    A(1), B(1, 2);

    Mode(int a) {}

    Mode(int a, int b) {}
  }
}
"""


def test_compiled_constructor_counts_never_choose_an_overload() -> None:
    # An inner class's constructor takes its outer instance first; an enum's its name and ordinal.
    rel = "src/main/java/calc/Outer.java"
    symbols = _symbols(rel, INNER)
    for sig in (
        "calc/Outer$Inner.<init>(Lcalc/Outer;I)V",
        "calc/Outer$Mode.<init>(Ljava/lang/String;II)V",
    ):
        _, res = _joined(_collapsed(f"{sig} 1"), symbols)
        assert res.reasons == ("ambiguous",), sig


def test_a_jvm_frame_without_a_line_or_a_unique_name_is_not_joined_by_line_rules() -> None:
    run = _collapsed("calc/Calc.lambda$mapAll$0 1", "calc/Calc$1.compare 1")
    _, res = _joined(run)
    assert set(res.reasons) == {"no_second_signal"}


def test_synthetic_frames_are_generated_and_static_init_is_the_file() -> None:
    rel = REL
    root = ROOT
    frames = [
        ProfileFrame(f"{root}/{rel}", 30, "java", "calc.Calc.access$000(Calc)"),
        ProfileFrame(f"{root}/{rel}", 9, "java", "calc.Calc.<clinit>()"),
        ProfileFrame(f"{root}/{rel}", 9, "java", "calc.Calc$$Lambda$14.run()"),
        ProfileFrame(f"{root}/{rel}", 96, "java", "calc.Calc$Box.compareTo(Object)"),
        ProfileFrame(f"{root}/{rel}", 104, "java", "calc.Calc$Box.compareTo(Calc$Box)"),
        ProfileFrame(f"{root}/{rel}", 104, "java", "calc.Calc.compareTo(Calc$Box)"),
    ]
    res = resolve_frames(frames, root=root, symbols_by_file=_symbols())
    assert res.reasons == (
        "generated",
        None,
        "generated",
        "generated",
        None,
        "second_signal_mismatch",
    )
    clinit, compare = res.resolved
    assert (clinit.symbol, clinit.folded) == (None, False)
    assert _local(compare.symbol.symbol_id) == "Box::compareTo"


def test_frames_are_placed_on_the_one_file_of_their_class() -> None:
    files = [
        "core/src/main/java/com/acme/Cache.java",
        "core/src/main/kotlin/com/acme/Util.kt",
        "a/src/main/java/com/acme/Twin.java",
        "b/src/main/java/com/acme/Twin.java",
        "other/src/main/java/org/acme/Cache.java",
    ]
    frames = [
        ProfileFrame("", 0, "java", "com/acme/Cache$Entry.get:12"),
        ProfileFrame("", 0, "java", "com.acme.UtilKt.hex(Byte)"),
        ProfileFrame("", 0, "java", "com.acme.Twin.run()"),
        ProfileFrame("", 0, "java", "java.lang.String.hashCode()"),
        ProfileFrame("Cache.java", 3, "java", "com.acme.Cache.put(Object)"),
        ProfileFrame("/elsewhere/com/acme/Cache.java", 3, "java", "com.acme.Cache.put(Object)"),
        ProfileFrame("", 0, "unknown", "com.acme.Cache.get"),
    ]
    placed = place_jvm_frames(frames, root="C:\\work\\repo\\", files=files)
    assert [(f.path, f.line, f.language) for f in placed] == [
        ("C:/work/repo/core/src/main/java/com/acme/Cache.java", 12, "java"),
        ("C:/work/repo/core/src/main/kotlin/com/acme/Util.kt", 0, "kotlin"),
        ("", 0, "java"),  # two modules declare it: neither file
        ("", 0, "java"),
        ("C:/work/repo/core/src/main/java/com/acme/Cache.java", 3, "java"),
        ("/elsewhere/com/acme/Cache.java", 3, "java"),  # another checkout stays outside
        ("", 0, "unknown"),
    ]


KOTLIN = b"""package com.acme

fun Byte.hex(): String = toString(16)

fun hex(b: Byte): String = b.toString(16)

fun twice(n: Int): Int = n * 2
"""


def test_kotlin_top_level_functions_join_through_the_file_class() -> None:
    rel = "src/main/kotlin/com/acme/Util.kt"
    parsed = ASTParser().parse_file(_make_file_info(rel, "kotlin"), KOTLIN)
    symbols = {
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
    frames = [
        ProfileFrame("", 0, "java", "com/acme/UtilKt.twice(I)I"),
        # An extension compiles to hex(byte), the same as the plain overload: never chosen.
        ProfileFrame("", 0, "java", "com/acme/UtilKt.hex(B)Ljava/lang/String;"),
        ProfileFrame("", 0, "java", "com/acme/UtilKt.twice:7"),
    ]
    placed = place_jvm_frames(frames, root=ROOT, files=[rel])
    res = resolve_frames(placed, root=ROOT, symbols_by_file=symbols)
    assert res.reasons[1] == "ambiguous"
    names = Counter(_local(r.symbol.symbol_id) for r in res.resolved)
    assert names == Counter({"twice": 2})


def test_a_default_package_class_is_placed_only_at_a_source_root() -> None:
    frames = [ProfileFrame("", 0, "java", "Main.main(String[])")]
    nested = place_jvm_frames(frames, root=ROOT, files=["src/main/java/com/foo/Main.java"])
    at_root = place_jvm_frames(frames, root=ROOT, files=["app/src/main/java/Main.java"])
    assert nested[0].path == ""
    assert at_root[0].path == f"{ROOT}/app/src/main/java/Main.java"


def test_a_vendored_copy_of_a_class_does_not_hide_the_maintained_one() -> None:
    files = ["src/main/java/com/acme/Cache.java", "vendor/src/main/java/com/acme/Cache.java"]
    (placed,) = place_jvm_frames(
        [ProfileFrame("", 0, "java", "com.acme.Cache.get()")], root=ROOT, files=files
    )
    assert placed.path == f"{ROOT}/src/main/java/com/acme/Cache.java"


ANON = b"""package p;

public class T {
  static class Impl {
    public int f(int a) {
      return a;
    }
  }

  int m() {
    Impl b =
        new Impl() {
          public int f(int a) {
            return a + 1;
          }

          public int f(String s) {
            return 0;
          }
        };
    return b.f(1);
  }

  int g(int a) {
    return a;
  }

  int g(String s) {
    return 0;
  }
}
"""


def test_an_anonymous_subclass_never_lends_its_overloads_to_the_named_class() -> None:
    rel = "src/main/java/p/T.java"
    symbols = _symbols(rel, ANON)
    _, res = _joined(_collapsed("p/T$Impl.f(I)I 1"), symbols)
    assert _local(res.resolved[0].symbol.symbol_id) == "Impl::f"


def test_a_line_that_drifted_onto_a_neighbouring_overload_is_a_mismatch() -> None:
    rel = "src/main/java/p/T.java"
    path = f"{ROOT}/{rel}"
    frames = [
        ProfileFrame(path, 29, "java", "p.T.g(int)"),  # line 29 is in g(String)
        ProfileFrame(path, 29, "java", "p.T.g(String)"),
    ]
    res = resolve_frames(frames, root=ROOT, symbols_by_file=_symbols(rel, ANON))
    assert res.reasons[0] == "second_signal_mismatch"
    assert _local(res.resolved[0].symbol.symbol_id) == "T::g#1(String)"


def test_javacs_access_constructor_and_local_classes() -> None:
    frames = [
        ProfileFrame(f"{ROOT}/{REL}", 0, "java", "calc.Calc.<init>(Calc$1)"),
        ProfileFrame(f"{ROOT}/{REL}", 0, "java", "calc.Calc.sum(int, int, int)"),
    ]
    res = resolve_frames(frames, root=ROOT, symbols_by_file=_symbols())
    # No overload takes three parameters: nothing to join, not a choice between two.
    assert res.reasons == ("generated", "no_span")


def test_a_frame_with_a_path_keeps_its_own_language() -> None:
    # An extensionless script, a Cython module: their frames are not JVM frames.
    assert frame_language("/repo/bin/app", "App.run") == "unknown"
    assert frame_language("/repo/fast.pyx", "Parser.feed") == "unknown"
    assert frame_language("Cache.java", "com.acme.Cache.get") == "java"


def test_an_unbalanced_parenthesis_splits_as_before() -> None:
    run = _collapsed("a(b;c;d 1")
    assert [f.function for f in run.frames] == ["a(b", "c", "d"]


FIELD_LAMBDA = b"""package p;

public class F {
  private final java.util.function.IntUnaryOperator f = x -> x + 1;

  F() {}

  int run(int x) {
    return f.applyAsInt(x);
  }
}
"""


def test_a_lambda_in_an_instance_field_folds_into_the_constructor() -> None:
    rel = "src/main/java/p/F.java"
    frame = ProfileFrame(f"{ROOT}/{rel}", 4, "java", "p.F.lambda$new$0(int)")
    res = resolve_frames([frame], root=ROOT, symbols_by_file=_symbols(rel, FIELD_LAMBDA))
    (hit,) = res.resolved
    assert (_local(hit.symbol.symbol_id), hit.folded) == ("F::F", True)


KOTLIN_OVERLOADS = b"""package com.acme

fun twice(n: Int): Int = n * 2

fun twice(s: String): String = s + s
"""


def test_a_kotlin_frame_on_a_neighbouring_overload_is_a_mismatch() -> None:
    rel = "src/main/kotlin/com/acme/Util.kt"
    parsed = ASTParser().parse_file(_make_file_info(rel, "kotlin"), KOTLIN_OVERLOADS)
    symbols = {
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
    ids = sorted(s.symbol_id.split("::", 1)[1] for s in symbols[rel])
    assert ids == ["twice#1(Int)", "twice#1(String)"]
    frames = [
        ProfileFrame(
            f"{ROOT}/{rel}", 5, "kotlin", "com.acme.UtilKt.twice(int)"
        ),  # line 5 is twice(String)
        ProfileFrame(f"{ROOT}/{rel}", 3, "kotlin", "com.acme.UtilKt.twice(int)"),
    ]
    res = resolve_frames(frames, root=ROOT, symbols_by_file=symbols)
    assert res.reasons[0] == "second_signal_mismatch"
    assert _local(res.resolved[0].symbol.symbol_id) == "twice#1(Int)"
