"""A top-tier dead-code confidence must mean "we looked everywhere".

The unused-export pass promotes to 1.0 when the defining file has importers,
which assumes that using a symbol requires importing it. Where that is false —
a same-package Kotlin or Go reference, a same-translation-unit C++ type, an
aliased import, a handler named by string from infrastructure config — the
promotion is not weak evidence, it is inadmissible, and the finding shipped as
deletion-ready.

These exercise the clamp directly rather than through a detector. What needs
pinning is which findings it touches and which it must leave alone; building a
graph that yields one finding per case would test the detectors instead. The
detector-level behaviour is covered by the fixtures in
``test_unused_exports.py``.

The control in every rescue case is the second symbol: a change that suppresses
false positives by suppressing everything passes every other assertion here.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.dead_code.models import DeadCodeFindingData, DeadCodeKind
from repowise.core.analysis.dead_code.name_occurrences import clamp_unverified_absence
from repowise.core.analysis.dead_code.risk_factors import RISK_CAP_CONFIDENCE


def _finding(
    symbol_name: str,
    *,
    file_path: str = "src/lib.kt",
    confidence: float = 1.0,
    kind: DeadCodeKind = DeadCodeKind.UNUSED_EXPORT,
    start_line: int | None = 1,
    end_line: int | None = 3,
) -> DeadCodeFindingData:
    return DeadCodeFindingData(
        kind=kind,
        file_path=file_path,
        symbol_name=symbol_name,
        symbol_kind="class",
        confidence=confidence,
        reason="test",
        last_commit_at=None,
        commit_count_90d=0,
        lines=3,
        start_line=start_line,
        end_line=end_line,
        package=None,
        evidence=[],
        safe_to_delete=True,
        primary_owner=None,
        age_days=None,
    )


def _src(**files: str) -> dict[str, bytes]:
    """Keyword paths, since a dict literal per case buries the source itself.

    ``src__sky_cpp=...`` is ``src/sky.cpp``: ``__`` is a directory separator
    and the last ``_`` is the extension dot.
    """
    out: dict[str, bytes] = {}
    for key, body in files.items():
        stem, _, ext = key.replace("__", "/").rpartition("_")
        out[f"{stem}.{ext}"] = body.encode("utf-8")
    return out


class TestUsedElsewhere:
    """A name the repository writes somewhere else stops claiming certainty."""

    def test_same_package_reference_in_another_file_caps(self):
        # No import statement anywhere: this is the Kotlin/Go shape.
        source = _src(
            src__lib_kt="package app\n\nclass Registry\n",
            src__server_kt="package app\n\nfun start() = Registry()\n",
        )
        finding = _finding("Registry")
        clamp_unverified_absence([finding], source)

        assert finding.confidence == RISK_CAP_CONFIDENCE
        assert finding.safe_to_delete is False
        assert "src/server.kt" in finding.evidence[-1]

    def test_untouched_when_the_name_appears_only_in_its_declaration(self):
        source = _src(
            src__lib_kt="package app\n\nclass Registry\n",
            src__server_kt="package app\n\nfun start() = 1\n",
        )
        finding = _finding("Registry")
        clamp_unverified_absence([finding], source)

        assert finding.confidence == 1.0
        assert finding.safe_to_delete is True
        assert finding.evidence == []

    def test_a_modules_own_generated_type_sibling_is_not_a_use(self):
        # A .d.ts beside runtime.js restates that module's exports as types. It
        # declares the names a second time and calls none of them, so counting
        # it would make every symbol in a generated binding module look alive.
        source = _src(
            **{
                "runtime__runtime_js": "export function WindowHide() {}\n",
                "runtime__runtime_d.ts": "export function WindowHide(): void;\n",
            }
        )
        finding = _finding("WindowHide", file_path="runtime/runtime.js")
        clamp_unverified_absence([finding], source)

        assert finding.confidence == 1.0
        assert finding.safe_to_delete is True

    def test_a_declaration_file_elsewhere_is_still_a_use(self):
        # The narrowing is same-directory, same-stem only. A .d.ts naming a
        # symbol from another module really is referring to it.
        source = _src(
            **{
                "runtime__runtime_js": "export function WindowHide() {}\n",
                "types__api_d.ts": "import { WindowHide } from '../runtime/runtime'\n",
            }
        )
        finding = _finding("WindowHide", file_path="runtime/runtime.js")
        clamp_unverified_absence([finding], source)

        assert finding.confidence == RISK_CAP_CONFIDENCE

    def test_a_non_code_file_counts_as_a_use(self):
        # The handler is named by string from infrastructure config, which is
        # the only place in the repository that knows it is an entry point.
        source = _src(
            broker_mjs="export const handleEvent = async () => 1;\n",
            main_tf='resource "aws_lambda_function" "b" {\n  handler = "broker.handleEvent"\n}\n',
        )
        finding = _finding("handleEvent", file_path="broker.mjs")
        clamp_unverified_absence([finding], source)

        assert finding.confidence == RISK_CAP_CONFIDENCE
        assert "main.tf" in finding.evidence[-1]


class TestOwnFile:
    """The same-translation-unit shape, where the only use is in the declaring file."""

    def test_use_below_its_own_span_caps(self):
        source = _src(
            src__sky_cpp=(
                "struct Row { float a; };\n"
                "float compute() {\n"
                "    Row scratch{1.0f};\n"
                "    return scratch.a;\n"
                "}\n"
            )
        )
        finding = _finding("Row", file_path="src/sky.cpp", start_line=1, end_line=1)
        clamp_unverified_absence([finding], source)

        assert finding.confidence == RISK_CAP_CONFIDENCE
        assert "src/sky.cpp:3" in finding.evidence[-1]

    def test_a_recursive_call_inside_the_span_is_not_a_use(self):
        # The symbol names itself, which is not evidence that anything else does.
        source = _src(
            src__rec_py=(
                "def walk(n):\n"
                "    if n:\n"
                "        return walk(n - 1)\n"
                "    return 0\n"
            )
        )
        finding = _finding("walk", file_path="src/rec.py", start_line=1, end_line=4)
        clamp_unverified_absence([finding], source)

        assert finding.confidence == 1.0
        assert finding.safe_to_delete is True

    def test_unknown_span_cannot_be_answered_and_caps(self):
        source = _src(src__lib_kt="class Registry\n")
        finding = _finding("Registry", start_line=None, end_line=None)
        clamp_unverified_absence([finding], source)

        assert finding.confidence == RISK_CAP_CONFIDENCE
        # The repository *was* searched and nothing was found in another file.
        # Only the declaration's own extent was unknown, so saying the repo
        # could not be searched would describe something that did not happen.
        assert "could not be bounded" in finding.evidence[-1]

    def test_a_bare_carriage_return_does_not_shift_the_span(self):
        # ``bytes.splitlines`` breaks on a lone \r and the parser's row counter
        # does not, so a progress-bar print earlier in the file used to push
        # every later line out of its recorded span — which made a symbol's own
        # recursive call read as an external use.
        source = _src(
            src__rec_py=(
                'def progress():\n'
                '    print("\rworking\r", end="")\n'
                '\n'
                'def walk(n):\n'
                '    if n:\n'
                '        return walk(n - 1)\n'
                '    return 0\n'
            )
        )
        finding = _finding("walk", file_path="src/rec.py", start_line=4, end_line=7)
        clamp_unverified_absence([finding], source)

        assert finding.confidence == 1.0

    def test_overloads_do_not_count_each_other_as_uses(self):
        # Two declarations share a name, so each one's header sits outside the
        # other's span. Nothing calls either.
        source = _src(
            src__util_cpp=(
                "void process(int a) {\n    (void)a;\n}\n"
                "\n"
                "void process(float b) {\n    (void)b;\n}\n"
            )
        )
        first = _finding("process", file_path="src/util.cpp", start_line=1, end_line=3)
        second = _finding("process", file_path="src/util.cpp", start_line=5, end_line=7)
        clamp_unverified_absence([first, second], source)

        assert first.confidence == 1.0
        assert second.confidence == 1.0

    def test_a_use_inside_a_same_named_symbols_body_still_counts(self):
        # Only a sibling's declaration header is discounted, never its body. A
        # span that encloses an unrelated same-named symbol's real call site
        # would otherwise swallow that use and leave a live symbol at the top
        # of the scale, which is the one outcome this module must not produce.
        source = _src(
            src__util_cpp=(
                "class process {\n"
                "  void run() {\n"
                "    other(process());\n"
                "  }\n"
                "};\n"
                "\n"
                "int process() { return 1; }\n"
            )
        )
        klass = _finding("process", file_path="src/util.cpp", start_line=1, end_line=5)
        func = _finding("process", file_path="src/util.cpp", start_line=7, end_line=7)
        clamp_unverified_absence([klass, func], source)

        assert func.confidence == RISK_CAP_CONFIDENCE
        assert "src/util.cpp:3" in func.evidence[-1]

    def test_one_object_listed_twice_collects_one_evidence_line(self):
        source = _src(
            src__lib_kt="class Registry\n", src__b_kt="fun f() = Registry()\n"
        )
        finding = _finding("Registry")
        clamp_unverified_absence([finding, finding], source)

        assert len(finding.evidence) == 1


class TestScopeAndSafety:
    """What the clamp must refuse to touch."""

    def test_no_source_access_declines_rather_than_guessing(self):
        finding = _finding("Registry")
        clamp_unverified_absence([finding], {})

        assert finding.confidence == 1.0
        assert finding.evidence == []

    @pytest.mark.parametrize(
        "kind",
        [
            DeadCodeKind.UNREACHABLE_FILE,
            DeadCodeKind.UNUSED_INTERNAL,
            DeadCodeKind.ZOMBIE_PACKAGE,
        ],
    )
    def test_only_unused_exports_are_in_scope(self, kind):
        source = _src(
            src__lib_kt="class Registry\n", src__b_kt="fun f() = Registry()\n"
        )
        finding = _finding("Registry", kind=kind, confidence=0.9)
        clamp_unverified_absence([finding], source)

        assert finding.confidence == 0.9

    def test_never_raises_a_confidence(self):
        # 0.5 is above the cap, so this one is in scope and falls to it. What
        # is being pinned is the direction: a clamp that could move a number
        # up would be a way to invent certainty rather than to withdraw it.
        source = _src(
            src__lib_kt="class Registry\n", src__b_kt="fun f() = Registry()\n"
        )
        finding = _finding("Registry", confidence=0.5)
        clamp_unverified_absence([finding], source)

        assert finding.confidence <= 0.5

    def test_a_finding_already_at_the_cap_is_left_alone(self):
        # Otherwise every low-confidence finding collects a second evidence
        # line saying something its confidence already said.
        source = _src(
            src__lib_kt="class Registry\n", src__b_kt="fun f() = Registry()\n"
        )
        finding = _finding("Registry", confidence=RISK_CAP_CONFIDENCE)
        clamp_unverified_absence([finding], source)

        assert finding.evidence == []

    def test_no_finding_is_ever_removed(self):
        source = _src(
            src__lib_kt="class Registry\n", src__b_kt="fun f() = Registry()\n"
        )
        findings = [_finding("Registry"), _finding("Stranded")]
        result = clamp_unverified_absence(findings, source)

        assert len(result) == 2

    def test_a_name_too_short_to_search_for_is_not_an_absence(self):
        # The identifier scan needs three characters, so a shorter name matches
        # nowhere — including on its own declaration. That is a question we
        # cannot ask, not an answer.
        source = _src(src__lib_kt="class Ab\n")
        finding = _finding("Ab")
        clamp_unverified_absence([finding], source)

        assert finding.confidence == RISK_CAP_CONFIDENCE
        assert "could not be searched" in finding.evidence[-1]

    def test_a_non_ascii_name_is_not_searched_for_by_its_ascii_stump(self):
        # Dropping the non-ASCII characters would search for "caf" and let an
        # unrelated identifier elsewhere read as a use of this symbol.
        source = _src(
            src__lib_py="class café:\n    pass\n",
            src__other_py="caf = 1\n",
        )
        finding = _finding("café", file_path="src/lib.py")
        clamp_unverified_absence([finding], source)

        assert "could not be searched" in finding.evidence[-1]
        assert "other.py" not in finding.evidence[-1]

    def test_a_name_the_identifier_scan_cannot_match_is_not_an_absence(self):
        # A JVM or JavaScript synthetic name carries a character the identifier
        # shape does not admit, so the scan would only ever see two shorter
        # tokens and would miss the declaration itself.
        source = _src(
            src__lib_kt="class Foo$Companion\n",
            src__b_kt="val x = Foo$Companion\n",
        )
        finding = _finding("Foo$Companion")
        clamp_unverified_absence([finding], source)

        assert "could not be searched" in finding.evidence[-1]

    def test_a_file_outside_the_indexed_set_is_not_an_absence(self):
        # The scan cannot see the declaration it is standing on, so nothing
        # about this file was established either way.
        source = _src(src__other_kt="fun f() = 1\n")
        finding = _finding("Registry")
        clamp_unverified_absence([finding], source)

        assert finding.confidence == RISK_CAP_CONFIDENCE
        assert "could not be searched" in finding.evidence[-1]
