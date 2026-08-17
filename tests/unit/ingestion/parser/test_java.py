"""Unit tests for the unified ASTParser.

Tests parse inline byte strings so no filesystem I/O is needed.
Covers Python, TypeScript, Go, Rust, Java, C++ — one test class per language.
"""

from __future__ import annotations

from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.parser._helpers import _make_file_info

JAVA_SOURCE = b"""package com.repowise.sample;

import java.util.ArrayList;
import java.util.List;

/**
 * Stateful calculator with history.
 */
public class Calculator {

    private final List<Object> history = new ArrayList<>();

    /**
     * Adds x and y.
     */
    public double add(double x, double y) {
        return x + y;
    }

    /** Private helper. */
    private void record(Object entry) {
        history.add(entry);
    }
}
"""


FIELD_RECEIVER_SOURCE = b"""package com.repowise.sample;

public class Clinic {

    private final OwnerRepository owners;
    private final Basket basket;

    public void report() {
        this.owners.findAll();
        super.audit.record();
        this.basket.items.size();
    }

    class Inner {
        void run() {
            Clinic.this.reset();
        }
    }

    void reset() {
    }
}
"""


class TestJavaParser:
    def test_finds_class(self, parser: ASTParser) -> None:
        fi = _make_file_info("java_pkg/Calculator.java", "java")
        result = parser.parse_file(fi, JAVA_SOURCE)
        classes = [s for s in result.symbols if s.kind == "class"]
        assert any(s.name == "Calculator" for s in classes)

    def test_finds_methods(self, parser: ASTParser) -> None:
        fi = _make_file_info("java_pkg/Calculator.java", "java")
        result = parser.parse_file(fi, JAVA_SOURCE)
        methods = [s for s in result.symbols if s.kind == "method"]
        method_names = [m.name for m in methods]
        assert "add" in method_names
        assert "record" in method_names

    def test_parses_imports(self, parser: ASTParser) -> None:
        fi = _make_file_info("java_pkg/Calculator.java", "java")
        result = parser.parse_file(fi, JAVA_SOURCE)
        assert len(result.imports) >= 2
        module_paths = [i.module_path for i in result.imports]
        assert any("ArrayList" in p for p in module_paths)

    def test_own_field_receiver_is_captured(self, parser: ASTParser) -> None:
        """``this.field.m()`` must carry ``field``, not arrive receiver-less.

        The bare-call pattern matches every invocation, so a shape no
        receiver-carrying pattern claims reads as an implicit receiver on the
        caller's own class and binds by bare name.
        """
        fi = _make_file_info("java_pkg/Calculator.java", "java")
        result = parser.parse_file(fi, FIELD_RECEIVER_SOURCE)
        by_target = {c.target_name: c for c in result.calls if c.receiver_name}
        assert by_target["findAll"].receiver_name == "owners"
        assert by_target["record"].receiver_name == "audit"

    def test_other_objects_field_is_not_a_receiver(self, parser: ASTParser) -> None:
        """Only a name in the caller's own scope may be captured.

        ``items`` in ``this.basket.items.size()`` is a field of ``Basket``, and
        the receiver strategies type a field against the *caller's* class — so
        capturing it would offer it to be bound to a same-named field of the
        caller. ``Outer.this.m()`` is a real implicit receiver and keeps none.
        """
        fi = _make_file_info("java_pkg/Calculator.java", "java")
        result = parser.parse_file(fi, FIELD_RECEIVER_SOURCE)
        for target in ("size", "reset"):
            assert all(
                c.receiver_name is None for c in result.calls if c.target_name == target
            ), target
