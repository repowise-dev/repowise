"""Whether a JVM file's bare type name can refer to a given class.

Java, Kotlin and Scala use a type from their own package by its bare name, so
a class's name written in another file is usually a use the import graph
cannot see. It is not one when the name means a different type there: a
nested record of the same name, or a generated protobuf message imported from
another package. Reading those as uses hides a class nothing references.

A bare name in file W can refer to class ``C`` of package ``P`` when W does not
declare or explicitly import another ``C`` (either shadows it), and one of:

* W is in package ``P``;
* W writes ``P.C`` (a single-type import, a static import, a qualified use)
  or imports ``P.*``;
* no other file declares or imports a type named ``C``, so the name has
  nothing else to mean. A Kotlin ``expect``/``actual`` pair is one type.

Text-level, like the rest of the name scan. A declaration keyword followed by
the name in a comment reads as a second declaration, which only keeps a
finding reported.
"""

from __future__ import annotations

import re
from functools import lru_cache

from ...ingestion.languages.jvm_same_package import _IMPORT_LINE_RE
from ...ingestion.resolvers.jvm_workspace import _PACKAGE_RE

#: Keywords that declare a type. ``message`` is a protobuf declaration, whose
#: generated class shares the message's name.
_TYPE_KEYWORDS = r"(?:class|interface|enum|record|object|trait|message)"


class JvmNameScope:
    """Answers :meth:`can_refer` over one source map, caching per-file facts."""

    def __init__(self, source_map: dict[str, bytes]) -> None:
        self._source_map = source_map
        self._texts: dict[str, str] = {}

    def can_refer(self, name: str, declaring: str, writers: set[str], writer: str) -> bool:
        """Whether *name* written in *writer* can mean the class *declaring* declares.

        *writers* is every file writing *name*, used to tell whether the name
        is declared anywhere else.
        """
        package = self._package(declaring)
        fqn = f"{package}.{name}" if package else name
        if self._means_other(writer, name, fqn):
            return False
        if self._package(writer) == package or _names_fqn(self._text(writer), fqn, package):
            return True
        return not any(
            self._means_other(path, name, fqn) for path in writers if path != declaring
        )

    def _means_other(self, path: str, name: str, fqn: str) -> bool:
        """Whether *path* declares its own *name* or imports one other than *fqn*."""
        text = self._text(path)
        if _declaration_re(name).search(text):
            return True
        return any(
            imported.rsplit(".", 1)[-1] == name and imported != fqn
            for imported in _IMPORT_LINE_RE.findall(text)
        )

    def _package(self, path: str) -> str:
        match = _PACKAGE_RE.search(self._text(path))
        return match.group(1) if match else ""

    def _text(self, path: str) -> str:
        text = self._texts.get(path)
        if text is None:
            text = self._source_map.get(path, b"").decode("utf-8", "replace")
            self._texts[path] = text
        return text


def _names_fqn(text: str, fqn: str, package: str) -> bool:
    """Whether *text* writes *fqn* or imports every type of *package*."""
    if not package:
        return False  # a class in the default package cannot be imported
    pattern = rf"(?<![\w.]){re.escape(fqn)}\b|^\s*import\s+{re.escape(package)}\.[*_]"
    return re.search(pattern, text, re.MULTILINE) is not None


@lru_cache(maxsize=1024)
def _declaration_re(name: str) -> re.Pattern[str]:
    # A Kotlin ``expect`` or ``actual`` declaration is the same multiplatform
    # type in another source set, not another type.
    return re.compile(
        rf"^(?![^\n]*\b(?:expect|actual)\b)[^\n]*?\b{_TYPE_KEYWORDS}\s+{re.escape(name)}\b",
        re.MULTILINE,
    )
