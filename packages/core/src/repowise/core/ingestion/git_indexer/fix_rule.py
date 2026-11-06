"""The fix rule that reads a conventional-commit type before the subject's words.

``is_fix_commit`` is the defect benchmark's keyword rule and stays byte-identical
for parity. On a repository that writes conventional commits it miscounts in both
directions: "refactor(...): split the Fix-first builder" matches the word "fix", and
"fix(deps): patch advisories" is dropped by the word "deps" in its scope.

This rule decides by the declared type first. A non-fix type (the types the
evolution timeline maps to a category) is never a fix, and a fix type always is:
the diff-shape filter downstream drops a fix that touches no production code. A
subject without a known type is a fix when its type word or the text after the
prefix passes the keyword rule, so a scope never excludes it. It counts
``prior_defect`` and its fix events and sets the change-risk ``is_fix`` flag;
every other fix count keeps ``is_fix_commit``.
"""

from __future__ import annotations

from ._constants import _CONVENTIONAL_MAP, _CONVENTIONAL_PREFIX, is_fix_commit

_FIX_TYPES = frozenset({"fix", "hotfix", "bugfix"})


def is_typed_fix_commit(subject: str) -> bool:
    """Whether *subject* is a fix, judged by its conventional type when it declares one."""
    match = _CONVENTIONAL_PREFIX.match(subject or "")
    if match is None:
        return is_fix_commit(subject)
    kind = match.group("type").lower()
    if kind in _FIX_TYPES:
        return True
    if kind in _CONVENTIONAL_MAP:
        return False
    return is_fix_commit(kind) or is_fix_commit(subject[match.end() :])
