"""A C/C++ symbol written outside its own declaration is used.

Shapes taken from dotnet/runtime and PowerToys, where every one of them was
reported as dead: a typedef tag used through its alias, an enum used through
its enumerators, a timer callback passed by name, a P/Invoke export named from
C#, an MSI custom action listed in a ``.def`` file, a private member defined
in the ``.cpp`` beside its header.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.dead_code import DeadCodeAnalyzer
from repowise.core.analysis.dead_code.c_name_uses import (
    declared_names,
    drop_preprocessed_named_elsewhere,
)
from repowise.core.analysis.dead_code.models import DeadCodeFindingData, DeadCodeKind
from tests.unit.dead_code._helpers import _build_graph

_UNWINDER = b"""#include "unwinder.h"

typedef struct _ARM64_VFP_STATE
{
    struct _ARM64_VFP_STATE *Link;          // link to next state entry
    ULONG Fpcr;
} ARM64_VFP_STATE, *PARM64_VFP_STATE, KARM64_VFP_STATE, *PKARM64_VFP_STATE;

ULONG64 Offset()
{
    return offsetof(KARM64_VFP_STATE, Fpcr);
}
"""

_FLAGS = b"""#pragma once
enum GenerateDumpFlags
{
    GenerateDumpFlagsNone = 0x00,   // none
    GenerateDumpFlagsLoggingEnabled = 0x01,
#if defined(HOST_UNIX)
    GenerateDumpFlagsCrashReportEnabled = 0x04
#endif
};
"""


def _finding(path, name, kind, start, end, finding_kind=DeadCodeKind.UNUSED_EXPORT):
    return DeadCodeFindingData(
        kind=finding_kind,
        file_path=path,
        symbol_name=name,
        symbol_kind=kind,
        confidence=0.6,
        reason="test",
        last_commit_at=None,
        commit_count_90d=0,
        lines=end - start + 1,
        evidence=[],
        safe_to_delete=False,
        primary_owner=None,
        age_days=None,
        start_line=start,
        end_line=end,
    )


def _kept(findings, source_map, unread=frozenset(), declarations=None):
    kept = drop_preprocessed_named_elsewhere(findings, source_map, declarations or {}, unread)
    return {f.symbol_name for f in kept}


def test_typedef_declares_tag_and_every_alias():
    names = declared_names(_finding("unwinder.cpp", "_ARM64_VFP_STATE", "struct", 3, 7), _UNWINDER)
    assert names == [
        "_ARM64_VFP_STATE",
        "ARM64_VFP_STATE",
        "PARM64_VFP_STATE",
        "KARM64_VFP_STATE",
        "PKARM64_VFP_STATE",
    ]


def test_enum_declares_its_enumerators_past_comments_and_directives():
    names = declared_names(_finding("flags.h", "GenerateDumpFlags", "enum", 2, 9), _FLAGS)
    assert names == [
        "GenerateDumpFlags",
        "GenerateDumpFlagsNone",
        "GenerateDumpFlagsLoggingEnabled",
        "GenerateDumpFlagsCrashReportEnabled",
    ]


def test_typedef_tag_used_through_an_alias_is_dropped():
    findings = [
        _finding("unwinder.cpp", "_ARM64_VFP_STATE", "struct", 3, 7),
        _finding("unwinder.cpp", "ARM64_VFP_STATE", "struct", 3, 7),
    ]
    assert _kept(findings, {"unwinder.cpp": _UNWINDER}) == set()


def test_enum_used_through_an_enumerator_elsewhere_is_dropped():
    source = {
        "inc/flags.h": _FLAGS,
        "dump.cpp": b"int f() { return GenerateDumpFlagsNone; }\n",
    }
    assert _kept([_finding("inc/flags.h", "GenerateDumpFlags", "enum", 2, 9)], source) == set()


def test_callback_passed_by_name_in_its_own_file_is_dropped():
    source = {
        "hook.cpp": (
            b"void CALLBACK PressedKeyTimerProc(HWND, UINT, UINT_PTR, DWORD)\n{\n}\n"
            b"void Arm()\n{\n    SetTimer(nullptr, 0, 10, PressedKeyTimerProc);\n}\n"
        )
    }
    assert _kept([_finding("hook.cpp", "PressedKeyTimerProc", "function", 1, 3)], source) == set()


def test_export_named_by_a_csharp_pinvoke_is_dropped():
    source = {
        "native/pal_io.c": b"int32_t SystemNative_FcntlGetIsNonBlocking(intptr_t fd)\n{\n    return 0;\n}\n",
        "Interop.Fcntl.cs": (
            b'[LibraryImport(Libraries.SystemNative, EntryPoint = '
            b'"SystemNative_FcntlGetIsNonBlocking")]\n'
        ),
    }
    finding = _finding("native/pal_io.c", "SystemNative_FcntlGetIsNonBlocking", "function", 1, 4)
    assert _kept([finding], source) == set()


def test_name_listed_only_in_an_unread_file_is_dropped():
    source = {"CustomAction.cpp": b"UINT __stdcall SetBundleInstallLocationCA(MSIHANDLE h)\n{\n}\n"}
    finding = _finding("CustomAction.cpp", "SetBundleInstallLocationCA", "function", 1, 3)
    assert _kept([finding], source, frozenset({"SetBundleInstallLocationCA"})) == set()


def test_private_member_defined_beside_its_header_is_dropped():
    source = {
        "debugger.h": b"class Debugger {\nprivate:\n    static DWORD ThreadProcStatic(LPVOID p);\n};\n",
        "debugger.cpp": b"DWORD Debugger::ThreadProcStatic(LPVOID p) { return 0; }\n",
    }
    finding = _finding(
        "debugger.h", "ThreadProcStatic", "function", 3, 3, DeadCodeKind.UNUSED_INTERNAL
    )
    assert _kept([finding], source) == set()


def test_name_written_nowhere_else_is_kept():
    source = {
        "util.c": b"int unused_helper(void)\n{\n    return unused_helper_impl;\n}\n",
        "main.c": b"int main(void) { return 0; }\n",
    }
    assert _kept([_finding("util.c", "unused_helper", "function", 1, 4)], source) == {
        "unused_helper"
    }


def test_other_languages_are_untouched():
    source = {"a.py": b"def helper():\n    pass\n", "b.py": b"helper()\n"}
    assert _kept([_finding("a.py", "helper", "function", 1, 2)], source) == {"helper"}


def test_analyzer_drops_the_typedef_tag_end_to_end(tmp_path: Path):
    symbols = [
        {
            "name": "_ARM64_VFP_STATE",
            "kind": "struct",
            "visibility": "public",
            "decorators": [],
            "start_line": 3,
            "end_line": 7,
            "language": "cpp",
        },
        {
            "name": "Offset",
            "kind": "function",
            "visibility": "public",
            "decorators": [],
            "start_line": 9,
            "end_line": 12,
            "language": "cpp",
        },
    ]
    graph = _build_graph({"unwinder.cpp": {"language": "cpp", "symbols": symbols}})
    report = DeadCodeAnalyzer(graph, source_map={"unwinder.cpp": _UNWINDER}).analyze(
        {"detect_unreachable_files": False, "detect_zombie_packages": False, "min_confidence": 0.0}
    )
    assert {f.symbol_name for f in report.findings} == {"Offset"}


def test_header_prototype_and_comment_banner_are_not_uses():
    # ZoomIt's ``ConvertToUnicode`` and powerrename's ``SetRegBoolean``: the
    # only other mentions are a banner comment and the header prototype.
    source = {
        "Helpers.cpp": b"// SetRegBoolean\nvoid SetRegBoolean(bool v)\n{\n}\n",
        "Helpers.h": b"#pragma once\nvoid SetRegBoolean(bool v);\n",
        "Other.cpp": b'const char* s = "http://x"; // SetRegBoolean is unused\n',
    }
    declarations = {"SetRegBoolean": [("Helpers.h", 2, 2), ("Helpers.cpp", 2, 4)]}
    finding = _finding("Helpers.cpp", "SetRegBoolean", "function", 2, 4)
    assert _kept([finding], source, declarations=declarations) == {"SetRegBoolean"}


def test_com_interface_method_is_not_reported():
    symbols = [
        {
            "name": "GetTitle",
            "kind": "function",
            "visibility": "public",
            "decorators": [],
            "start_line": 1,
            "end_line": 4,
            "language": "cpp",
            "signature": "GetTitle(_In_opt_ IShellItemArray* items, PWSTR* name) -> IFACEMETHODIMP",
        }
    ]
    graph = _build_graph({"dllmain.cpp": {"language": "cpp", "symbols": symbols}})
    report = DeadCodeAnalyzer(graph).analyze(
        {"detect_unreachable_files": False, "detect_zombie_packages": False, "min_confidence": 0.0}
    )
    assert report.findings == []


def test_documentation_and_prose_strings_are_not_uses():
    # aria2's unused ``Supported`` functor: the name is otherwise written only in
    # an HTTP status string and a changelog.
    source = {
        "src/MetalinkEntry.cc": b"namespace {\nclass Supported {\n};\n}\n",
        "src/HttpServer.cc": b'const char* s = "505 HTTP Version Not Supported";\n',
        "README.md": b"Supported protocols: HTTP, FTP\n",
    }
    finding = _finding("src/MetalinkEntry.cc", "Supported", "class", 2, 3)
    assert _kept([finding], source) == {"Supported"}


def test_one_word_string_is_a_use():
    # ``GetProcAddress(h, "FreeString")`` names the export it loads.
    source = {
        "Wrapper.cpp": b"void FreeString(wchar_t* s)\n{\n}\n",
        "Loader.cpp": b'auto f = GetProcAddress(h, "FreeString");\n',
    }
    assert _kept([_finding("Wrapper.cpp", "FreeString", "function", 1, 3)], source) == set()
