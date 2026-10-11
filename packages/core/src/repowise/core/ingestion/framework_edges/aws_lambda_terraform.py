"""Lambda handlers declared in Terraform, read for :mod:`.aws_lambda`.

Three shapes name a handler and where its code lives:

* ``resource "aws_lambda_function"`` with ``handler`` and ``filename`` / ``s3_key``,
  the zip usually being ``data.archive_file.X.output_path``, whose
  ``source_dir`` or ``source_file`` is the code;
* any ``module`` block carrying both ``handler`` and ``source_path``, the
  ``terraform-aws-modules/lambda/aws`` interface (also used through a local
  ``source``).

No HCL parser is installed, so this is a bounded read of those attributes, not a
parse. Ceiling: a value must be a string literal (``${path.module}`` is the one
interpolation understood) or an ``archive_file`` reference; variables, locals,
heredocs and ``for`` expressions are not evaluated, and an attribute is taken
from the first line that assigns it anywhere in the block. A relative path
Terraform resolves from the working directory is tried from the ``.tf`` file's
directory up to the repository root.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Callable, Iterator

from ..framework_routes import match_paren

# The prefilter: a ``.tf`` file mentioning none of these is not read further.
_KEYWORDS = ("aws_lambda_function", "archive_file", "source_path")

_BLOCK_RE = re.compile(
    r'^[ \t]*(?P<kind>resource|data|module)[ \t]+"(?P<type>[^"]+)"(?:[ \t]+"(?P<name>[^"]+)")?[ \t]*\{',
    re.MULTILINE,
)
_STRING_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')
_ARCHIVE_REF_RE = re.compile(r'^"?\$?\{?data\.archive_file\.(?P<name>[\w-]+)\.output_path\}?"?$')
_PATH_MODULE = "${path.module}"
# The runtimes whose handler is ``<file>.<export>``: Node.js, Python, Ruby.
SOURCE_SUFFIXES = (".js", ".mjs", ".cjs", ".ts", ".mts", ".cts", ".py", ".rb")


def _blocks(text: str) -> Iterator[tuple[str, str, str, str]]:
    """``(kind, type, name, body)`` for each top-level block opening a line."""
    for m in _BLOCK_RE.finditer(text):
        open_idx = m.end() - 1
        close = match_paren(text, open_idx, quotes='"', hash_comments=True)
        if close != -1:
            yield m["kind"], m["type"], m["name"] or "", text[open_idx + 1 : close]


def _attr(body: str, name: str) -> str:
    """The raw expression assigned to *name*, or ``""``. A ``[`` value runs to its ``]``."""
    m = re.search(rf"^[ \t]*{name}[ \t]*=[ \t]*(\S.*)$", body, re.MULTILINE)
    if m is None:
        return ""
    if m[1].startswith("["):
        close = match_paren(body, m.start(1), quotes='"', hash_comments=True)
        return body[m.start(1) : close + 1] if close != -1 else ""
    return m[1].strip()


def _literal(expr: str) -> str | None:
    """The string an expression spells (a trailing comment allowed), or None."""
    m = _STRING_RE.match(expr)
    if m is None or expr[m.end() :].strip()[:1] not in ("", "#", "/"):
        return None
    return m[1]


def _dir_candidates(path: str, depth: int) -> tuple[str, ...]:
    """Code dirs relative to the ``.tf`` dir that *path* (a dir or a source file) may mean."""
    if path.endswith(SOURCE_SUFFIXES):
        path = posixpath.dirname(path)
    if path.startswith(_PATH_MODULE):
        rest = path[len(_PATH_MODULE) :].lstrip("/")
        return () if "${" in rest else (rest,)
    if "${" in path or path.startswith("/"):
        return ()
    return tuple("../" * up + path for up in range(depth + 1))


def _code_dirs(
    body: str, archives: dict[str, tuple[str, ...]], dirs: Callable[[str], tuple[str, ...]]
) -> tuple[str, ...]:
    """Where an ``aws_lambda_function``'s code lives, best guess first."""
    for attr in ("filename", "s3_key"):
        expr = _attr(body, attr)
        ref = _ARCHIVE_REF_RE.match(expr)
        if ref is not None:
            return archives.get(ref["name"], ())
        zip_path = _literal(expr)
        if zip_path:
            # A prebuilt zip: try the directory named like it, then its own.
            stem = posixpath.splitext(zip_path)[0]
            return dirs(stem) + dirs(posixpath.dirname(zip_path))
    return dirs("")


def _module_dirs(body: str, dirs: Callable[[str], tuple[str, ...]]) -> tuple[str, ...]:
    expr = _attr(body, "source_path")
    if expr.startswith("["):
        # A list of paths or of ``{ path = "..." }`` objects.
        paths = re.findall(r'\bpath[ \t]*=[ \t]*"([^"]*)"', expr) or _STRING_RE.findall(expr)
    else:
        paths = [p for p in (_literal(expr),) if p]
    return tuple(d for p in paths for d in dirs(p))


def terraform_handlers(
    tf_dir: str, texts: dict[str, str]
) -> Iterator[tuple[str, tuple[str, ...], str]]:
    """``(tf file, code dirs, handler)`` for each Lambda in one Terraform module dir.

    *texts* maps each ``.tf`` path in *tf_dir* to its source; an ``archive_file``
    in one file serves a function declared in another, as in Terraform.
    """
    depth = tf_dir.count("/") + 1 if tf_dir else 0

    def dirs(path: str) -> tuple[str, ...]:
        return _dir_candidates(path, depth)

    archives: dict[str, tuple[str, ...]] = {}
    declared: list[tuple[str, str, str]] = []
    for path, text in texts.items():
        for kind, block_type, name, body in _blocks(text):
            if kind == "data" and block_type == "archive_file":
                source = _literal(_attr(body, "source_dir")) or _literal(_attr(body, "source_file"))
                if source:
                    archives[name] = dirs(source)
            elif (kind, block_type) == ("resource", "aws_lambda_function") or kind == "module":
                declared.append((path, kind, body))
    for path, kind, body in declared:
        handler = _literal(_attr(body, "handler"))
        if not handler or "${" in handler:
            continue
        if kind == "module":
            if not _attr(body, "source_path"):
                continue
            yield path, _module_dirs(body, dirs), handler
        else:
            yield path, _code_dirs(body, archives, dirs), handler


def wanted(text: str) -> bool:
    """Whether a ``.tf`` file can declare a Lambda or the archive one uses."""
    return any(keyword in text for keyword in _KEYWORDS)
