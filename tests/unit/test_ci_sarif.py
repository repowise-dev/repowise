"""``repowise.core.ci.sarif``: the shared SARIF 2.1.0 envelope."""

from __future__ import annotations

from repowise.core.ci import sarif


def test_run_indexes_known_rules_and_encodes_uris():
    rules = [sarif.rule("path", "DocDriftPath", "short", "full", "help")]
    results = [
        sarif.result("path", "error", "msg", "docs/Setup #2.md", 0, "k/v1", "abc"),
        sarif.result("other", "warning", "msg", "\\docs\\a.md", 3, "k/v1", "def", {"x": 1}),
    ]
    log = sarif.run("tool", "1.0", rules, results)

    run = log["runs"][0]
    assert log["version"] == "2.1.0"
    assert run["tool"]["driver"]["rules"][0]["help"] == {"text": "help"}
    first, second = run["results"]
    loc = first["locations"][0]["physicalLocation"]
    assert loc["artifactLocation"]["uri"] == "docs/Setup%20%232.md"
    assert loc["region"]["startLine"] == 1
    assert first["ruleIndex"] == 0
    assert "ruleIndex" not in second
    assert second["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "docs/a.md"
    assert second["properties"] == {"x": 1}
    assert "properties" not in first


def test_result_without_a_line_or_accepted_by_a_baseline():
    res = sarif.result("r", "error", "m", "a.py", None, "k/v1", "abc", suppressed=True)
    assert "region" not in res["locations"][0]["physicalLocation"]
    assert res["suppressions"] == [{"kind": "external"}]
