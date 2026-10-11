"""Tests for _is_symbol_deprecated and its effect on confidence scoring.

The real deprecation signal in most codebases is an annotation, not a name
suffix. These tests verify that the full decorator surface (Python, Java/Kotlin,
Rust inner-attr, C# stripped attr, C++ stripped attr, Swift) is recognised and
lowers confidence to 0.3 — placing the finding below the default min_confidence
floor of 0.4 so it shows up in hidden_below_threshold rather than in the
returned findings.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.dead_code import DeadCodeAnalyzer
from repowise.core.analysis.dead_code.analyzer import _is_symbol_deprecated
from tests.unit.dead_code._helpers import _build_graph, _old_date

# ---------------------------------------------------------------------------
# Unit tests for _is_symbol_deprecated
# ---------------------------------------------------------------------------


class TestIsSymbolDeprecated:
    """Pure-function tests — no graph needed."""

    def test_name_suffix_DEPRECATED(self):
        assert _is_symbol_deprecated("process_data_DEPRECATED", []) is True

    def test_name_suffix_LEGACY(self):
        assert _is_symbol_deprecated("config_LEGACY", []) is True

    def test_name_suffix_COMPAT(self):
        assert _is_symbol_deprecated("parse_COMPAT", []) is True

    def test_python_at_deprecated(self):
        assert _is_symbol_deprecated("process_data", ["@deprecated"]) is True

    def test_python_typing_deprecated(self):
        assert _is_symbol_deprecated("process_data", ["@typing.deprecated"]) is True

    def test_python_warnings_deprecated(self):
        assert _is_symbol_deprecated("process_data", ["@warnings.deprecated"]) is True

    def test_java_Deprecated(self):
        # Java / Kotlin annotation — capital D
        assert _is_symbol_deprecated("processData", ["@Deprecated"]) is True

    def test_kotlin_Deprecated(self):
        assert _is_symbol_deprecated("processData", ["@kotlin.Deprecated"]) is True

    def test_scala_deprecated(self):
        assert _is_symbol_deprecated("processData", ["@deprecated"]) is True

    def test_swift_available_deprecated(self):
        # @available(*, deprecated) — call stripped, base is "available"
        # This should NOT match; the actual deprecated signal here is via the
        # argument — the helper only strips the outer call args.
        # Keeping this explicit so behaviour is documented.
        result = _is_symbol_deprecated("func", ["@available(*, deprecated)"])
        # "available" is not in _DEPRECATED_DECORATOR_BASES, so this is False.
        assert result is False

    def test_rust_inner_attr_no_at(self):
        # Rust: parser.py strips #[ ] and stores the inner content directly
        assert _is_symbol_deprecated("my_fn", ["deprecated"]) is True

    def test_rust_inner_attr_with_args(self):
        # #[deprecated(since = "1.0")] → "deprecated(since = \"1.0\")"
        assert _is_symbol_deprecated("my_fn", ['deprecated(since = "1.0")']) is True

    def test_csharp_Obsolete(self):
        # C#: parser.py strips [ ] and stores "Obsolete"
        assert _is_symbol_deprecated("MyMethod", ["Obsolete"]) is True

    def test_csharp_System_Obsolete(self):
        assert _is_symbol_deprecated("MyMethod", ["System.Obsolete"]) is True

    def test_csharp_Obsolete_with_message(self):
        # "[Obsolete(\"Use Foo instead\")]" → "Obsolete(\"Use Foo instead\")"
        assert _is_symbol_deprecated("MyMethod", ['Obsolete("Use Foo instead")']) is True

    def test_cpp_double_bracket_deprecated(self):
        # C++: parser.py strips [[ ]] and stores "deprecated"
        assert _is_symbol_deprecated("my_func", ["deprecated"]) is True

    def test_cpp_deprecated_with_reason(self):
        # [[deprecated("use bar() instead")]] → "deprecated(\"use bar() instead\")"
        assert _is_symbol_deprecated("my_func", ['deprecated("use bar() instead")']) is True

    def test_unrelated_decorator_is_not_deprecated(self):
        assert _is_symbol_deprecated("process_data", ["@property"]) is False
        assert _is_symbol_deprecated("process_data", ["@app.route"]) is False
        assert _is_symbol_deprecated("process_data", ["@staticmethod"]) is False

    def test_empty_decorators_no_suffix_is_not_deprecated(self):
        assert _is_symbol_deprecated("process_data", []) is False

    def test_multiple_decorators_one_deprecated(self):
        assert _is_symbol_deprecated("fn", ["@staticmethod", "@deprecated"]) is True


# ---------------------------------------------------------------------------
# Integration tests — full analyzer path
# ---------------------------------------------------------------------------


def _stale_file_node(name: str, *, symbols: list | None = None) -> dict:
    return {
        name: {
            "is_entry_point": False,
            "is_test": False,
            "is_api_contract": False,
            "symbol_count": 5,
            "symbols": symbols or [],
        }
    }


def _stale_git_meta(name: str) -> dict:
    return {
        name: {
            "commit_count_90d": 0,
            "last_commit_at": _old_date(days=400),
            "age_days": 400,
            "primary_owner_name": None,
        }
    }


def _symbol(name: str, decorators: list[str]) -> dict:
    return {
        "name": name,
        "kind": "function",
        "visibility": "public",
        "language": "python",
        "decorators": decorators,
        "start_line": 1,
        "end_line": 10,
    }


class TestDeprecatedDecoratorIntegration:
    """End-to-end: the decorator reaches the confidence score via the analyzer."""

    def _run(self, decorator: str, min_confidence: float = 0.4) -> object:
        """Build a minimal graph with one deprecated export and run the analyzer."""
        g = _build_graph(
            nodes={
                **_stale_file_node(
                    "pkg/utils.py",
                    symbols=[_symbol("process_data", [decorator])],
                ),
                "pkg/caller.py": {
                    "is_entry_point": False,
                    "is_test": False,
                    "is_api_contract": False,
                    "symbol_count": 2,
                    "symbols": [],
                },
            },
            edges=[("pkg/caller.py", "pkg/utils.py", {"edge_type": "imports"})],
        )
        git_meta = {**_stale_git_meta("pkg/utils.py"), **_stale_git_meta("pkg/caller.py")}
        analyzer = DeadCodeAnalyzer(g, git_meta_map=git_meta)
        return analyzer.analyze(
            {
                "detect_zombie_packages": False,
                "min_confidence": min_confidence,
            }
        )

    @pytest.mark.parametrize(
        "decorator",
        [
            "@deprecated",
            "@typing.deprecated",
            "@Deprecated",
            # Rust / C# / C++ inner-attr forms reach the graph without "@"
            "deprecated",
            "Obsolete",
        ],
    )
    def test_decorator_scores_confidence_0_3(self, decorator: str):
        report = self._run(decorator, min_confidence=0.0)
        deprecated_findings = [
            f for f in report.findings if f.symbol_name == "process_data"
        ]
        assert len(deprecated_findings) == 1, (
            f"Expected one finding for decorator {decorator!r}; got {deprecated_findings}"
        )
        assert deprecated_findings[0].confidence == pytest.approx(0.3), (
            f"Expected confidence=0.3 for decorator {decorator!r}; "
            f"got {deprecated_findings[0].confidence}"
        )

    @pytest.mark.parametrize(
        "decorator",
        [
            "@deprecated",
            "@Deprecated",
            "deprecated",
            "Obsolete",
        ],
    )
    def test_deprecated_decorator_hidden_under_default_floor(self, decorator: str):
        """Decorated symbols (confidence=0.3) fall below the 0.4 default floor."""
        report = self._run(decorator, min_confidence=0.4)
        deprecated_in_findings = [
            f for f in report.findings if f.symbol_name == "process_data"
        ]
        assert deprecated_in_findings == [], (
            f"Decorated symbol should not appear under default floor; "
            f"decorator={decorator!r}"
        )

    def test_unrelated_decorator_does_not_lower_confidence(self):
        report = self._run("@property", min_confidence=0.0)
        findings = [f for f in report.findings if f.symbol_name == "process_data"]
        assert len(findings) == 1
        assert findings[0].confidence != pytest.approx(0.3), (
            "@property should not lower confidence to 0.3"
        )

    def test_suffix_DEPRECATED_still_works_without_decorator(self):
        """Name-suffix detection (backward compat) is not broken by the new path."""
        g = _build_graph(
            nodes={
                **_stale_file_node(
                    "pkg/utils.py",
                    symbols=[_symbol("process_data_DEPRECATED", [])],
                ),
                "pkg/caller.py": {
                    "is_entry_point": False,
                    "is_test": False,
                    "is_api_contract": False,
                    "symbol_count": 2,
                    "symbols": [],
                },
            },
            edges=[("pkg/caller.py", "pkg/utils.py", {"edge_type": "imports"})],
        )
        git_meta = {**_stale_git_meta("pkg/utils.py"), **_stale_git_meta("pkg/caller.py")}
        analyzer = DeadCodeAnalyzer(g, git_meta_map=git_meta)
        report = analyzer.analyze({"detect_zombie_packages": False, "min_confidence": 0.0})
        findings = [f for f in report.findings if f.symbol_name == "process_data_DEPRECATED"]
        assert len(findings) == 1
        assert findings[0].confidence == pytest.approx(0.3)


# ---------------------------------------------------------------------------
# parse_file roundtrip tests — these are the tests the reviewer asked for.
# They call parse_file on a real code snippet per language and assert on
# symbol.decorators directly, proving the extraction path fires end-to-end
# rather than just testing the string normalization helper in isolation.
# ---------------------------------------------------------------------------


class TestDeprecatedDecoratorParseFile:
    """parse_file roundtrip: decorator text lands in symbol.decorators."""

    def _parser(self):
        from repowise.core.ingestion.parser import ASTParser

        return ASTParser()

    def _make_file_info(self, path: str, language: str):
        from datetime import datetime

        from repowise.core.ingestion.models import FileInfo

        return FileInfo(
            path=path,
            abs_path=f"/tmp/{path}",
            language=language,
            size_bytes=100,
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        )

    # ------------------------------------------------------------------
    # C# — [Obsolete] is child[0] of method_declaration
    # ------------------------------------------------------------------

    def test_csharp_obsolete_lands_in_decorators(self):
        src = b"""
using System;
public class Foo {
    [Obsolete("use Bar instead")]
    public void OldApi() { }

    public void NewApi() { }
}
"""
        fi = self._make_file_info("pkg/Foo.cs", "csharp")
        result = self._parser().parse_file(fi, src)
        old = next((s for s in result.symbols if s.name == "OldApi"), None)
        assert old is not None, "OldApi symbol not found"
        assert any("Obsolete" in d for d in old.decorators), (
            f"Expected 'Obsolete' in decorators; got {old.decorators}"
        )

    def test_csharp_obsolete_is_detected_as_deprecated(self):
        from repowise.core.analysis.dead_code.analyzer import _is_symbol_deprecated

        src = b"""
using System;
public class Foo {
    [Obsolete("use Bar instead")]
    public void OldApi() { }
}
"""
        fi = self._make_file_info("pkg/Foo.cs", "csharp")
        result = self._parser().parse_file(fi, src)
        old = next((s for s in result.symbols if s.name == "OldApi"), None)
        assert old is not None
        assert _is_symbol_deprecated(old.name, old.decorators), (
            f"OldApi should be deprecated; decorators={old.decorators}"
        )

    def test_csharp_no_attribute_has_empty_decorators(self):
        src = b"""
public class Foo {
    public void NewApi() { }
}
"""
        fi = self._make_file_info("pkg/Foo.cs", "csharp")
        result = self._parser().parse_file(fi, src)
        new = next((s for s in result.symbols if s.name == "NewApi"), None)
        assert new is not None
        assert not any("Obsolete" in d for d in new.decorators)

    # ------------------------------------------------------------------
    # C++ — [[deprecated]] is child[0] of function_definition
    # ------------------------------------------------------------------

    def test_cpp_deprecated_lands_in_decorators(self):
        src = b'[[deprecated("use bar() instead")]] void old_api() { }\n'
        fi = self._make_file_info("pkg/api.cpp", "cpp")
        result = self._parser().parse_file(fi, src)
        sym = next((s for s in result.symbols if s.name == "old_api"), None)
        assert sym is not None, "old_api symbol not found"
        assert any("deprecated" in d for d in sym.decorators), (
            f"Expected 'deprecated' in decorators; got {sym.decorators}"
        )

    def test_cpp_deprecated_is_detected_as_deprecated(self):
        from repowise.core.analysis.dead_code.analyzer import _is_symbol_deprecated

        src = b"[[deprecated]] void old_api() { }\n"
        fi = self._make_file_info("pkg/api.cpp", "cpp")
        result = self._parser().parse_file(fi, src)
        sym = next((s for s in result.symbols if s.name == "old_api"), None)
        assert sym is not None
        assert _is_symbol_deprecated(sym.name, sym.decorators), (
            f"old_api should be deprecated; decorators={sym.decorators}"
        )

    def test_cpp_no_attribute_has_empty_decorators(self):
        src = b"void new_api() { }\n"
        fi = self._make_file_info("pkg/api.cpp", "cpp")
        result = self._parser().parse_file(fi, src)
        sym = next((s for s in result.symbols if s.name == "new_api"), None)
        assert sym is not None
        assert not any("deprecated" in d for d in sym.decorators)

    # ------------------------------------------------------------------
    # Java — bare @Deprecated (the most common real-world case)
    # ------------------------------------------------------------------

    def test_java_bare_deprecated_lands_in_decorators(self):
        """Regression: bare @Deprecated was silently ignored before blob tokenization."""
        src = b"""
public class Service {
    @Deprecated
    public void processData() { }

    public void newProcess() { }
}
"""
        fi = self._make_file_info("pkg/Service.java", "java")
        result = self._parser().parse_file(fi, src)
        sym = next((s for s in result.symbols if s.name == "processData"), None)
        assert sym is not None, "processData symbol not found"
        assert sym.decorators, (
            f"processData should have non-empty decorators; got {sym.decorators}"
        )

    def test_java_bare_deprecated_is_detected_as_deprecated(self):
        """Regression: bare @Deprecated blob must match _is_symbol_deprecated."""
        from repowise.core.analysis.dead_code.analyzer import _is_symbol_deprecated

        src = b"""
public class Service {
    @Deprecated
    public void processData() { }
}
"""
        fi = self._make_file_info("pkg/Service.java", "java")
        result = self._parser().parse_file(fi, src)
        sym = next((s for s in result.symbols if s.name == "processData"), None)
        assert sym is not None
        assert _is_symbol_deprecated(sym.name, sym.decorators), (
            f"processData should be deprecated; decorators={sym.decorators}"
        )

    def test_java_deprecated_with_args_is_detected(self):
        from repowise.core.analysis.dead_code.analyzer import _is_symbol_deprecated

        src = b"""
public class Service {
    @Deprecated(since = "3.2", forRemoval = true)
    public void processData() { }
}
"""
        fi = self._make_file_info("pkg/Service.java", "java")
        result = self._parser().parse_file(fi, src)
        sym = next((s for s in result.symbols if s.name == "processData"), None)
        assert sym is not None
        assert _is_symbol_deprecated(sym.name, sym.decorators), (
            f"processData should be deprecated; decorators={sym.decorators}"
        )

    def test_java_override_plus_deprecated_is_detected(self):
        """Regression: @Override\\n  @Deprecated\\n  public blob — only second annotation matters."""
        from repowise.core.analysis.dead_code.analyzer import _is_symbol_deprecated

        src = b"""
public class Service extends Base {
    @Override
    @Deprecated
    public void processData() { }
}
"""
        fi = self._make_file_info("pkg/Service.java", "java")
        result = self._parser().parse_file(fi, src)
        sym = next((s for s in result.symbols if s.name == "processData"), None)
        assert sym is not None
        assert _is_symbol_deprecated(sym.name, sym.decorators), (
            f"processData should be deprecated; decorators={sym.decorators}"
        )

    def test_java_override_only_is_not_deprecated(self):
        from repowise.core.analysis.dead_code.analyzer import _is_symbol_deprecated

        src = b"""
public class Service extends Base {
    @Override
    public void processData() { }
}
"""
        fi = self._make_file_info("pkg/Service.java", "java")
        result = self._parser().parse_file(fi, src)
        sym = next((s for s in result.symbols if s.name == "processData"), None)
        assert sym is not None
        assert not _is_symbol_deprecated(sym.name, sym.decorators), (
            f"processData should NOT be deprecated; decorators={sym.decorators}"
        )

    # ------------------------------------------------------------------
    # Rust — #[deprecated] is a preceding sibling (existing behaviour preserved)
    # ------------------------------------------------------------------

    def test_rust_deprecated_still_works(self):
        """Rust attribute extraction was correct before; regression guard."""
        from repowise.core.analysis.dead_code.analyzer import _is_symbol_deprecated

        src = b'#[deprecated(since = "1.0", note = "use bar instead")]\npub fn old_api() {}\n'
        fi = self._make_file_info("pkg/lib.rs", "rust")
        result = self._parser().parse_file(fi, src)
        sym = next((s for s in result.symbols if s.name == "old_api"), None)
        assert sym is not None, "old_api symbol not found"
        assert _is_symbol_deprecated(sym.name, sym.decorators), (
            f"old_api should be deprecated; decorators={sym.decorators}"
        )
