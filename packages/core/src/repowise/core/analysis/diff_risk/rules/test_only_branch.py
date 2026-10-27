"""``test-only-branch``: a new test-environment check in production code."""

from __future__ import annotations

from .. import markers as m
from ..models import Rule
from ..text import FileView, Hit

RULE: Rule = "test-only-branch"

#: The most lines a quiet-logs check reads after a test check.
_MAX_GUARDED_LINES = 12

#: Lines a guarded block may hold besides its log calls.
_BLOCK_PUNCTUATION = frozenset({"", "{", ") {", ")", "):", "then"})


def hits(view: FileView, language: str | None, *, raw: bool) -> list[Hit]:
    """Test-environment checks in *view*; on raw lines for the base side."""
    checks = [c for c in m.TEST_ENV_CHECKS if language in c.languages]
    if not checks:
        return []
    found = _raw_checks(view, checks, language) if raw else _masked_checks(view, checks, language)
    return [Hit(index + 1, check.label, check.label) for index, check in found]


def message(hit: Hit) -> str:
    return (
        f"The change adds a `{hit.label}` check to production code, "
        "so tests and production can take different paths"
    )


def _raw_checks(
    view: FileView, checks: list[m.TestEnvCheck], language: str | None
) -> list[tuple[int, m.TestEnvCheck]]:
    found = ((i, _check(checks, line, line, language)) for i, line in enumerate(view.lines))
    return [(i, check) for i, check in found if check is not None]


def _masked_checks(
    view: FileView, checks: list[m.TestEnvCheck], language: str | None
) -> list[tuple[int, m.TestEnvCheck]]:
    out: list[tuple[int, m.TestEnvCheck]] = []
    for index, line in enumerate(view.text_view):
        check = _check(checks, line, view.code_view[index], language)
        if check is not None and not _exempt(view, index) and _local_code(view, index, check):
            out.append((index, check))
    return out


def _check(
    checks: list[m.TestEnvCheck], line: str, code: str, language: str | None
) -> m.TestEnvCheck | None:
    """The first check *line* makes: its token in code, its literal if any, and a
    branch keyword outside the languages where the comparison alone counts."""
    branch_free = language in m.BRANCH_FREE_LANGUAGES or m.BRANCH_KEYWORDS.search(code)
    if not branch_free:
        return None
    for check in checks:
        in_code = any(code[x.start()] == line[x.start()] for x in check.code.finditer(line))
        if in_code and (check.text is None or check.text.search(line)):
            return check
    return None


def _local_code(view: FileView, index: int, check: m.TestEnvCheck) -> bool:
    """The check's token is code on its own line too, not only in the whole-file mask."""
    text, code = view.local(index)
    return any(code[x.start()] == text[x.start()] for x in check.code.finditer(text))


def _exempt(view: FileView, index: int) -> bool:
    """An env-file pick, or a negated check that guards only log calls."""
    if m.ENV_FILE_LITERAL.search(view.text_view[index]):
        return True
    code = view.code_view[index]
    if not m.NEGATED_CHECK.search(code):
        return False
    return bool(m.LOG_CALL.search(code)) or _guards_only_logs(view.code_view, index)


def _guards_only_logs(code: list[str], index: int) -> bool:
    """Whether the block under line *index* holds only log calls and their arguments."""
    indent = _indent(code[index])
    end = min(index + 1 + _MAX_GUARDED_LINES, len(code))
    body = [line for line in code[index + 1 : end] if line.strip() not in _BLOCK_PUNCTUATION]
    if not body or not m.LOG_CALL.match(body[0].strip()):
        return False
    for line in body[1:]:
        if _closes(line, indent):
            return True
        if not _logs(line.strip()):
            return False
    return False


def _closes(line: str, indent: int) -> bool:
    """Whether *line* ends the block opened at *indent*."""
    stripped = line.strip()
    return stripped.startswith("}") or stripped == "end" or _indent(line) <= indent


def _logs(stripped: str) -> bool:
    """A log call, or a continuation of a log call's arguments."""
    continues = stripped.startswith((")", ",")) or stripped.endswith(",")
    return bool(m.LOG_CALL.match(stripped)) or continues


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())
