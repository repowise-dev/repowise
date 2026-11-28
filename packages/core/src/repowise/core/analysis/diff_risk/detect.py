"""The diff-risk rules: facts about a change that can game a health gate.

- ``added-suppression`` (:mod:`.rules.suppression`): a new lint, type-check,
  coverage or repowise suppression comment or attribute, or a biomarker newly
  disabled in ``.repowise/health-rules.json``.
- ``weakened-tests`` (:mod:`.rules.weakened_tests`, :mod:`.rules.removed_tests`):
  a new skip or focus marker in a test file, fewer test cases across the
  changed test files, fewer assertions in a test file, or a less strict
  coverage or quality threshold.
- ``test-only-branch`` (:mod:`.rules.test_only_branch`): a new
  test-environment check in production source.

:func:`detect_diff_risk` is pure: it reads the base and head text the caller
hands it and touches no filesystem or git. This module only orchestrates:
it reads each file once, nets marker counts across the change, and stamps
severities and ids.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import cast, get_args

from ...references import path_identity
from ...test_paths import is_test_path
from ..change_health.analyzer import language_of
from .models import (
    RULES,
    DiffRiskFinding,
    FileTexts,
    Lens,
    RawFinding,
    Rule,
    Severity,
    Tagged,
    TestClassifier,
    TestFile,
)
from .paths import in_scope, is_health_rules, is_production, is_prose, threshold_file
from .rules import removed_tests, suppression, test_only_branch, weakened_tests
from .text import FileView, added_line_indexes

DEFAULT_SEVERITY: Severity = "warn"
_SEVERITIES = frozenset(get_args(Severity))

#: Bumped when the identity inputs change.
IDENTITY_VERSION = 1
_ID_PREFIX = "drf_"

Levels = dict[Rule, Severity]


def detect_diff_risk(
    files: Iterable[FileTexts],
    *,
    severities: Mapping[str, str] | None = None,
    is_test: TestClassifier | None = None,
) -> list[DiffRiskFinding]:
    """Every diff-risk finding in *files*, sorted by path and line.

    *severities* maps a rule id to ``off``, ``warn`` or ``error``; a missing
    rule is ``warn``. *is_test* classifies ``(path, language)`` as a test
    file; the default is repowise's shared test-path classifier.
    """
    classify = is_test or is_test_path
    level: Levels = {rule: _severity(severities, rule) for rule in RULES}
    files = [f for f in files if in_scope(f)]
    raw: list[RawFinding] = []
    tests: list[TestFile] = []
    hits: list[_FileHits] = []
    for file in files:
        config = _config_findings(file, level)
        if config is not None:
            raw.extend(config)
            continue
        read = _read(file, classify, level)
        hits.append(read.hits)
        tests.extend([read.test] if read.test else [])
    raw.extend(_net(hits))
    if level["weakened-tests"] != "off":
        raw.extend(removed_tests.removed_tests(tests, files, classify))
    return _finalize(raw, level)


def _severity(severities: Mapping[str, str] | None, rule: str) -> Severity:
    value = (severities or {}).get(rule, DEFAULT_SEVERITY)
    return cast(Severity, value) if value in _SEVERITIES else DEFAULT_SEVERITY


def _config_findings(file: FileTexts, level: Levels) -> list[RawFinding] | None:
    """Findings of a health-rules or threshold config file; ``None`` for any other file."""
    if is_health_rules(file.path):
        if level["added-suppression"] == "off":
            return []
        return suppression.disabled_biomarkers(file.path, file.base, file.head)
    spec = threshold_file(file.path)
    if spec is None:
        return None
    if level["weakened-tests"] == "off" or file.base is None or file.head is None:
        return []
    return weakened_tests.weakened_thresholds(file.path, spec, file.base, file.head)


@dataclass(slots=True)
class _FileHits:
    """The line-rule markers of one changed file, on both sides."""

    path: str
    base: list[Tagged]
    head: list[Tagged]
    #: 0-based head lines the change added.
    added: set[int]
    head_lines: list[str]


@dataclass(slots=True)
class _Read:
    hits: _FileHits
    #: Set for a test file, which the counting rules read too.
    test: TestFile | None


def _read(file: FileTexts, is_test: TestClassifier, level: Levels) -> _Read:
    """One file's line-rule markers, base on raw lines and head on both masks.

    See :meth:`.text.FileView.raw_hits` for why the sides differ.
    """
    path = file.path
    prose = is_prose(path)
    language = None if prose else language_of(path)
    base = _view(file.base, file.base_path or path, language, prose)
    head = _view(file.head, path, language, prose)
    test = not prose and is_test(path, language)
    lens = Lens(language, prose, test, not (test or prose) and is_production(file))
    base_lines = base.lines if base else []
    head_lines = head.lines if head else []
    hits = _FileHits(
        path,
        _tagged(base, lens, level, raw=True),
        _tagged(head, lens, level, raw=False),
        set(added_line_indexes(base_lines, head_lines)),
        head_lines,
    )
    return _Read(hits, TestFile(path, base, head) if test else None)


def _view(text: str | None, path: str, language: str | None, prose: bool) -> FileView | None:
    return None if text is None else FileView(text, path, language, prose=prose)


def _tagged(view: FileView | None, lens: Lens, level: Levels, *, raw: bool) -> list[Tagged]:
    """Every line-rule marker of one side, tagged with its rule and kind."""
    if view is None:
        return []
    out: list[Tagged] = []
    if level["added-suppression"] != "off":
        out += suppression.hits(view, lens, raw=raw)
    if lens.test and level["weakened-tests"] != "off":
        skips = weakened_tests.skip_hits(view, lens.language, raw=raw)
        out += [("weakened-tests", "skip-marker", h) for h in skips]
    if lens.production and level["test-only-branch"] != "off":
        checks = test_only_branch.hits(view, lens.language, raw=raw)
        out += [("test-only-branch", "test-env-check", h) for h in checks]
    return out


def _net(files: list[_FileHits]) -> list[RawFinding]:
    """Markers the whole change added, net of the ones it removed.

    A marker counts by its key across every changed file, so a suppression or
    a skip that moved to another file with its code (a module split) is not
    added. When head holds *n* more of a key than base, the first *n* head
    occurrences on added lines are reported, in path and line order.
    """
    balance: Counter[tuple[str, str, str]] = Counter()
    for f in files:
        balance.update((rule, kind, hit.key) for rule, kind, hit in f.head)
        balance.subtract((rule, kind, hit.key) for rule, kind, hit in f.base)
    out: list[RawFinding] = []
    for f in sorted(files, key=lambda f: f.path):
        for tagged in sorted(f.head, key=lambda t: t[2].line):
            key = (tagged[0], tagged[1], tagged[2].key)
            if balance[key] > 0 and tagged[2].line - 1 in f.added:
                balance[key] -= 1
                out.append(_line_finding(tagged, f))
    return out


def _line_finding(tagged: Tagged, f: _FileHits) -> RawFinding:
    rule, kind, hit = tagged
    if rule == "added-suppression":
        message = suppression.message(hit)
    elif rule == "weakened-tests":
        message = weakened_tests.skip_message(f.head_lines[hit.line - 1])
    else:
        message = test_only_branch.message(hit)
    return RawFinding(rule, kind, f.path, hit.line, hit.label, message, hit.key)


def _finalize(raw: list[RawFinding], level: Levels) -> list[DiffRiskFinding]:
    raw.sort(key=lambda r: (r.path, r.line or 0, r.rule, r.kind, r.evidence))
    ordinals: defaultdict[tuple[str, ...], int] = defaultdict(int)
    out: list[DiffRiskFinding] = []
    for r in raw:
        identity = (r.rule, r.kind, path_identity(r.path), r.label)
        out.append(
            DiffRiskFinding(
                finding_id=_finding_id(identity, ordinals[identity]),
                rule=r.rule,
                kind=r.kind,
                path=r.path,
                line=r.line,
                severity=level[r.rule],
                message=r.message,
                evidence=r.evidence,
            )
        )
        ordinals[identity] += 1
    return out


def _finding_id(identity: tuple[str, ...], ordinal: int) -> str:
    """No line number: an edit above the finding does not move its id."""
    payload = "\x00".join([str(IDENTITY_VERSION), *identity, str(ordinal)])
    return _ID_PREFIX + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
