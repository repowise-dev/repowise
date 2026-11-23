import pytest

from repowise.core.analysis.security.calls import _call_findings


@pytest.mark.parametrize(
    ("filename", "source"),
    [
        (
            "Calc.java",
            "class Calc { int eval(String e) { return parser.eval(e); } }",
        ),
        (
            "JavascriptEvaluator.kt",
            "fun eval(js: String) = web.evaluateJavascript(js, null)",
        ),
    ],
)
def test_java_kotlin_eval_false_positives(filename, source):
    findings = _call_findings(
        filename,
        source,
        lambda: source,
    )

    assert not any(finding["kind"] == "eval_call" for finding in findings)


def test_kotlin_script_eval_is_not_reported():
    findings = _call_findings(
        "script.kts",
        "fun eval(x: String) = x",
        lambda: "fun eval(x: String) = x",
    )
    assert not any(f["kind"] == "eval_call" for f in findings)


def test_javascript_eval_still_reported():
    source = "const result = eval(userInput);"
    findings = _call_findings(
        "script.js",
        source,
        lambda: source,
    )
    assert any(f["kind"] == "eval_call" for f in findings)
