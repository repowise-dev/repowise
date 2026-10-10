"""Whether a file's text names one particular file, not just a file of that name.

A namer search matches a changed file's name (``package.json``) in source
text, and every workspace has many files of that name. A mention written with
its directory says which one it means. Each mention is reduced to its written
path, with leading ``/``, ``./`` and ``../`` steps and inner ``a/../`` folded
away (a relative path depends on the working directory, which the text does
not give), and compared to the file's path, case-insensitively (the name too):

- a written path names every file whose path ends in it
  (``ui/package.json`` names ``packages/ui/package.json``), and every file
  whose path it ends in (``/app/packages/ui/package.json``,
  ``C:\\repo\\packages\\ui\\package.json``);
- a bare name (``"package.json"``, ``../package.json``,
  ``root + "/package.json"``), a glob or a template names every file of that
  name.

A mention that only continues the name (``mypackage.json``, ``package.json5``)
is another file; an escape just before the name (``"\\npackage.json"``) is not
part of it. Ceiling: a path built from separate parts
(``ROOT / "ui" / "package.json"``) reads as the bare name, so it names every
file of that name.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterable

# Characters a written path is made of; a mention's directory part is the run
# of them before the name.
_PATH_CHAR = re.compile(r"[\w.\-/\\*?\[\]{}$%@~+]")
# A directory part holding one of these is a glob or a template: any directory.
_OPEN_CHARS = frozenset("*?[]{}$%@~+")
# Continuing the name with one of these makes it another file's name.
_NAME_CHAR = re.compile(r"[\w\-]")


def names_file(text: str, target: str) -> bool:
    """Whether *text* holds a mention that can mean *target*."""
    return any_may_mean(mentions(text, posixpath.basename(target)), target)


def mentions(text: str, name: str) -> list[str | None]:
    """Each mention of *name* in *text* as its written path, ``None`` for any directory.

    A mention that is only part of a longer name is left out.
    """
    out: list[str | None] = []
    for match in re.finditer(re.escape(name), text, re.IGNORECASE):
        if _continues(text, match.end()):
            continue
        prefix = _directory_part(text, match.start())
        if prefix is None:
            continue
        written = _strip_relative(prefix + name)
        out.append(None if _OPEN_CHARS & set(prefix) or written == name else written)
    return out


def any_may_mean(written: Iterable[str | None], target: str) -> bool:
    """Whether any mention reduced by :func:`mentions` can mean *target*."""
    path = target.casefold()
    for mention in written:
        if mention is None:
            return True
        tail = mention.casefold()
        if tail == path or path.endswith(f"/{tail}") or tail.endswith(f"/{path}"):
            return True
    return False


def _continues(text: str, end: int) -> bool:
    """Whether the name goes on past *end* (``package.json5``, ``README.md.bak``)."""
    if end < len(text) and _NAME_CHAR.match(text[end]):
        return True
    return text[end : end + 1] == "." and bool(_NAME_CHAR.match(text[end + 1 : end + 2]))


def _directory_part(text: str, start: int) -> str | None:
    """The written directory before the name at *start*, ``""`` when bare.

    ``None`` when the name only ends a longer one (``mypackage.json``). An
    escape such as ``\\n`` just before the name is a boundary, not a letter.
    """
    if start >= 2 and text[start - 2] == "\\" and text[start - 1].isalpha():
        return ""
    begin = start
    while begin > 0 and _PATH_CHAR.match(text[begin - 1]):
        begin -= 1
    prefix = text[begin:start].replace("\\\\", "/").replace("\\", "/")
    if prefix and not prefix.endswith("/"):
        return None
    return prefix


def _strip_relative(written: str) -> str:
    """*written* without leading ``/``, ``./`` or ``../`` steps, inner ``a/../`` folded."""
    parts: list[str] = []
    for part in written.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)
