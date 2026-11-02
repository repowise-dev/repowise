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
_TYPE_KEYWORDS = r"(?:class|interface|enum|record|object|trait|typealias|message)"

#: A Kotlin ``expect`` or ``actual`` declaration is the same multiplatform type
#: in another source set, not another type.
_MULTIPLATFORM_RE = re.compile(r"\b(?:expect|actual)\b")


class JvmNameScope:
    """Answers :meth:`can_refer` over one source map.

    Every per-file fact (text, package, imports, whether it means another type
    of a name) and the per-name "declared anywhere else" answer are computed
    once, so judging every writer of a name stays linear in the writers.
    """

    def __init__(self, source_map: dict[str, bytes]) -> None:
        self._source_map = source_map
        self._texts: dict[str, str] = {}
        self._packages: dict[str, str] = {}
        self._imports: dict[str, list[str]] = {}
        self._others: dict[tuple[str, str, str], bool] = {}
        self._declared_elsewhere: dict[tuple[str, str], bool] = {}

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
        key = (name, declaring)
        elsewhere = self._declared_elsewhere.get(key)
        if elsewhere is None:
            elsewhere = any(
                self._means_other(path, name, fqn) for path in writers if path != declaring
            )
            self._declared_elsewhere[key] = elsewhere
        return not elsewhere

    def _means_other(self, path: str, name: str, fqn: str) -> bool:
        """Whether *path* declares its own *name* or imports one other than *fqn*."""
        key = (path, name, fqn)
        answer = self._others.get(key)
        if answer is None:
            answer = _declares(self._text(path), name) or any(
                imported.rsplit(".", 1)[-1] == name and imported != fqn
                for imported in self._imported(path)
            )
            self._others[key] = answer
        return answer

    def _package(self, path: str) -> str:
        package = self._packages.get(path)
        if package is None:
            match = _PACKAGE_RE.search(self._text(path))
            package = self._packages[path] = match.group(1) if match else ""
        return package

    def _imported(self, path: str) -> list[str]:
        imported = self._imports.get(path)
        if imported is None:
            imported = self._imports[path] = _IMPORT_LINE_RE.findall(self._text(path))
        return imported

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


def _declares(text: str, name: str) -> bool:
    """Whether *text* declares a type *name* that is not a multiplatform half."""
    for match in _declaration_re(name).finditer(text):
        start = text.rfind("\n", 0, match.start()) + 1
        end = text.find("\n", match.end())
        if not _MULTIPLATFORM_RE.search(text, start, end if end >= 0 else len(text)):
            return True
    return False


@lru_cache(maxsize=1024)
def _declaration_re(name: str) -> re.Pattern[str]:
    return re.compile(rf"\b{_TYPE_KEYWORDS}\s+{re.escape(name)}\b")
