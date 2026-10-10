"""Whether a file's text names one particular file, not just a file of that name.

A namer search matches a changed file's name (``package.json``) in source
text, and every workspace has many files of that name. A mention written with
its directory says which one it means:

- ``packages/ui/package.json`` names the file at that path, and at any path
  ending in it (the directory may be joined to a base the code computes).
- ``./package.json`` and ``../package.json`` are resolved from the namer's own
  directory, and from each directory above it, which covers module-relative
  imports and a working directory at a package or the repository root.
- A bare ``package.json``, a path holding a template or glob character, or one
  climbing out of the repository may mean any file of that name, so it names
  every one of them, as before.

A mention that only continues the name (``mypackage.json``,
``package.json5``) is a different file. Ceiling: a working directory below
the namer, or outside its ancestry, is not followed; such code reads a path
it builds at run time, which no text search sees.
"""

from __future__ import annotations

import posixpath
import re

# Characters a written path is made of; a mention's directory part is the run
# of them before the name.
_PATH_CHAR = re.compile(r"[\w.\-/\\*?\[\]{}$%@~+]")
# A directory part holding one of these is a glob or a template: any directory.
_OPEN_CHARS = frozenset("*?[]{}$%@~+")
# Continuing the name with one of these makes it another file's name.
_NAME_CHAR = re.compile(r"[\w\-]")


def names_file(text: str, namer: str, target: str) -> bool:
    """Whether *text* (of the file *namer*) holds a mention that can mean *target*."""
    name = posixpath.basename(target)
    for match in re.finditer(re.escape(name), text):
        if _continues(text, match.end()):
            continue
        prefix = _directory_part(text, match.start())
        if prefix is None:
            continue  # the name is the tail of a longer one
        if _may_mean(prefix, name, namer, target):
            return True
    return False


def _continues(text: str, end: int) -> bool:
    """Whether the name goes on past *end* (``package.json5``, ``README.md.bak``)."""
    if end < len(text) and _NAME_CHAR.match(text[end]):
        return True
    return text[end : end + 1] == "." and bool(_NAME_CHAR.match(text[end + 1 : end + 2]))


def _directory_part(text: str, start: int) -> str | None:
    """The written directory before the name at *start*, ``""`` when bare.

    ``None`` when the name only ends a longer one (``mypackage.json``).
    """
    begin = start
    while begin > 0 and _PATH_CHAR.match(text[begin - 1]):
        begin -= 1
    prefix = text[begin:start].replace("\\\\", "/").replace("\\", "/")
    if prefix and not prefix.endswith("/"):
        return None
    return prefix


def _may_mean(prefix: str, name: str, namer: str, target: str) -> bool:
    """Whether a mention written as *prefix* + *name* in *namer* can mean *target*."""
    if prefix in ("", "/") or _OPEN_CHARS & set(prefix):
        return True  # bare, joined to a computed base, or a glob or template
    written = prefix + name
    here = posixpath.dirname(namer)
    if posixpath.normpath(posixpath.join(here, written)).startswith("../"):
        return True  # climbs out of the repository from the namer: cannot tell
    base = here
    while True:
        if posixpath.normpath(posixpath.join(base, written)) == target:
            return True
        if not base:
            break
        base = posixpath.dirname(base)
    if written.startswith(("./", "../")):
        return False
    tail = written.lstrip("/")
    return target == tail or target.endswith(f"/{tail}")
