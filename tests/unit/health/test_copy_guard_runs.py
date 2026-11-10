"""A run of guarded copies counts once in the CCN the complexity markers judge.

``if (input.containsKey(K)) b.setRecord(input.get(K));`` copies one value
across when it is present. Ten of those in a row are one mapping step, not ten
decisions; the stored CCN is unchanged.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from repowise.core.analysis.health.complexity import walk_file
from repowise.core.analysis.health.complexity.dispatch import judged_ccn, judged_leaves_out

_EXT = {"apex": "cls", "java": "java", "csharp": "cs"}


def _fn(language: str, source: str, name: str):
    fcx = walk_file(f"/tmp/Sample.{_EXT[language]}", language, source.encode())
    if not fcx.functions:
        pytest.skip(f"{language} grammar unavailable")
    return {fc.name: fc for fc in fcx.functions}[name]


# Shared by Apex, Java and C#: the same statement shapes parse in all three.
_COPIES = """
class Sample {
  void newEntry(Map<String, Object> input, Builder b, Rule rule, Settings s) {
    if (input.containsKey(ARGUMENT_EXCEPTION)) {
      b.setExceptionDetails((Exception) input.get(ARGUMENT_EXCEPTION));
    }
    if (input.containsKey(ARGUMENT_RECORD_ID)) {
      b.setRecord((String) input.get(ARGUMENT_RECORD_ID));
    }
    if (input.containsKey(ARGUMENT_RECORD)) {
      b.setRecord((SObject) input.get(ARGUMENT_RECORD));
    }
    if (rule.Enabled__c != null) s.Enabled__c = rule.Enabled__c;
    if (isNotBlank(rule.Level__c)) s.Level__c = rule.Level__c;
  }

  void decides(Map<String, Object> input, Builder b, Rule rule, Settings s) {
    if (input.isEmpty()) {
      b.clear();
    }
    if (rule.Enabled__c != null) {
      s.Enabled__c = rule.Enabled__c;
      s.Touched__c = true;
    }
    if (input.containsKey(ARGUMENT_RECORD)) {
      b.setRecord(input.get(ARGUMENT_RECORD));
    } else {
      b.clear();
    }
    if (prev != null) prev.next = next;
    if (next != null) next.prev = prev;
    if (input.containsKey(ARGUMENT_TAGS)) {
      while (b.hasRoom()) { b.addTag(input.get(ARGUMENT_TAGS)); }
    }
  }
}
"""


@pytest.mark.parametrize("language", ["apex", "java", "csharp"])
def test_guarded_copy_run_counts_once(language):
    fn = _fn(language, _COPIES, "newEntry")
    assert fn.ccn == 6
    assert fn.copy_guards == 4
    assert judged_ccn(fn) == 2


@pytest.mark.parametrize("language", ["apex", "java", "csharp"])
def test_ifs_that_decide_are_not_copies(language):
    """A test the body does not read, two statements, an else, a write through
    the tested value (list relinking), a loop: real decisions."""
    fn = _fn(language, _COPIES, "decides")
    assert fn.copy_guards == 0
    assert judged_ccn(fn) == fn.ccn


def test_finding_text_names_the_copies():
    copies = SimpleNamespace(
        ccn=12, exit_guards=0, copy_guards=4, dispatch_share=0.0, dispatch_arm=0
    )
    assert judged_ccn(copies) == 8
    assert judged_leaves_out(copies) == "every repeated guarded copy"
