import pytest

from repowise.core.analysis.security.calls import _call_findings
from repowise.core.analysis.security.masking import mask_comments_and_strings, mask_dialect


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


def test_java_script_engine_eval_is_reported():
    source = """
import javax.script.ScriptEngine;
import javax.script.ScriptEngineManager;

class ScriptRunner {
    void run(String userInput) throws Exception {
        ScriptEngine engine =
            new ScriptEngineManager().getEngineByName("JavaScript");

        engine.eval(userInput);
    }
}
"""

    findings = _call_findings(
        "ScriptRunner.java",
        source,
        lambda: source,
    )

    assert any(finding["kind"] == "eval_call" for finding in findings)


@pytest.mark.parametrize(
    "source",
    [
        """
import javax.script.ScriptEngine;

class Runner {
    void execute(ScriptEngine engine, String input) {
        engine.eval(input);
    }
}
""",
        """
class Runner {
    void execute(javax.script.ScriptEngine runner, String input) {
        runner.eval(input);
    }
}
""",
    ],
)
def test_java_script_engine_parameter_eval_is_reported(source):
    findings = _call_findings(
        "Runner.java",
        source,
        lambda: source,
    )

    assert any(finding["kind"] == "eval_call" for finding in findings)


def test_java_unrelated_eval_remains_unreported():
    source = """
import javax.script.ScriptEngine;

class Runner {
    void run(ScriptEngine engine, Parser parser) {
        parser.eval("1 + 2");
    }
}
"""
    findings = _call_findings("Runner.java", source, lambda: source)
    assert not any(f["kind"] == "eval_call" for f in findings)


def test_java_commented_script_engine_eval_is_unreported():
    source = """
// ScriptEngine engine = null;
// engine.eval(userInput);

class Runner {}
"""
    findings = _call_findings(
        "Runner.java",
        source,
        lambda: mask_comments_and_strings(source, **mask_dialect("Runner.java")),
    )
    assert not any(f["kind"] == "eval_call" for f in findings)


def test_java_script_engine_eval_respects_method_scope():
    source = """
import javax.script.ScriptEngine;

class Runner {
    void execute(ScriptEngine engine) {
        engine.eval("1 + 2");
    }

    void calculate(Parser engine) {
        engine.eval("1 + 2");
    }
}
"""

    findings = _call_findings("Runner.java", source, lambda: source)

    eval_findings = [finding for finding in findings if finding["kind"] == "eval_call"]

    assert len(eval_findings) == 1
    assert eval_findings[0]["line"] == 6


def test_java_script_engine_field_eval_is_reported():
    source = """
import javax.script.ScriptEngine;

class Runner {
    private ScriptEngine engine;

    void execute(String input) throws Exception {
        engine.eval(input);
    }
}
"""

    findings = _call_findings("Runner.java", source, lambda: source)

    assert any(finding["kind"] == "eval_call" and finding["line"] == 8 for finding in findings)


def test_java_method_parameter_shadows_script_engine_field():
    source = """
import javax.script.ScriptEngine;

class Runner {
    private ScriptEngine engine;

    void calculate(Parser engine) {
        engine.eval("1 + 2");
    }
}
"""

    findings = _call_findings("Runner.java", source, lambda: source)

    assert not any(finding["kind"] == "eval_call" for finding in findings)


def test_java_this_script_engine_field_eval_is_reported():
    source = """
import javax.script.ScriptEngine;

class Runner {
    private ScriptEngine engine;

    void run(String input) throws Exception {
        this.engine.eval(input);
    }
}
"""
    findings = _call_findings("Runner.java", source, lambda: source)

    assert any(f["kind"] == "eval_call" and f["line"] == 8 for f in findings)


def test_java_user_defined_script_engine_is_not_reported():
    source = """
class ScriptEngine {
    int eval(String expression) {
        return 42;
    }
}

class Runner {
    void run() {
        ScriptEngine engine = new ScriptEngine();
        engine.eval("1 + 2");
    }
}
"""
    findings = _call_findings("Runner.java", source, lambda: source)

    assert not any(f["kind"] == "eval_call" for f in findings)


def test_java_this_field_with_shadowing_parameter():
    source = """
import javax.script.ScriptEngine;

class Runner {
    private ScriptEngine engine;

    void run(Parser engine) throws Exception {
        engine.eval("1 + 2");
        this.engine.eval("1 + 2");
    }
}
"""

    findings = _call_findings("Runner.java", source, lambda: source)
    eval_findings = [f for f in findings if f["kind"] == "eval_call"]

    assert len(eval_findings) == 1
    assert eval_findings[0]["line"] == 9


def test_java_script_engine_binding_does_not_escape_block():
    source = """
import javax.script.ScriptEngine;

class Runner {
    void run() throws Exception {
        {
            ScriptEngine engine = null;
            engine.eval("test");
        }

        Parser engine = new Parser();
        engine.eval("1 + 2");
    }
}
"""

    findings = _call_findings("Runner.java", source, lambda: source)
    eval_findings = [f for f in findings if f["kind"] == "eval_call"]

    assert len(eval_findings) == 1
    assert eval_findings[0]["line"] == 8
