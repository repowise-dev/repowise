"""Unit tests for the unified ASTParser.

Tests parse inline byte strings so no filesystem I/O is needed.
Covers Python, TypeScript, Go, Rust, Java, C++ — one test class per language.
"""

from __future__ import annotations

import pytest

from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.parser._helpers import _make_file_info

CPP_SOURCE = b"""#include "calculator.hpp"
#include <stdexcept>
#include <string>

namespace sample {

double Calculator::add(double x, double y) {
    return x + y;
}

double Calculator::divide(double x, double y) {
    if (y == 0.0) {
        throw std::invalid_argument("Division by zero");
    }
    return x / y;
}

}  // namespace sample
"""


CPP_HEADER_SOURCE = b"""#pragma once

#include <vector>
#include "models.hpp"

namespace sample {

class Calculator {
public:
    double add(double x, double y);
    double subtract(double x, double y);
    double divide(double x, double y);

private:
    std::vector<int> history_;
};

}  // namespace sample
"""


CPP_EXPORT_MACRO_TYPES = b"""#define MYLIB_EXPORT

struct MYLIB_EXPORT WriteOptions {
    WriteOptions() = default;
    bool sync = false;
};

class MYLIB_EXPORT ClientOptions {
    bool enabled = true;
};

struct PlainOptions {
    PlainOptions() = default;
};
"""


CPP_EXPORT_MACRO_FORWARD_DECLARATIONS = b"""#define MYLIB_EXPORT
#define UNRELATED_MACRO 1

struct MYLIB_EXPORT Fwd;
class MYLIB_EXPORT Fwd2;
struct PlainFwd;
class PlainFwd2;
"""


CPP_CONDITIONAL_EXPORT_MACRO_TYPE = b"""#if defined(_WIN32)
#define COFFEE_EXPORT __declspec(dllexport)
#else
#define COFFEE_EXPORT
#endif

struct COFFEE_EXPORT Brewer {
    void brew();
};
"""


CPP_EXPORT_MACRO_FORWARD_DECLARATION_CONTEXTS = b"""#define API

namespace sdk {
struct API Namespaced;
template <typename T> class API Generic;

class Outer {
    struct API Nested;
};
}
"""


SPACED_CPP_UNDEF_FORWARD_DECLARATIONS = (
    b"""#define API
# undef API
struct API Fwd;
""",
    b"""#define API
#\tundef API
struct API Fwd;
""",
    b"""#define API
  # \t undef API // trailing text is not part of the macro name
struct API Fwd;
""",
)


CPP_EXPORT_MACRO_LIFETIME_CASES = (
    (
        b"""#define API Existing
#define API
struct API Fwd;
""",
        {
            ("API", "variable", 1, False),
            ("Fwd", "struct", 3, True),
        },
    ),
    (
        b"""#define API
#define API Existing
struct API Fwd;
""",
        {
            ("API", "variable", 1, False),
            ("API", "variable", 2, False),
        },
    ),
    (
        b"""#define API
#undef API
#define API
struct API Fwd;
""",
        {
            ("API", "variable", 1, False),
            ("Fwd", "struct", 4, True),
        },
    ),
    (
        b"""#define API(value)
#define API
struct API Fwd;
""",
        {
            ("API", "function", 1, False),
            ("Fwd", "struct", 3, True),
        },
    ),
    (
        b"""#define API
#define API(value)
struct API Fwd;
""",
        {
            ("API", "variable", 1, False),
            ("API", "function", 2, False),
        },
    ),
    (
        b"""#define API
struct API Early;
#undef API
struct API Late;
""",
        {
            ("Early", "struct", 2, True),
        },
    ),
    (
        b"""#define API
#undef API_EXTRA
struct API Fwd;
""",
        {
            ("Fwd", "struct", 3, True),
        },
    ),
    (
        b"""#define API /* no-op on this platform */
struct API Fwd;
""",
        {
            ("Fwd", "struct", 2, True),
        },
    ),
    (
        b"""#define API
struct API First;
#define API
class API Second;
""",
        {
            ("First", "struct", 2, True),
            ("Second", "class", 4, True),
        },
    ),
)


CPP_EXPORT_MACRO_BRANCH_CONTEXT_CASES = (
    (
        b"""#ifndef SDK_HEADER_H
#define SDK_HEADER_H
#define API
struct API Fwd;
#endif
""",
        {
            ("SDK_HEADER_H", "variable", 2, False),
            ("Fwd", "struct", 4, True),
        },
    ),
    (
        b"""#ifdef FEATURE
#define API
#if INNER
struct API Fwd;
#endif
#endif
""",
        {
            ("Fwd", "struct", 4, True),
        },
    ),
    (
        b"""#ifdef FEATURE
#define API
#else
struct API Fwd;
#endif
""",
        {
            ("API", "variable", 2, False),
        },
    ),
    (
        b"""#ifdef FEATURE
#define API
#endif
struct API Fwd;
""",
        {
            ("API", "variable", 2, False),
        },
    ),
    (
        b"""#if FIRST
#define API
struct API First;
#elif SECOND
#define API
struct API Second;
#else
#define API
class API Last;
#endif
""",
        {
            ("First", "struct", 3, True),
            ("Second", "struct", 6, True),
            ("Last", "class", 9, True),
        },
    ),
    (
        b"""#if FIRST
#define API
struct API First;
#elif SECOND
struct API WrongSecond;
#else
class API WrongLast;
#endif
""",
        {
            ("First", "struct", 3, True),
        },
    ),
)


CPP_EXPORT_MACRO_STATE_BOUNDARY_CASES = (
    (
        b"""#define API
#include "redefine.hpp"
struct API Fwd;
""",
        {("API", "variable", 1, False)},
    ),
    (
        b"""#define API
#include PLATFORM_HEADER
struct API Fwd;
""",
        {("API", "variable", 1, False)},
    ),
    (
        b"""#define API
#include_next <possibly_redefines.hpp>
struct API Fwd;
""",
        {("API", "variable", 1, False)},
    ),
    (
        b"""#define API
#import "possibly_redefines.hpp"
struct API Fwd;
""",
        {("API", "variable", 1, False)},
    ),
    (
        b"""#define API Existing
#pragma push_macro("API")
#undef API
#define API
#pragma pop_macro("API")
struct API Fwd;
""",
        {
            ("API", "variable", 1, False),
            ("API", "variable", 4, False),
        },
    ),
    (
        b"""#pragma push_macro("API")
#define API
#pragma pop_macro("API")
struct API Fwd;
""",
        {("API", "variable", 2, False)},
    ),
    (
        b'#define API Existing\n#pragma push_macro("API")\n#undef API\n#define API\n#pragma pop_ma\\\ncro("API")\nstruct API Fwd;\n',
        {
            ("API", "variable", 1, False),
            ("API", "variable", 4, False),
        },
    ),
    (
        b'#define API Existing\n#pragma push_macro("API")\n#undef API\n#define API\n#pragma pop_macro("A\\\nPI")\nstruct API Fwd;\n',
        {
            ("API", "variable", 1, False),
            ("API", "variable", 4, False),
        },
    ),
    (
        b"""#define API Existing
_Pragma("push_macro(\"API\")")
#undef API
#define API
_Pragma("pop_macro(\"API\")")
struct API Fwd;
""",
        {
            ("API", "variable", 1, False),
            ("API", "variable", 4, False),
        },
    ),
    (
        b"""#define API Existing
__pragma(push_macro("API"))
#undef API
#define API
__pragma(pop_macro("API"))
struct API Fwd;
""",
        {
            ("API", "variable", 1, False),
            ("API", "variable", 4, False),
        },
    ),
    (
        b"""#define API Existing
#pragma push_macro("API")
#undef API
#define API
#define RESTORE_API() _Pragma("pop_macro(\"API\")")
RESTORE_API()
struct API Fwd;
""",
        {
            ("API", "variable", 1, False),
            ("API", "variable", 4, False),
            ("RESTORE_API", "function", 5, False),
        },
    ),
    (
        b"""#define API Existing
#pragma push_macro("API")
#undef API
#define API
#pragma push_macro("API")
#define API ExistingAgain
#define RESTORE_API() _Pragma("pop_macro(\"API\")")
RESTORE_API()
#pragma pop_macro("API")
struct API Fwd;
""",
        {
            ("API", "variable", 1, False),
            ("API", "variable", 4, False),
            ("API", "variable", 6, False),
            ("RESTORE_API", "function", 7, False),
        },
    ),
    (
        b"""#define API Existing
#pragma push_macro("API")
#undef API
#define API
#define RESTORE_API() __pragma(pop_macro("API"))
RESTORE_API()
struct API Fwd;
""",
        {
            ("API", "variable", 1, False),
            ("API", "variable", 4, False),
            ("RESTORE_API", "function", 5, False),
        },
    ),
    (
        b"""#define API Existing
#pragma push_macro("API")
#undef API
#define API
#define DO_PRAGMA(value) _Pragma(#value)
DO_PRAGMA(pop_macro("API"))
struct API Fwd;
""",
        {
            ("API", "variable", 1, False),
            ("API", "variable", 4, False),
            ("DO_PRAGMA", "function", 5, False),
        },
    ),
    (
        b"""#define API Existing
#pragma push_macro("API")
#undef API
#define API
#define RESTORE_API _Pragma("pop_macro(\"API\")")
RESTORE_API;
struct API Fwd;
""",
        {
            ("API", "variable", 1, False),
            ("API", "variable", 4, False),
            ("RESTORE_API", "variable", 5, False),
        },
    ),
    (
        b"""#define API Existing
#pragma push_macro("API")
#undef API
#define API
#define RESTORE_API _Pragma("pop_macro(\"API\")") 1
int restored[RESTORE_API];
struct API Fwd;
""",
        {
            ("API", "variable", 1, False),
            ("API", "variable", 4, False),
            ("RESTORE_API", "variable", 5, False),
        },
    ),
    (
        b"""#define API
#pragma push_macro("API")
#undef API
#pragma pop_macro("API")
struct API Fwd;
""",
        {("Fwd", "struct", 5, True)},
    ),
    (
        b"""#define API
_Pragma("push_macro(\"API\")")
#undef API
_Pragma("pop_macro(\"API\")")
struct API Fwd;
""",
        {("Fwd", "struct", 5, True)},
    ),
    (
        b"""#define API
#pragma pop_macro("API")
struct API Fwd;
""",
        {("Fwd", "struct", 3, True)},
    ),
    (
        b"""#define API
_Pragma("pop_macro(\"API\")")
struct API Fwd;
""",
        {("Fwd", "struct", 3, True)},
    ),
    (
        b"""#define API
#define RESTORE_API() _Pragma("pop_macro(\"API\")")
struct API Fwd;
""",
        {
            ("RESTORE_API", "function", 2, False),
            ("Fwd", "struct", 3, True),
        },
    ),
    (
        b"""#define API Existing
#pragma push_macro("API")
#undef API
#define API
#define RESTORE_API() _Pragma("pop_macro(\"API\")")
#define RESTORE_ALIAS() RESTORE_API()
RESTORE_ALIAS()
struct API Fwd;
""",
        {
            ("API", "variable", 1, False),
            ("API", "variable", 4, False),
            ("RESTORE_API", "function", 5, False),
            ("RESTORE_ALIAS", "function", 6, False),
        },
    ),
    (
        b"""#define API Existing
#pragma push_macro("API")
#undef API
#define API
#define RESTORE_API() _Pragma("pop_macro(\"API\")")
#define RESTORE_WRAPPED() (RESTORE_API())
RESTORE_WRAPPED()
struct API Fwd;
""",
        {
            ("API", "variable", 1, False),
            ("API", "variable", 4, False),
            ("RESTORE_API", "function", 5, False),
            ("RESTORE_WRAPPED", "function", 6, False),
        },
    ),
    (
        b"""#define API Existing
#pragma push_macro("API")
#undef API
#define API
#define RESTORE_API() _Pragma("pop_macro(\"API\")")
#define APPLY(value) value()
APPLY(RESTORE_API)
struct API Fwd;
""",
        {
            ("API", "variable", 1, False),
            ("API", "variable", 4, False),
            ("RESTORE_API", "function", 5, False),
            ("APPLY", "function", 6, False),
        },
    ),
    (
        b"""#define API
#pragma push_macro("API")
#include "possibly_mutates_macro_stack.hpp"
#pragma pop_macro("API")
struct API Fwd;
""",
        {("API", "variable", 1, False)},
    ),
    (
        b"""#define API
#include "possibly_mutates_macro_stack.hpp"
#pragma push_macro("API")
#undef API
#define API Existing
#pragma pop_macro("API")
struct API Fwd;
""",
        {("API", "variable", 1, False), ("API", "variable", 5, False)},
    ),
    (
        b"""#include "possibly_mutates_macro_stack.hpp"
#define API
#pragma push_macro("API")
#undef API
#define API Existing
#pragma pop_macro("API")
struct API Fwd;
""",
        {("API", "variable", 5, False), ("Fwd", "struct", 7, True)},
    ),
    (
        b"""#define API
#pragma push_macro("API")
#define API Existing
#pragma push_macro("API")
#undef API
#pragma pop_macro("API")
#pragma pop_macro("API")
struct API Fwd;
""",
        {
            ("API", "variable", 3, False),
            ("Fwd", "struct", 8, True),
        },
    ),
    (
        b"#define API\n#undef A\\\nPI\nstruct API Fwd;\n",
        {("API", "variable", 1, False)},
    ),
    (
        b"#define API\n#un\\\ndef API\nstruct API Fwd;\n",
        {("API", "variable", 1, False)},
    ),
    (
        """#define ÉXPORT
#undef ÉXPORT
struct ÉXPORT Fwd;
""".encode(),
        {("ÉXPORT", "variable", 1, False)},
    ),
    (
        """#define ÉXPORT
#undef \\u00C9XPORT
struct ÉXPORT Fwd;
""".encode(),
        {("ÉXPORT", "variable", 1, False)},
    ),
    (
        """#define \\u00C9XPORT
#undef ÉXPORT
struct ÉXPORT Fwd;
""".encode(),
        {("\\u00C9XPORT", "variable", 1, False)},
    ),
    (
        """#define \\u00C9XPORT
struct ÉXPORT Fwd;
""".encode(),
        {("Fwd", "struct", 2, True)},
    ),
    (
        b"""#define API
#include "possibly_redefines.hpp"
#define API
struct API Fwd;
""",
        {
            ("API", "variable", 1, False),
            ("Fwd", "struct", 4, True),
        },
    ),
    (
        b"""#define API
#pragma pop_macro("OTHER")
struct API Fwd;
""",
        {("Fwd", "struct", 3, True)},
    ),
    (
        b"""#define API
#pragma pop_macro("API")
#define API
struct API Fwd;
""",
        {
            ("API", "variable", 1, False),
            ("Fwd", "struct", 4, True),
        },
    ),
)


UNSAFE_CPP_EXPORT_MACRO_FORWARD_DECLARATIONS = (
    (
        b"""struct MYLIB_EXPORT Fwd;
#define MYLIB_EXPORT
""",
        "MYLIB_EXPORT",
        "variable",
    ),
    (
        b"""#define MYLIB_EXPORT
#undef MYLIB_EXPORT
struct MYLIB_EXPORT Fwd;
""",
        "MYLIB_EXPORT",
        "variable",
    ),
    (
        b"""#if 0
#define MYLIB_EXPORT
#endif
struct MYLIB_EXPORT Fwd;
""",
        "MYLIB_EXPORT",
        "variable",
    ),
    (
        b"""#define LEGACY_API Existing
struct LEGACY_API Fwd;
""",
        "LEGACY_API",
        "variable",
    ),
    (
        b"""#define MYLIB_EXPORT(value)
struct MYLIB_EXPORT Fwd;
""",
        "MYLIB_EXPORT",
        "function",
    ),
    (
        b"""#define MYLIB_EXPORT
#if FEATURE
#undef MYLIB_EXPORT
#endif
struct MYLIB_EXPORT Fwd;
""",
        "MYLIB_EXPORT",
        "variable",
    ),
    (
        b"""#define MYLIB_EXPORT
#if FEATURE
#define MYLIB_EXPORT Existing
#endif
struct MYLIB_EXPORT Fwd;
""",
        "MYLIB_EXPORT",
        "variable",
    ),
)


class TestCppParser:
    def test_finds_class_in_header(self, parser: ASTParser) -> None:
        fi = _make_file_info("cpp_pkg/calculator.hpp", "cpp")
        result = parser.parse_file(fi, CPP_HEADER_SOURCE)
        classes = [s for s in result.symbols if s.kind == "class"]
        assert any(s.name == "Calculator" for s in classes)

    def test_finds_functions_in_source(self, parser: ASTParser) -> None:
        fi = _make_file_info("cpp_pkg/calculator.cpp", "cpp")
        result = parser.parse_file(fi, CPP_SOURCE)
        # ``Calculator::add`` style qualified definitions now resolve to
        # ``kind=method`` with ``parent_name=Calculator``; free functions
        # stay ``kind=function``. Either way we expect symbols to land.
        callable_symbols = [s for s in result.symbols if s.kind in ("function", "method")]
        assert len(callable_symbols) >= 1

    def test_parses_includes(self, parser: ASTParser) -> None:
        fi = _make_file_info("cpp_pkg/calculator.cpp", "cpp")
        result = parser.parse_file(fi, CPP_SOURCE)
        assert len(result.imports) >= 2
        module_paths = [i.module_path for i in result.imports]
        assert any("calculator.hpp" in p or "stdexcept" in p for p in module_paths)

    def test_finds_types_declared_through_export_macros(self, parser: ASTParser) -> None:
        fi = _make_file_info("cpp_pkg/options.hpp", "cpp")
        result = parser.parse_file(fi, CPP_EXPORT_MACRO_TYPES)
        symbols = {(symbol.parent_name, symbol.name): symbol for symbol in result.symbols}

        assert symbols[(None, "WriteOptions")].kind == "struct"
        assert symbols[(None, "WriteOptions")].is_declaration is False
        assert symbols[("WriteOptions", "WriteOptions")].kind == "method"
        assert symbols[(None, "ClientOptions")].kind == "class"
        assert symbols[(None, "ClientOptions")].is_declaration is False
        assert symbols[(None, "PlainOptions")].kind == "struct"
        assert symbols[("PlainOptions", "PlainOptions")].kind == "method"
        assert not any(symbol.name == "MYLIB_EXPORT" for symbol in result.symbols)

    def test_preserves_conditional_export_macro_body_recovery(self, parser: ASTParser) -> None:
        fi = _make_file_info("cpp_pkg/conditional_options.hpp", "cpp")
        result = parser.parse_file(fi, CPP_CONDITIONAL_EXPORT_MACRO_TYPE)

        assert {
            (symbol.parent_name, symbol.name, symbol.kind, symbol.is_declaration)
            for symbol in result.symbols
        } == {
            (None, "Brewer", "struct", False),
            ("Brewer", "brew", "method", True),
        }

    @pytest.mark.parametrize(
        "source",
        (
            "#define \\u00C9XPORT\nstruct ÉXPORT Body { int x; };\n".encode(),
            "#define ÉXPORT\nstruct \\u00C9XPORT Body { int x; };\n".encode(),
        ),
        ids=("ucn-definition", "ucn-use"),
    )
    def test_normalizes_ucn_spellings_for_export_macro_body_recovery(
        self, parser: ASTParser, source: bytes
    ) -> None:
        fi = _make_file_info("cpp_pkg/ucn_body.hpp", "cpp")
        result = parser.parse_file(fi, source)

        assert {
            (symbol.name, symbol.kind, symbol.start_line, symbol.is_declaration)
            for symbol in result.symbols
        } == {("Body", "struct", 2, False)}

    def test_finds_types_forward_declared_through_export_macros(self, parser: ASTParser) -> None:
        fi = _make_file_info("cpp_pkg/forward.hpp", "cpp")
        result = parser.parse_file(fi, CPP_EXPORT_MACRO_FORWARD_DECLARATIONS)
        expected_symbols = {
            (None, "Fwd", "struct", True),
            (None, "Fwd2", "class", True),
            (None, "PlainFwd", "struct", True),
            (None, "PlainFwd2", "class", True),
            (None, "UNRELATED_MACRO", "variable", False),
        }

        assert len(result.symbols) == len(expected_symbols)
        assert {
            (symbol.parent_name, symbol.name, symbol.kind, symbol.is_declaration)
            for symbol in result.symbols
        } == expected_symbols

    def test_ignores_unrelated_identifiers_after_include_for_forward_recovery(
        self, parser: ASTParser
    ) -> None:
        source = b"""#include <vector>
#define API
int helper(int x) { return x; }
struct API Fwd;
"""
        fi = _make_file_info("cpp_pkg/forward_after_helper.hpp", "cpp")
        result = parser.parse_file(fi, source)

        assert {
            (symbol.name, symbol.kind, symbol.start_line, symbol.is_declaration)
            for symbol in result.symbols
        } == {
            ("helper", "function", 3, False),
            ("Fwd", "struct", 4, True),
        }

    def test_ignores_unrelated_calls_after_include_for_forward_recovery(
        self, parser: ASTParser
    ) -> None:
        source = b"""#include <vector>
#define API
int value = transform(1);
struct API Fwd;
"""
        fi = _make_file_info("cpp_pkg/forward_after_call.hpp", "cpp")
        result = parser.parse_file(fi, source)

        assert {
            (symbol.name, symbol.kind, symbol.start_line, symbol.is_declaration)
            for symbol in result.symbols
        } == {("Fwd", "struct", 4, True)}

    def test_preserves_local_macro_stack_hazard_after_include(self, parser: ASTParser) -> None:
        source = b"""#include <vector>
#define API
#define RESTORE_API() _Pragma("pop_macro(\\"API\\")")
RESTORE_API()
struct API Fwd;
"""
        fi = _make_file_info("cpp_pkg/forward_after_wrapper.hpp", "cpp")
        result = parser.parse_file(fi, source)

        assert {
            (symbol.name, symbol.kind, symbol.start_line, symbol.is_declaration)
            for symbol in result.symbols
        } == {
            ("API", "variable", 2, False),
            ("RESTORE_API", "function", 3, False),
        }

    def test_does_not_reinterpret_tag_variable_declaration_as_export_macro(
        self, parser: ASTParser
    ) -> None:
        fi = _make_file_info("cpp_pkg/tag_variable.hpp", "cpp")
        result = parser.parse_file(fi, b"struct API Fwd;\n")

        assert not any(symbol.name == "Fwd" for symbol in result.symbols)

    def test_finds_export_macro_forward_declarations_in_cpp_contexts(
        self, parser: ASTParser
    ) -> None:
        fi = _make_file_info("cpp_pkg/forward_contexts.hpp", "cpp")
        result = parser.parse_file(fi, CPP_EXPORT_MACRO_FORWARD_DECLARATION_CONTEXTS)

        assert {
            (symbol.parent_name, symbol.name, symbol.kind, symbol.is_declaration)
            for symbol in result.symbols
        } == {
            (None, "sdk", "module", False),
            (None, "Namespaced", "struct", True),
            (None, "Generic", "class", True),
            (None, "Outer", "class", False),
            ("Outer", "Nested", "struct", True),
        }

    def test_includes_template_lines_in_export_macro_forward_declaration_range(
        self, parser: ASTParser
    ) -> None:
        source = b"""#define API
template <typename T>
class API Generic;
"""
        fi = _make_file_info("cpp_pkg/template_forward.hpp", "cpp")
        result = parser.parse_file(fi, source)

        generic = next(symbol for symbol in result.symbols if symbol.name == "Generic")
        assert (generic.start_line, generic.end_line, generic.is_declaration) == (2, 3, True)

    @pytest.mark.parametrize(
        ("source", "macro_name", "macro_kind"),
        UNSAFE_CPP_EXPORT_MACRO_FORWARD_DECLARATIONS,
        ids=(
            "defined-later",
            "undefined",
            "conditional",
            "non-empty-alias",
            "function-like",
            "conditional-undef-before-use",
            "conditional-redefinition-before-use",
        ),
    )
    def test_does_not_guess_export_macro_forward_declarations(
        self, parser: ASTParser, source: bytes, macro_name: str, macro_kind: str
    ) -> None:
        fi = _make_file_info("cpp_pkg/ambiguous_forward.hpp", "cpp")
        result = parser.parse_file(fi, source)

        assert not any(symbol.name == "Fwd" for symbol in result.symbols)
        assert any(
            symbol.name == macro_name and symbol.kind == macro_kind for symbol in result.symbols
        )

    @pytest.mark.parametrize(
        "source",
        SPACED_CPP_UNDEF_FORWARD_DECLARATIONS,
        ids=("space-after-hash", "tab-after-hash", "leading-and-mixed-space"),
    )
    def test_respects_spaced_undef_before_export_macro_forward_declaration(
        self, parser: ASTParser, source: bytes
    ) -> None:
        fi = _make_file_info("cpp_pkg/spaced_undef.hpp", "cpp")
        result = parser.parse_file(fi, source)

        assert {
            (symbol.name, symbol.kind, symbol.start_line, symbol.is_declaration)
            for symbol in result.symbols
        } == {("API", "variable", 1, False)}

    def test_recovers_export_macro_forward_declaration_inside_conditional_branch(
        self, parser: ASTParser
    ) -> None:
        source = b"""#define API
#ifdef FEATURE
struct API Fwd;
#endif
"""
        fi = _make_file_info("cpp_pkg/conditional_forward.hpp", "cpp")
        result = parser.parse_file(fi, source)

        assert {
            (symbol.parent_name, symbol.name, symbol.kind, symbol.is_declaration)
            for symbol in result.symbols
        } == {(None, "Fwd", "struct", True)}

    @pytest.mark.parametrize(
        ("source", "expected_symbols"),
        CPP_EXPORT_MACRO_LIFETIME_CASES,
        ids=(
            "non-empty-to-empty",
            "empty-to-non-empty",
            "undef-then-redefine",
            "function-to-object",
            "object-to-function",
            "undef-after-use",
            "unrelated-undef",
            "comment-only-empty-macro",
            "redefine-between-uses",
        ),
    )
    def test_tracks_export_macro_lifetime_for_forward_declarations(
        self,
        parser: ASTParser,
        source: bytes,
        expected_symbols: set[tuple[str, str, int, bool]],
    ) -> None:
        fi = _make_file_info("cpp_pkg/macro_lifetime.hpp", "cpp")
        result = parser.parse_file(fi, source)

        assert {
            (symbol.name, symbol.kind, symbol.start_line, symbol.is_declaration)
            for symbol in result.symbols
        } == expected_symbols

    @pytest.mark.parametrize(
        ("source", "expected_symbols"),
        CPP_EXPORT_MACRO_BRANCH_CONTEXT_CASES,
        ids=(
            "include-guard",
            "outer-definition-inner-declaration",
            "definition-in-sibling-branch",
            "definition-in-finished-branch",
            "definitions-in-each-elif-branch",
            "definition-missing-from-elif-branches",
        ),
    )
    def test_requires_compatible_preprocessor_branches_for_forward_declarations(
        self,
        parser: ASTParser,
        source: bytes,
        expected_symbols: set[tuple[str, str, int, bool]],
    ) -> None:
        fi = _make_file_info("cpp_pkg/branch_context.hpp", "cpp")
        result = parser.parse_file(fi, source)

        assert {
            (symbol.name, symbol.kind, symbol.start_line, symbol.is_declaration)
            for symbol in result.symbols
        } == expected_symbols

    @pytest.mark.parametrize(
        ("source", "expected_symbols"),
        CPP_EXPORT_MACRO_STATE_BOUNDARY_CASES,
        ids=(
            "include-literal-invalidates-state",
            "include-macro-invalidates-state",
            "include-next-invalidates-state",
            "import-invalidates-state",
            "pragma-pop-restores-non-empty",
            "pragma-pop-restores-undefined",
            "line-spliced-pop-macro-keyword",
            "line-spliced-pop-macro-target",
            "pragma-operator-pop-restores-state",
            "msvc-pragma-pop-restores-state",
            "macro-wrapped-pragma-restores-non-empty",
            "macro-wrapped-pop-taints-later-direct-pop",
            "macro-wrapped-msvc-pragma-restores-non-empty",
            "two-level-macro-wrapped-pragma-restores-non-empty",
            "object-macro-wrapped-pragma-restores-non-empty",
            "object-wrapper-in-array-bound-restores-non-empty",
            "pragma-pop-restores-empty",
            "pragma-operator-pop-restores-empty",
            "unmatched-pragma-pop-preserves-empty",
            "unmatched-pragma-operator-pop-preserves-empty",
            "unused-pragma-wrapper-preserves-known-state",
            "aliased-pragma-wrapper-invalidates-state",
            "parenthesized-alias-wrapper-invalidates-state",
            "parameter-relayed-wrapper-invalidates-state",
            "include-invalidates-existing-macro-stack",
            "push-after-include-snapshots-unknown-state",
            "local-definition-and-push-after-include-restore-empty",
            "nested-pragma-stack-restores-empty",
            "line-spliced-undef-name",
            "line-spliced-undef-directive",
            "unicode-undef-name",
            "ucn-undef-name",
            "ucn-definition-literal-undef",
            "ucn-definition-literal-use",
            "definition-after-include-restores-known-state",
            "unrelated-pop-does-not-invalidate-state",
            "definition-after-pop-restores-known-state",
        ),
    )
    def test_treats_uncertain_macro_state_as_unsafe_for_forward_recovery(
        self,
        parser: ASTParser,
        source: bytes,
        expected_symbols: set[tuple[str, str, int, bool]],
    ) -> None:
        fi = _make_file_info("cpp_pkg/macro_state_boundary.hpp", "cpp")
        result = parser.parse_file(fi, source)

        assert {
            (symbol.name, symbol.kind, symbol.start_line, symbol.is_declaration)
            for symbol in result.symbols
        } == expected_symbols
