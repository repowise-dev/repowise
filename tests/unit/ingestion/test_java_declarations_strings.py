"""The declarations of a Java file are read from its code: the text of a string makes none."""

from __future__ import annotations

from repowise.core.ingestion.languages.receiver_types import scan_declarations

_LOOP = (
    "class Pipe {\n"
    "  T apply(T context) {\n"
    "    for (Step<T> step : steps) {\n"
    '      log.trace("Applying step: class={}", step.getClass());\n'
    "      context = step.apply(context);\n"
    "    }\n"
    "    return context;\n"
    "  }\n"
    "}\n"
)


def test_the_text_of_a_string_declares_no_name() -> None:
    declared = [
        (d.line, d.name, d.type_name) for d in scan_declarations(_LOOP, "java") if d.name == "step"
    ]
    assert declared == [(3, "step", "Step")]


def test_a_declaration_in_code_is_still_read_beside_a_string_that_looks_like_one() -> None:
    text = (
        "class A {\n  void m() {\n"
        '    String s = "Other other: x";\n'
        "    Other other = make();\n  }\n}\n"
    )
    assert [(d.line, d.name) for d in scan_declarations(text, "java") if d.name == "other"] == [
        (4, "other")
    ]
