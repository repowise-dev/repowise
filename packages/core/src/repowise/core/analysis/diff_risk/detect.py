"""The diff-risk rules: facts about a change that can game a health gate.

- ``added-suppression``: a new lint, type-check, coverage or repowise
  suppression comment or attribute, or a biomarker newly disabled in
  ``.repowise/health-rules.json``.
- ``weakened-tests``: a new skip or focus marker in a test file, fewer test
  cases across the changed test files, fewer assertions in a test file, or a
  less strict coverage or quality threshold.
- ``test-only-branch``: a new test-environment check in production source.

:func:`detect_diff_risk` is pure: it reads the base and head text the caller
hands it and touches no filesystem or git. :func:`read_change_texts` is the
thin adapter that fetches that text from a :class:`RevisionSource`.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Any, Literal, cast, get_args

from ...code_origin import code_origin
from ...references import path_identity
from ...test_paths import is_test_path
from ..change_health.analyzer import language_of
from ..change_health.sources import RevisionPair, RevisionSource
from . import markers as m
from .text import FileView, Hit, added_line_indexes, numeric_values

Rule = Literal["added-suppression", "weakened-tests", "test-only-branch"]
Severity = Literal["off", "warn", "error"]

RULES: tuple[Rule, ...] = ("added-suppression", "weakened-tests", "test-only-branch")
DEFAULT_SEVERITY: Severity = "warn"
_SEVERITIES = frozenset(get_args(Severity))

#: Bumped when the identity inputs change.
IDENTITY_VERSION = 1
_ID_PREFIX = "drf_"

#: Larger files are bundles or data, not hand-written code.
MAX_FILE_BYTES = 1_000_000

HEALTH_RULES_PATH = ".repowise/health-rules.json"

#: The most lines a quiet-logs check reads after a test check.
_MAX_GUARDED_LINES = 12


@dataclass(frozen=True, slots=True)
class FileTexts:
    """Both sides of one changed file. ``None`` means the side has no file."""

    base_path: str | None
    head_path: str | None
    base: str | None
    head: str | None

    @property
    def path(self) -> str:
        return self.head_path or self.base_path or ""


@dataclass(frozen=True, slots=True)
class DiffRiskFinding:
    """One diff-risk finding. Every one is introduced by the change."""

    #: Line-free, stable across runs and unrelated edits.
    finding_id: str
    rule: Rule
    #: ``comment`` | ``attribute`` | ``config`` | ``skip-marker`` |
    #: ``removed-tests`` | ``assertion-drop`` | ``threshold`` | ``test-env-check``.
    kind: str
    path: str
    #: 1-based head line; ``None`` for a file-level finding.
    line: int | None
    severity: Severity
    message: str
    #: The marker or the value change that supports the finding.
    evidence: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class _Raw:
    rule: Rule
    kind: str
    path: str
    line: int | None
    label: str
    message: str
    evidence: str


TestClassifier = Callable[[str, "str | None"], bool]


def _default_is_test(path: str, language: str | None) -> bool:
    return is_test_path(path, language)


# -- relevance ----------------------------------------------------------------


def _is_prose(path: str) -> bool:
    return path.lower().endswith(m.PROSE_SUFFIXES)


def _threshold_file(path: str) -> m.ThresholdFile | None:
    name = PurePosixPath(path).name
    return next((t for t in m.THRESHOLD_FILES if t.matches(name)), None)


def is_relevant(path: str) -> bool:
    """Whether a rule reads *path*. A caller fetching bytes fetches only these."""
    if _in_fixture_or_generated_dir(path):
        return False
    return (
        language_of(path) is not None
        or _is_prose(path)
        or _threshold_file(path) is not None
        or path_identity(path) == HEALTH_RULES_PATH
    )


def _in_fixture_or_generated_dir(path: str) -> bool:
    p = PurePosixPath(path_identity(path))
    return bool(m.GOLDEN_NAME.search(p.name)) or any(
        part in m.FIXTURE_OR_GENERATED_DIRS for part in p.parts[:-1]
    )


def _held(path: str, text: str) -> bool:
    """Vendored or generated: a tool or a third party wrote the markers."""
    return code_origin(path, text[:4096]) in ("vendored", "generated")


# -- the pure entry point -----------------------------------------------------


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
    classify = is_test or _default_is_test
    level = {rule: _severity(severities, rule) for rule in RULES}
    files = [
        f
        for f in files
        if f.path and is_relevant(f.path) and not _held(f.path, f.head or f.base or "")
    ]
    raw: list[_Raw] = []
    tests: list[_TestFile] = []
    hits: list[_FileHits] = []
    for file in files:
        tests.extend(_detect_file(file, classify, level, raw, hits))
    raw.extend(_net(hits))
    if level["weakened-tests"] != "off":
        raw.extend(_removed_tests(tests, files, classify))
    return _finalize(raw, level)


def _severity(severities: Mapping[str, str] | None, rule: str) -> Severity:
    value = (severities or {}).get(rule, DEFAULT_SEVERITY)
    return cast(Severity, value) if value in _SEVERITIES else DEFAULT_SEVERITY


@dataclass(slots=True)
class _TestFile:
    path: str
    base: FileView | None
    head: FileView | None


#: ``(rule, kind, hit)``: one marker occurrence on one side.
_Tagged = tuple[Rule, str, Hit]


@dataclass(slots=True)
class _FileHits:
    """The line-rule markers of one changed file, on both sides."""

    path: str
    base: list[_Tagged]
    head: list[_Tagged]
    #: 0-based head lines the change added.
    added: set[int]
    head_lines: list[str]


def _detect_file(
    file: FileTexts,
    is_test: TestClassifier,
    level: dict[Rule, Severity],
    raw: list[_Raw],
    hits: list[_FileHits],
) -> list[_TestFile]:
    config = _config_findings(file, level)
    if config is not None:
        raw.extend(config)
        return []
    path = file.path
    prose = _is_prose(path)
    language = None if prose else language_of(path)
    base = None
    if file.base is not None:
        base = FileView(file.base, file.base_path or path, language, prose=prose)
    head = FileView(file.head, path, language, prose=prose) if file.head is not None else None
    test = not prose and is_test(path, language)
    lens = _Lens(language, prose, test, _is_production(file, test or prose), level)
    added = set(added_line_indexes(base.lines if base else [], head.lines)) if head else set()
    # Base counts on raw lines, head on both masks: see FileView.raw_hits.
    base_hits, head_hits = lens.tagged(base, raw=True), lens.tagged(head, raw=False)
    hits.append(_FileHits(path, base_hits, head_hits, added, head.lines if head else []))
    return [_TestFile(path, base, head)] if test else []


def _config_findings(file: FileTexts, level: dict[Rule, Severity]) -> list[_Raw] | None:
    """Findings of a health-rules or threshold config file; ``None`` for any other file."""
    path = file.path
    if path_identity(path) == HEALTH_RULES_PATH:
        if level["added-suppression"] == "off":
            return []
        return _disabled_biomarkers(path, file.base, file.head)
    threshold = _threshold_file(path)
    if threshold is None:
        return None
    if level["weakened-tests"] == "off" or file.base is None or file.head is None:
        return []
    return _weakened_thresholds(path, threshold, file.base, file.head)


def _is_production(file: FileTexts, test_or_prose: bool) -> bool:
    if test_or_prose or _is_tool_config(file.path):
        return False
    return code_origin(file.path, (file.head or file.base or "")[:4096]) == "production"


@dataclass(frozen=True, slots=True)
class _Lens:
    """What one file is, and so which line rules read it."""

    language: str | None
    prose: bool
    test: bool
    production: bool
    level: dict[Rule, Severity]

    def tagged(self, view: FileView | None, *, raw: bool) -> list[_Tagged]:
        if view is None:
            return []
        out: list[_Tagged] = []
        if self.level["added-suppression"] != "off":
            out += _suppression_hits(view, self, raw=raw)
        if self.test and self.level["weakened-tests"] != "off":
            skips = _skip_hits(view, self.language, raw=raw)
            out += [("weakened-tests", "skip-marker", h) for h in skips]
        if self.production and self.level["test-only-branch"] != "off":
            checks = _test_env_hits(view, self.language, raw=raw)
            out += [("test-only-branch", "test-env-check", h) for h in checks]
        return out


def _net(files: list[_FileHits]) -> list[_Raw]:
    """Markers the whole change added, net of the ones it removed.

    A marker counts by its key across every changed file, so a suppression or
    a skip that moved to another file with its code (a module split) is not
    added. When head holds *n* more of a key than base, the first *n* head
    occurrences on added lines are reported, in path and line order.
    """
    balance: Counter[tuple[str, str, str]] = Counter()
    for f in files:
        for rule, kind, hit in f.head:
            balance[(rule, kind, hit.key)] += 1
        for rule, kind, hit in f.base:
            balance[(rule, kind, hit.key)] -= 1
    out: list[_Raw] = []
    for f in sorted(files, key=lambda f: f.path):
        for rule, kind, hit in sorted(f.head, key=lambda t: t[2].line):
            key = (rule, kind, hit.key)
            if balance[key] > 0 and hit.line - 1 in f.added:
                balance[key] -= 1
                out.append(_line_finding(rule, kind, f.path, hit, f.head_lines[hit.line - 1]))
    return out


def _line_finding(rule: Rule, kind: str, path: str, hit: Hit, line: str) -> _Raw:
    if rule == "added-suppression":
        message = (
            f"The change adds a `{hit.label}` suppression, "
            "which hides findings instead of fixing them"
        )
    elif rule == "weakened-tests":
        message = (
            f"The change adds a skip or focus marker, so tests stop running: {line.strip()[:80]}"
        )
    else:
        message = (
            f"The change adds a `{hit.label}` check to production code, "
            "so tests and production can take different paths"
        )
    return _Raw(rule, kind, path, hit.line, hit.label, message, hit.key)


# -- added-suppression --------------------------------------------------------


def _suppression_hits(view: FileView, lens: _Lens, *, raw: bool) -> list[_Tagged]:
    language, prose = lens.language, lens.prose
    comment = [k for k in m.COMMENT_SUPPRESSIONS if k.applies_to(language, is_prose=prose)]
    code = [k for k in m.CODE_SUPPRESSIONS if k.applies_to(language, is_prose=prose)]
    comments = view.raw_hits(comment, comment=True) if raw else view.comment_hits(comment)
    codes = view.raw_hits(code, comment=False) if raw else view.code_hits(code)
    tagged: list[_Tagged] = [("added-suppression", "comment", h) for h in comments]
    tagged += [("added-suppression", "attribute", h) for h in codes]
    if lens.test:
        tagged = [t for t in tagged if t[2].label not in m.TEST_ALLOWED_SUPPRESSIONS]
    return tagged


def _disabled_biomarkers(path: str, base: str | None, head: str | None) -> list[_Raw]:
    """Biomarkers ``.repowise/health-rules.json`` newly turns off."""
    before = _disabled_pairs(base)
    after = _disabled_pairs(head)
    if before is None or after is None:
        return []
    return [
        _Raw(
            "added-suppression",
            "config",
            path,
            None,
            f"{scope}:{name}",
            f"The change disables the `{name}` health check for `{scope}`",
            f"{scope}: {name}",
        )
        for scope, name in sorted(after - before)
        # Narrowing a global disable to a path is stricter, not looser.
        if ("*", name) not in before
    ]


def _disabled_pairs(text: str | None) -> set[tuple[str, str]] | None:
    """``{(path glob or "*", biomarker)}``; ``None`` when the file is unreadable."""
    if text is None:
        return set()
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    pairs = {("*", str(b)) for b in data.get("disabled_biomarkers") or [] if isinstance(b, str)}
    for rule in data.get("rules") or []:
        if isinstance(rule, dict) and isinstance(rule.get("path"), str):
            names = rule.get("disabled_biomarkers") or []
            pairs |= {(rule["path"], b) for b in names if isinstance(b, str)}
    return pairs


# -- weakened-tests: thresholds -----------------------------------------------


def _weakened_thresholds(path: str, spec: m.ThresholdFile, base: str, head: str) -> list[_Raw]:
    """Threshold values the change made less strict.

    Occurrences of a key pair in file order, and only when both sides hold
    the same number of them: an added, removed or reordered entry cannot be
    paired by position, so the rule abstains.
    """
    out: list[_Raw] = []
    for keys, lower_is_weaker in ((spec.minimums, True), (spec.maximums, False)):
        for key in keys:
            out += [
                _threshold(path, key, before, after, line)
                for before, after, line in _paired(base, head, key)
                if (after < before if lower_is_weaker else after > before)
            ]
    return out


def _paired(base: str, head: str, key: str) -> list[tuple[float, float, int]]:
    """``(before, after, head line)`` per occurrence of *key*; none when counts differ."""
    olds, news = numeric_values(base, key), numeric_values(head, key)
    if len(olds) != len(news):
        return []
    return [(b, a, line) for (b, _), (a, line) in zip(olds, news, strict=True)]


def _threshold(path: str, key: str, before: float, after: float, line: int) -> _Raw:
    evidence = f"{key}: {before:g} -> {after:g}"
    message = f"The change makes a threshold less strict ({evidence})"
    return _Raw("weakened-tests", "threshold", path, line, key, message, evidence)


# -- weakened-tests: removed tests and assertions -----------------------------


def _removed_tests(
    tests: list[_TestFile], files: list[FileTexts], is_test: TestClassifier
) -> list[_Raw]:
    """``removed-tests`` and ``assertion-drop``, with anti-noise rules.

    Both report only when the total over every changed test file dropped, so
    tests that moved to another changed file do not report. A drop does not
    report when the lost test lines name code the change removed: the tests
    went with that code. An assertion drop reports only when assertions are at
    least half of the code lines the file lost and the file added none;
    otherwise the test was rewritten. Either drop must also show in the lines the change removed,
    each read on its own, so a whole-file mask that drifted on one side (a
    quote it misread) never reads as lost tests.
    """
    if not tests:
        return []
    removed = _RemovedNames.collect(files, is_test)
    rows = []
    for test in tests:
        base_cases = _count(test.base, m.TEST_CASES)
        head_cases = _count(test.head, m.TEST_CASES)
        base_asserts = _count(test.base, m.ASSERTIONS)
        head_asserts = _count(test.head, m.ASSERTIONS)
        rows.append((test, base_cases, head_cases, base_asserts, head_asserts))
    cases_dropped = sum(r[2] for r in rows) < sum(r[1] for r in rows)
    asserts_dropped = sum(r[4] for r in rows) < sum(r[3] for r in rows)
    out: list[_Raw] = []
    for test, base_cases, head_cases, base_asserts, head_asserts in rows:
        if test.base is None or removed.explain(test.base, test.head) or _parametrized(test):
            continue
        if cases_dropped and head_cases < base_cases and _lost(test, m.TEST_CASES) > 0:
            evidence = f"test cases: {base_cases} -> {head_cases}"
            out.append(
                _Raw(
                    "weakened-tests", "removed-tests", test.path, None, "removed-tests",
                    f"The change removes test cases ({evidence})", evidence,
                )
            )  # fmt: skip
        elif (
            asserts_dropped
            and head_cases >= base_cases
            and head_asserts < base_asserts
            and _assertions_led(test)
        ):
            evidence = f"assertions: {base_asserts} -> {head_asserts}"
            out.append(
                _Raw(
                    "weakened-tests", "assertion-drop", test.path, None, "assertion-drop",
                    f"The change removes assertions ({evidence})", evidence,
                )
            )  # fmt: skip
    return out


def _count(view: FileView | None, markers: tuple[m.Marker, ...]) -> int:
    if view is None:
        return 0
    return view.count(k for k in markers if k.applies_to(view.language, is_prose=False))


def _parametrized(test: _TestFile) -> bool:
    """Whether the change added a parametrize marker: cases folded into one."""
    if test.head is None:
        return False
    base_lines = test.base.lines if test.base else []
    added = added_line_indexes(base_lines, test.head.lines)
    return any(m.PARAMETRIZE.search(test.head.text_view[i]) for i in added)


def _lost(test: _TestFile, markers: tuple[m.Marker, ...]) -> int:
    """Markers on the test's removed lines minus those on its added lines."""
    return _on_removed_lines(test, markers) - _on_added_lines(test, markers)


def _on_removed_lines(test: _TestFile, markers: tuple[m.Marker, ...]) -> int:
    if test.base is None:
        return 0
    rows = [k for k in markers if k.applies_to(test.base.language, is_prose=False)]
    return test.base.local_count(rows, _removed_line_indexes(test.base, test.head))


def _on_added_lines(test: _TestFile, markers: tuple[m.Marker, ...]) -> int:
    if test.head is None:
        return 0
    rows = [k for k in markers if k.applies_to(test.head.language, is_prose=False)]
    base_lines = test.base.lines if test.base else []
    return test.head.local_count(rows, added_line_indexes(base_lines, test.head.lines))


def _removed_line_indexes(base: FileView, head: FileView | None) -> list[int]:
    """0-based base lines the change removed (base lines head does not hold)."""
    return added_line_indexes(head.lines if head else [], base.lines)


def _assertions_led(test: _TestFile) -> bool:
    """Whether the file only lost assertions: it added none, and assertions
    are at least half of the code lines it removed.

    An added assertion means the checks were rewritten (two expectations
    folded into one), which a count cannot tell from a weaker test.
    """
    base, head = test.base, test.head
    if base is None or _on_added_lines(test, m.ASSERTIONS):
        return False
    markers = [k for k in m.ASSERTIONS if k.applies_to(base.language, is_prose=False)]
    code_lines = [i for i in _removed_line_indexes(base, head) if base.code_view[i].strip()]
    if not code_lines:
        return False
    asserting = sum(1 for i in code_lines if base.local_count(markers, [i]))
    return asserting * 2 >= len(code_lines)


class _RemovedNames:
    """Names the change took out of the project's non-test source.

    Identifiers a changed source file held in base and no longer holds in
    head; identifiers of a deleted source file that no changed file still
    holds; and the stems and gone directories of deleted files.
    """

    def __init__(self, identifiers: set[str], segments: set[str]) -> None:
        self.identifiers = identifiers
        self.segments = segments

    @classmethod
    def collect(cls, files: list[FileTexts], is_test: TestClassifier) -> _RemovedNames:
        identifiers: set[str] = set()
        deleted: set[str] = set()
        head_union: set[str] = set()
        segments: set[str] = set()
        live_dirs = {
            str(parent)
            for f in files
            if f.head_path and f.head is not None
            for parent in PurePosixPath(f.head_path).parents
        }
        for f in files:
            if f.head is None and f.base_path:
                segments |= _gone_segments(f.base_path, live_dirs)
            language = language_of(f.path)
            if language is None or _is_prose(f.path) or is_test(f.path, language):
                continue
            head_ids = FileView(f.head, f.path, language).identifiers() if f.head else set()
            head_union |= head_ids
            if f.base is None:
                continue
            base_view = FileView(f.base, f.base_path or f.path, language)
            base_ids = {n for n in base_view.identifiers() if _candidate(n)}
            if f.head is None:
                deleted |= base_ids
            else:
                identifiers |= base_ids - head_ids
        # A name of a deleted file that a changed file still holds moved, it
        # did not leave. Names the deleted file only imported count too: the
        # ceiling is abstaining on a few more removals, never a false alarm.
        identifiers |= deleted - head_union
        return cls(identifiers, segments)

    def explain(self, base: FileView, head: FileView | None) -> bool:
        """Whether the lines a test file lost name removed code its head no longer names."""
        removed = _removed_line_indexes(base, head)
        if not removed:
            return False
        head_ids = head.identifiers() if head else set()
        if any(n in self.identifiers and n not in head_ids for n in base.identifiers(removed)):
            return True
        head_text = head.text if head else ""
        tokens = set().union(*(_path_tokens(base.lines[i]) for i in removed))
        return any(s in tokens and s not in head_text for s in self.segments)


def _candidate(name: str) -> bool:
    return len(name) >= m.MIN_REMOVED_NAME_LEN and name not in m.NOT_REMOVED_NAMES


def _gone_segments(path: str, live_dirs: set[str]) -> set[str]:
    """The stem of a deleted file and each directory no changed head file lives in.

    Without a head tree listing a directory that still holds unchanged files
    reads as gone; that only makes the rule abstain more often.
    """
    p = PurePosixPath(path)
    out = {p.stem} if len(p.stem) >= m.MIN_REMOVED_NAME_LEN else set()
    for parent in p.parents:
        if str(parent) not in live_dirs and len(parent.name) >= m.MIN_REMOVED_NAME_LEN:
            out.add(parent.name)
    return out


def _path_tokens(line: str) -> set[str]:
    return {t for t in _split_path(line) if t}


def _split_path(line: str) -> list[str]:
    for sep in "/.'\"`:\\":
        line = line.replace(sep, " ")
    return line.split()


# -- test-only-branch ---------------------------------------------------------


def _is_tool_config(path: str) -> bool:
    """A tool config (``vite.config.ts``, ``.eslintrc.js``) or a settings module.

    A test-environment check is normal there.
    """
    p = PurePosixPath(path)
    name = p.name
    dot_rc = name.startswith(".") and name[1:].split(".", 1)[0].endswith("rc")
    settings = p.stem in {"settings", "config", "conftest"} or any(
        part in {"settings", "config", "configs"} for part in p.parts[:-1]
    )
    return ".config." in name or ".conf." in name or dot_rc or settings


def _test_env_hits(view: FileView, language: str | None, *, raw: bool) -> list[Hit]:
    checks = [c for c in m.TEST_ENV_CHECKS if language in c.languages]
    found: list[Hit] = []
    if not checks:
        return found
    for index, line in enumerate(view.lines if raw else view.text_view):
        if raw:
            check = _env_check(checks, line, line, language)
        else:
            check = _env_check(checks, line, view.code_view[index], language)
            if check is not None and (_exempt(view, index) or not _local_code(view, index, check)):
                check = None
        if check is not None:
            found.append(Hit(index + 1, check.label, check.label))
    return found


def _local_code(view: FileView, index: int, check: m.TestEnvCheck) -> bool:
    """The check's token is code on its own line too, not only in the whole-file mask."""
    text, code = view.local(index)
    return any(code[x.start()] == text[x.start()] for x in check.code.finditer(text))


def _skip_hits(view: FileView, language: str | None, *, raw: bool) -> list[Hit]:
    """Skip and focus markers, minus the guarded ones.

    A skip on a branch line or right under one (``if not HAS_GO:
    pytest.skip(...)``, ``skip "x" unless windows?``, an ``except`` handler)
    guards an environment
    rather than turning a test off, so it does not count.
    """
    markers = [k for k in m.SKIP_MARKERS if k.applies_to(language, is_prose=False)]
    if raw:
        return view.raw_hits(markers, comment=False)
    return [h for h in view.code_hits(markers) if not _guarded(view, h.line - 1)]


def _guarded(view: FileView, index: int) -> bool:
    """Whether a guard sits on the skip's line, the code line right above it,
    or a line that encloses it (less indented) below the enclosing function
    or test header."""
    code = view.code_view
    if m.SKIP_GUARDS.search(code[index]):
        return True
    indent = _indent(code[index])
    previous = True
    for i in range(index - 1, -1, -1):
        line = code[i]
        if not line.strip():
            continue
        if previous and m.SKIP_GUARDS.search(line):
            return True
        previous = False
        if _indent(line) < indent:
            if m.SKIP_GUARDS.search(line):
                return True
            if m.BLOCK_HEADER.search(line):
                return False
            indent = _indent(line)
            if indent == 0:
                return False
    return False


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _env_check(
    checks: list[m.TestEnvCheck], line: str, code: str, language: str | None
) -> m.TestEnvCheck | None:
    for check in checks:
        in_code = any(code[x.start()] == line[x.start()] for x in check.code.finditer(line))
        if not in_code or (check.text is not None and not check.text.search(line)):
            continue
        if language not in m.BRANCH_FREE_LANGUAGES and not m.BRANCH_KEYWORDS.search(code):
            continue
        return check
    return None


def _exempt(view: FileView, index: int) -> bool:
    """An env-file pick, or a negated check that guards only log calls."""
    line = view.text_view[index]
    if m.ENV_FILE_LITERAL.search(line):
        return True
    code = view.code_view[index]
    if not m.NEGATED_CHECK.search(code):
        return False
    if m.LOG_CALL.search(code):
        return True
    indent = len(code) - len(code.lstrip())
    end = min(index + 1 + _MAX_GUARDED_LINES, len(view.code_view))
    body = (view.code_view[i] for i in range(index + 1, end))
    body = (b for b in body if b.strip() not in {"", "{", ") {", ")", "):", "then"})
    first = next(body, None)
    if first is None or not m.LOG_CALL.match(first.strip()):
        return False
    for rest in body:
        stripped = rest.strip()
        dedented = len(rest) - len(rest.lstrip()) <= indent
        if stripped.startswith("}") or stripped == "end" or dedented:
            return True
        continues = stripped.startswith((")", ",")) or stripped.endswith(",")
        if not (m.LOG_CALL.match(stripped) or continues):
            return False
    return False


# -- finalize -----------------------------------------------------------------


def _finalize(raw: list[_Raw], level: dict[Rule, Severity]) -> list[DiffRiskFinding]:
    raw.sort(key=lambda r: (r.path, r.line or 0, r.rule, r.kind, r.evidence))
    ordinals: defaultdict[tuple[str, str, str, str], int] = defaultdict(int)
    out: list[DiffRiskFinding] = []
    for r in raw:
        identity = (r.rule, r.kind, path_identity(r.path), r.label)
        ordinal = ordinals[identity]
        ordinals[identity] += 1
        out.append(
            DiffRiskFinding(
                finding_id=_finding_id(*identity, ordinal),
                rule=r.rule,
                kind=r.kind,
                path=r.path,
                line=r.line,
                severity=level[r.rule],
                message=r.message,
                evidence=r.evidence,
            )
        )
    return out


def _finding_id(rule: str, kind: str, path: str, label: str, ordinal: int) -> str:
    """No line number: an edit above the finding does not move its id."""
    payload = "\x00".join([str(IDENTITY_VERSION), rule, kind, path, label, str(ordinal)])
    return _ID_PREFIX + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# -- adapter ------------------------------------------------------------------


def read_change_texts(source: RevisionSource, pair: RevisionPair) -> list[FileTexts]:
    """Both sides of every relevant changed file, in two bulk reads.

    A rename reads its base text at the old path. A side that should exist
    and cannot be read drops the file: a failed read must never pass for a
    new file, where every marker would read as added.
    """
    changes = [c for c in pair.changes if is_relevant(c.path)]
    head_paths = [c.head_path for c in changes if c.head_path]
    base_paths = [c.base_path for c in changes if c.base_path]
    head = (
        source.read_working_tree(head_paths)
        if pair.working_tree
        else source.read(pair.head_sha, head_paths)
    )
    base = source.read(pair.base_sha, base_paths)
    texts = (_texts(c.base_path, c.head_path, base, head) for c in changes)
    return [t for t in texts if t is not None]


def _texts(
    base_path: str | None, head_path: str | None, base: dict[str, bytes], head: dict[str, bytes]
) -> FileTexts | None:
    """Both sides of one file, or ``None`` when a side that exists did not read as text."""
    sides: list[str | None] = []
    for path, blobs in ((base_path, base), (head_path, head)):
        if path is None:
            sides.append(None)
            continue
        text = _decode(blobs.get(path))
        if text is None:
            return None
        sides.append(text)
    return FileTexts(base_path, head_path, sides[0], sides[1])


def _decode(raw: bytes | None) -> str | None:
    """Text of *raw*; ``None`` for a missing, binary or oversized blob."""
    if raw is None or len(raw) > MAX_FILE_BYTES or b"\x00" in raw:
        return None
    return raw.decode("utf-8", errors="replace")


def diff_risk_for_change(
    source: RevisionSource,
    pair: RevisionPair,
    *,
    severities: Mapping[str, str] | None = None,
) -> list[DiffRiskFinding]:
    """:func:`detect_diff_risk` over a resolved revision pair."""
    return detect_diff_risk(read_change_texts(source, pair), severities=severities)
