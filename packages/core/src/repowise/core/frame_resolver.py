"""Profile frames joined to indexed symbols, precision first.

A frame names a file, a line and a function as a profiler or a runtime wrote
them. :func:`resolve_frames` joins each to the symbol of the indexed commit
that ran it, or skips it and says why. Nothing is guessed: a skipped frame is
counted under its reason, and a frame that resolves passed two independent
checks, the span lookup and a second signal from the frame itself.

Kept apart from :mod:`repowise.core.stack_trace` on purpose. That module reads
pasted traces and matches a frame to a repo file by path suffix, which is right
for a trace copied from anywhere and wrong for a profile: a profile can carry
frames from another checkout of the same project, and a suffix match would
attach them here. This module accepts a frame only under the checkout root the
caller names.
"""

from __future__ import annotations

import posixpath
import re
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import unquote

from repowise.core.callable_spans import body_span
from repowise.core.code_origin import path_origin
from repowise.core.symbol_spans import symbol_at

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable, Mapping, Sequence

    from repowise.core.workspace.repo_index import IndexedSymbol

FrameLanguage = Literal["go", "python", "javascript", "java", "kotlin"]

#: JVM frames: no file of their own, placed by class name (:func:`place_jvm_frames`).
_JVM = frozenset({"java", "kotlin"})
#: Source files a JVM class may be compiled from, as its frames are placed on them.
JVM_SOURCE_SUFFIXES = (".java", ".kt")

#: Why a frame was not joined. A frame is counted under exactly one.
SkipReason = Literal[
    "outside_root",  # not under the checkout root: runtime, dependency, other checkout
    "unsupported_language",
    "bad_line",  # no 1-based line
    "dirty",  # the file differs from the indexed commit
    "generated",
    "vendored",  # a dependency installed inside the checkout (node_modules, a venv)
    "not_indexed",  # under the root, but the index has no such file
    "no_span",  # no indexed symbol holds (JavaScript: starts at) the line
    "ambiguous",  # several indexed functions fit and nothing in the frame picks one
    "no_second_signal",  # the frame lacks the field its language is checked on
    "no_source",  # JavaScript: the start line could not be read to check the column
    "second_signal_mismatch",
]

_CALLABLE_KINDS = frozenset({"function", "method", "constructor"})
_DRIVE_RE = re.compile(r"^[A-Za-z]:/")
# What may stand on a JavaScript line before the function that line declares:
# keywords, a binding or property name, ``=`` and ``:``. A bracket, a call, a
# string, a separator or an arrow before the frame's column means some other
# function could start there, so the column does not single out the symbol.
_JS_HEAD_RE = re.compile(r"[\w$.\s=:*#\[\]\ufeff]*")


@dataclass(frozen=True)
class ProfileFrame:
    """One frame as the profiler wrote it.

    *line* is 1-based: the executing line for Go, Python and the JVM, the
    function's start line for JavaScript (V8 records no executing line); 0
    when the profiler wrote none. *column* is the 1-based column V8 gives for
    a function's start. *start_line* is the function's start line where the
    format records it (Go ``s=``). *function* is the name the profiler gave:
    Python's ``co_qualname`` (or ``co_name``), Go's function name, V8's
    ``functionName``, a JVM ``class.method`` with its parameter types or
    descriptor when the profiler wrote them (:func:`parse_jvm_frame`).
    """

    path: str
    line: int
    language: FrameLanguage
    function: str = ""
    column: int | None = None
    start_line: int | None = None
    calls: int | None = None


@dataclass(frozen=True)
class ResolvedFrame:
    #: Position of the frame in the input.
    index: int
    #: Repo-relative POSIX path, spelled as the index spells it.
    path: str
    #: The symbol that ran the frame; None when it ran the file's module body.
    symbol: IndexedSymbol | None
    #: The frame was a closure, lambda, generator expression or nested function
    #: with no symbol of its own, charged to the symbol (or file) around it.
    folded: bool = False


@dataclass(frozen=True)
class FrameResolution:
    resolved: tuple[ResolvedFrame, ...]
    #: Per input frame: why it was skipped, or None when it resolved.
    reasons: tuple[SkipReason | None, ...]

    @property
    def skipped(self) -> dict[SkipReason, int]:
        """Frames per skip reason. A reason no frame hit is absent, not zero."""
        return dict(Counter(r for r in self.reasons if r is not None))


#: What a frame resolved to: the symbol (None for the file) and whether it was folded.
_Hit = tuple["IndexedSymbol | None", bool]


def resolve_frames(
    frames: Iterable[ProfileFrame],
    *,
    root: str,
    symbols_by_file: Mapping[str, Sequence[IndexedSymbol]],
    dirty_paths: Collection[str] = (),
    origins: Mapping[str, str] | None = None,
    read_lines: Callable[[str], Sequence[str] | None] | None = None,
) -> FrameResolution:
    """Join *frames* to the symbols of one indexed commit.

    *root* is the checkout the profile ran in, as the profiler saw it (an OS
    path; ``file://`` URLs in frames are decoded). *symbols_by_file* holds the
    indexed commit's symbols per repo-relative file; an indexed file with no
    symbols maps to an empty sequence. *dirty_paths* are repo-relative files
    that differ from that commit. *origins* is the stored ``code_origin`` per
    file; a file without one falls back to :func:`path_origin`. *read_lines*
    returns a repo-relative file's lines from the checkout, for the JavaScript
    checks and Python decorator lines; without it those frames are skipped.

    Second signals, after the span lookup: Go needs the index start line to
    equal the frame's ``start_line``; Python needs the frame's qualified name
    to be a dotted tail of the symbol's; JavaScript joins by position only:
    exactly one indexed function starts on the frame's line, and nothing but
    names, keywords, ``=`` and ``:`` stands before the frame's column, so it is
    the first function the line can start. A JVM frame (Java, Kotlin) needs
    the method name and class to agree with the innermost method at its line;
    with no line it joins by name (:func:`_name_join`): the one method of that
    name in that class, else the one whose parameter count (and types) match
    the frame's, else ``ambiguous``. A frame is never put on one overload of
    several by name alone.

    Folding, when the frame's own function has no symbol: a Go closure
    (``Outer.func1``) whose start line lies inside ``Outer``; a Python frame
    whose qualified name runs through ``<locals>`` or ends in ``<lambda>``,
    ``<genexpr>`` or a comprehension, charged to the symbol its enclosing name
    matches; a JavaScript function that starts on a body line of an indexed
    function. Module bodies (Python ``<module>``, the Go package initializer,
    the CommonJS wrapper at 1:1, a JVM static initializer) resolve to the
    file, and a Python frame on a decorator line resolves to the function it
    decorates. A JVM lambda (``lambda$name$0``, Kotlin ``name$lambda$0``) and a
    method of an anonymous class (``Outer$1``) fold into the method at their
    line; compiler-made accessors and bridges (``access$000``, ``$default``,
    ``$$Lambda``, ``$$`` proxies) are ``generated``.
    """
    paths = _PathMap(root, symbols_by_file, dirty_paths)
    reader = _read_once(read_lines) if read_lines is not None else None
    resolved: list[ResolvedFrame] = []
    reasons: list[SkipReason | None] = []
    for i, frame in enumerate(frames):
        outcome = _resolve(frame, paths, symbols_by_file, origins, reader)
        if isinstance(outcome, tuple):
            path, (symbol, folded) = outcome
            resolved.append(ResolvedFrame(i, path, symbol, folded))
            reasons.append(None)
        else:
            reasons.append(outcome)
    return FrameResolution(tuple(resolved), tuple(reasons))


def frame_paths(frames: Iterable[ProfileFrame], *, root: str) -> set[str]:
    """The repo-relative paths of the *frames* that sit under *root*.

    What a caller loads symbols for before :func:`resolve_frames`, so a
    profile with frames in thousands of runtime files reads only its own.
    """
    paths = _PathMap(root, {}, ())
    return {rel for frame in frames if (rel := paths.relative(frame.path)) is not None}


def checkout_reader(root: str | Path) -> Callable[[str], list[str] | None]:
    """A ``read_lines`` for :func:`resolve_frames` that reads files under *root*."""
    base = Path(root)

    def read_lines(rel: str) -> list[str] | None:
        try:
            return (base / rel).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return None

    return read_lines


def dirty_path_checker(root: str, dirty_paths: Collection[str]) -> Callable[[str], bool]:
    """Check repo-relative dirty paths using the resolver's platform rules."""
    return _PathMap(root, {}, dirty_paths).is_dirty


async def load_frame_symbols(
    session: Any, repository_id: str, paths: Collection[str], *, fold_case: bool = False
) -> tuple[dict[str, list[IndexedSymbol]], dict[str, str]]:
    """``symbols_by_file`` and ``origins`` for :func:`resolve_frames`, from the store.

    A path is indexed when it has a symbol or a stored health row, so a file
    indexed with no symbols still maps (to an empty list). Paths match
    exactly; with *fold_case* (a Windows checkout) a path no exact match found
    is looked up again without case, and :func:`resolve_frames` then takes the
    index's spelling.
    """
    symbols: dict[str, list[IndexedSymbol]] = {}
    origins: dict[str, str] = {}
    await _load_spans(session, repository_id, sorted(set(paths)), symbols, origins, fold=False)
    if fold_case:
        found = {p.casefold() for p in symbols}
        missed = sorted({p.lower() for p in paths if p.casefold() not in found})
        # A lower() lookup cannot use the path index, so only the misses take it.
        await _load_spans(session, repository_id, missed, symbols, origins, fold=True)
    return symbols, origins


async def _load_spans(
    session: Any,
    repository_id: str,
    wanted: list[str],
    symbols: dict[str, list[IndexedSymbol]],
    origins: dict[str, str],
    *,
    fold: bool,
) -> None:
    from sqlalchemy import func, select

    from repowise.core.persistence.models import HealthFileMetric, WikiSymbol
    from repowise.core.workspace.repo_index import IndexedSymbol

    def match(column: Any, chunk: list[str]) -> Any:
        return (func.lower(column) if fold else column).in_(chunk)

    for i in range(0, len(wanted), 400):
        chunk = wanted[i : i + 400]
        rows = await session.execute(
            select(
                WikiSymbol.symbol_id, WikiSymbol.name, WikiSymbol.qualified_name,
                WikiSymbol.kind, WikiSymbol.file_path, WikiSymbol.start_line,
                WikiSymbol.end_line,
            ).where(WikiSymbol.repository_id == repository_id, match(WikiSymbol.file_path, chunk))
        )
        for r in rows:
            symbols.setdefault(r.file_path, []).append(
                IndexedSymbol(
                    symbol_id=r.symbol_id, name=r.name, qualified_name=r.qualified_name,
                    kind=r.kind, signature="", file_path=r.file_path,
                    start_line=r.start_line, end_line=r.end_line, visibility="",
                )
            )
        metrics = await session.execute(
            select(HealthFileMetric.file_path, HealthFileMetric.code_origin).where(
                HealthFileMetric.repository_id == repository_id,
                match(HealthFileMetric.file_path, chunk),
            )
        )
        for path, origin in metrics:
            symbols.setdefault(path, [])
            if origin:
                origins[path] = origin


def _resolve(
    frame: ProfileFrame,
    paths: _PathMap,
    symbols_by_file: Mapping[str, Sequence[IndexedSymbol]],
    origins: Mapping[str, str] | None,
    read_lines: Callable[[str], list[str] | None] | None,
) -> tuple[str, _Hit] | SkipReason:
    rel = paths.relative(frame.path)
    if rel is None:
        return "outside_root"
    if frame.language not in ("go", "python", "javascript", *_JVM):
        return "unsupported_language"
    # A JVM frame without a line still has its class, method and parameters.
    if frame.line < 1 and frame.language not in _JVM:
        return "bad_line"
    indexed = paths.indexed(rel)
    if indexed is not None:
        rel = indexed
    if paths.is_dirty(rel):
        return "dirty"
    origin = (origins or {}).get(rel) or path_origin(rel)
    if origin == "generated":
        return "generated"
    if origin == "vendored":
        return "vendored"
    if indexed is None:
        return "not_indexed"
    symbols = symbols_by_file[rel]
    source = rel

    def lines() -> list[str] | None:
        return read_lines(source) if read_lines is not None else None

    if frame.language == "javascript":
        outcome = _javascript(frame, symbols, lines)
    elif frame.language == "go":
        outcome = _go(frame, symbols)
    elif frame.language in _JVM:
        outcome = _jvm(frame, symbols, kotlin=rel.endswith(".kt"))
    else:
        outcome = _python(frame, symbols, lines)
    return (rel, outcome) if isinstance(outcome, tuple) else outcome


# -- Go ------------------------------------------------------------------------

# Compiler-made names for a function literal inside another: ``Outer.func1``,
# ``Outer.func1.2``, ``Outer.gowrap1``, ``Outer.deferwrap1``.
_GO_CLOSURE_RE = re.compile(r"(?:\.(?:func|gowrap|deferwrap)\d+(?:\.\d+)*)+$")
_GO_TYPE_ARGS_RE = re.compile(r"\[[^\]]*\]")


def _go(frame: ProfileFrame, symbols: Sequence[IndexedSymbol]) -> _Hit | SkipReason:
    # Go frames run in functions only; a type or var span is never the answer.
    symbol = symbol_at((s for s in symbols if s.kind in _CALLABLE_KINDS), frame.line)
    name = _GO_TYPE_ARGS_RE.sub("", frame.function)
    closure = _GO_CLOSURE_RE.search(name)
    base = name[: closure.start()] if closure else name
    if symbol is None:
        # ``pkg.init`` is the initializer the compiler writes for package-level
        # vars: module body, so the file. A user ``func init()`` is ``init.0``,
        # and a method named init carries a receiver.
        package_init = re.fullmatch(r"[^.()]+\.init", base.rsplit("/", 1)[-1])
        return (None, closure is not None) if package_init else "no_span"
    if frame.start_line is None:
        return "no_second_signal"
    if symbol.start_line == frame.start_line:
        # A closure opening on its parent's first line shares its start line.
        return (symbol, closure is not None and base.rsplit(".", 1)[-1] == symbol.name)
    if (
        closure is not None
        and symbol.start_line < frame.start_line <= symbol.end_line
        and base.rsplit(".", 1)[-1] == symbol.name
    ):
        return (symbol, True)
    return "second_signal_mismatch"


# -- Python --------------------------------------------------------------------

# Code objects with no def of their own: a nested scope charged to its parent.
_PY_ANONYMOUS = frozenset({"<lambda>", "<genexpr>", "<listcomp>", "<dictcomp>", "<setcomp>"})
_PY_DECORATOR_RE = re.compile(r"\s*@")


def _python(
    frame: ProfileFrame,
    symbols: Sequence[IndexedSymbol],
    lines: Callable[[], list[str] | None],
) -> _Hit | SkipReason:
    name = frame.function
    if name == "<module>":
        return (None, False)
    if not name:
        return "no_second_signal"
    symbol = symbol_at(symbols, frame.line)
    # An indexed nested def: py-spy says ``f.<locals>.g``, the index ``f.g``.
    plain = name.replace(".<locals>", "")
    if symbol is not None and _qualname_tail(plain, symbol.qualified_name):
        return (symbol, False)
    decorated = _decorated(frame.line, symbols, lines)
    if decorated is not None and _qualname_tail(plain, decorated.qualified_name):
        return (decorated, False)
    return _python_fold(name, symbol)


def _python_fold(name: str, symbol: IndexedSymbol | None) -> _Hit | SkipReason:
    """A nested code object charged to the scope it runs inside, or why not."""
    scopes = _python_scopes(name)
    miss: SkipReason = "no_span" if symbol is None else "second_signal_mismatch"
    if not scopes:
        return miss
    if scopes == [""]:
        # A lambda or generator expression at module level: the file's.
        return (None, True) if symbol is None else miss
    # The innermost indexed symbol at the line must be one of the scopes the
    # frame runs inside; the nearest such scope is the one it folds into.
    if symbol is not None and any(_qualname_tail(sc, symbol.qualified_name) for sc in scopes):
        return (symbol, True)
    return miss


def _python_scopes(qualname: str) -> list[str]:
    """The scopes a nested code object runs inside, innermost first.

    ``A.f.<locals>.g.<locals>.h`` -> ``["A.f.g", "A.f"]``; ``A.<lambda>`` ->
    ``["A"]``; ``<genexpr>`` -> ``[""]`` (module level); empty when
    *qualname* is not nested.
    """
    parts = qualname.split(".")
    cuts = [i for i, part in enumerate(parts) if part == "<locals>"]
    if parts[-1] in _PY_ANONYMOUS and (len(parts) == 1 or parts[-2] != "<locals>"):
        cuts.append(len(parts) - 1)
    scopes = [".".join(p for p in parts[:cut] if p != "<locals>") for cut in reversed(cuts)]
    return list(dict.fromkeys(scopes))


def _decorated(
    line: int, symbols: Sequence[IndexedSymbol], lines: Callable[[], list[str] | None]
) -> IndexedSymbol | None:
    """The symbol a decorator on *line* applies to, or None.

    A Python span starts at ``def``/``class``, below its decorators. The
    symbol is the nearest one opening below *line* with no symbol ending in
    between, so only more decorators (or their arguments) separate them.
    """
    text = lines()
    if text is None or line > len(text) or not _PY_DECORATOR_RE.match(text[line - 1]):
        return None
    below = [s for s in symbols if s.start_line > line]
    if not below:
        return None
    nearest = symbol_at(below, min(s.start_line for s in below))
    if nearest is None or any(line <= s.end_line < nearest.start_line for s in symbols):
        return None
    return nearest


def _qualname_tail(frame_name: str, qualified_name: str) -> bool:
    """Whether *frame_name* (``Flask.__init__``) ends *qualified_name* at a dot."""
    return qualified_name == frame_name or qualified_name.endswith("." + frame_name)


# -- JVM (Java, Kotlin) ----------------------------------------------------------

# ``pkg.Outer$Inner.method`` (``jfr print``, jfrconv ``--dot``) or
# ``pkg/Outer$Inner.method`` (async-profiler), then parameter types
# ``(Object, int[])`` or a descriptor ``(Ljava/lang/Object;[I)V``, then a line
# ``:42`` and async-profiler's frame-type mark ``_[j]`` in either order. The
# outermost class name starts upper case, as Java and Kotlin classes do; that
# keeps Go's ``main.main`` and a bare ``runtime.mallocgc`` out.
_JVM_FRAME_RE = re.compile(
    r"(?P<package>(?:[A-Za-z_$][\w$]*[./])*?)"
    r"(?P<cls>[A-Z$][\w$]*)\."
    r"(?P<method>[A-Za-z_$][\w$]*|<init>|<clinit>)"
    r"(?P<params>\([^()]*\)\S*?)?"
    r"(?::(?P<line>\d+))?(?:_\[\w+\])?(?::(?P<line2>\d+))?"
)
_JVM_PRIMITIVES = {
    "B": "byte", "C": "char", "D": "double", "F": "float",
    "I": "int", "J": "long", "S": "short", "Z": "boolean", "V": "void",
}
_JVM_DESCRIPTOR_TYPE_RE = re.compile(r"\[*(?:[BCDFIJSZ]|L[^;]+;)")
# javac's ``lambda$name$0`` and Kotlin's ``name$lambda$0`` (or ``name$lambda-0``).
_JVM_LAMBDA_RE = re.compile(r"lambda\$(?P<a>\w+?)\$\d+|(?P<b>\w+?)\$lambda[$-]\d+")


@dataclass(frozen=True)
class JvmFrameName:
    """A JVM frame's name, split: ``com.acme`` / ``Outer$1`` / ``read`` / ``("JsonReader",)``."""

    package: tuple[str, ...]
    #: The class as the JVM names it, nested classes after ``$``: ``Outer$Inner``, ``Outer$1``.
    cls: str
    method: str
    #: Erased simple parameter types (``int[]``, ``Object``); None when the frame has none.
    params: tuple[str, ...] | None = None
    #: The line written into the name (``:42``); 0 when there is none.
    line: int = 0


def parse_jvm_frame(text: str) -> JvmFrameName | None:
    """*text* as a JVM frame name, or None when it is not one."""
    m = _JVM_FRAME_RE.fullmatch(text.strip())
    if m is None:
        return None
    package = tuple(p for p in re.split(r"[./]", m["package"]) if p)
    params = _jvm_params(m["params"]) if m["params"] else None
    return JvmFrameName(package, m["cls"], m["method"], params, int(m["line"] or m["line2"] or 0))


def _jvm_params(text: str) -> tuple[str, ...]:
    """``(Object, int[])`` or ``(Ljava/lang/Object;[I)V`` -> ``("Object", "int[]")``."""
    inner, _, returns = text[1:].partition(")")
    if returns:
        # A descriptor: the return type follows the parameters.
        out = []
        for t in _JVM_DESCRIPTOR_TYPE_RE.findall(inner):
            dims = len(t) - len(t.lstrip("["))
            t = t[dims:]
            base = _JVM_PRIMITIVES.get(t) or re.split(r"[/$]", t[1:-1])[-1]
            out.append(base + "[]" * dims)
        return tuple(out)
    return tuple(
        re.split(r"[.$]", p.strip().replace("...", "[]"))[-1] for p in inner.split(",") if p.strip()
    )


def place_jvm_frames(
    frames: Sequence[ProfileFrame], *, root: str, files: Collection[str]
) -> tuple[ProfileFrame, ...]:
    """*frames* with each JVM frame put on the indexed source file of its class.

    A JVM profile names classes, not files. A JVM frame whose path is empty or
    a bare file name (``Gson.java``) gets the one file in *files*
    (repo-relative) whose path ends in its package directories and outermost
    class: ``com.acme.Cache$Entry.get`` -> ``.../com/acme/Cache.java`` or
    ``.kt``, and a Kotlin file class ``UtilKt`` -> ``.../Util.kt``. A line
    written into the name (``:42``) becomes the frame's line. With no such
    file, or more than one (two modules declaring one class), the frame keeps
    its path and stays outside the checkout. Ceiling: a Kotlin class in a file
    named otherwise, or outside its package directories, is not found.
    """
    by_name: dict[str, list[str]] = {}
    for rel in files:
        by_name.setdefault(rel.rsplit("/", 1)[-1], []).append(rel)
    base = posixpath.normpath(root.replace("\\", "/")).rstrip("/")
    placed: dict[tuple[str, ...], str | None] = {}
    out: list[ProfileFrame] = []
    for frame in frames:
        name = parse_jvm_frame(frame.function) if frame.language in _JVM else None
        if name is None or "/" in frame.path.replace("\\", "/"):
            out.append(frame)
            continue
        outer = name.cls.split("$", 1)[0]
        key = (*name.package, outer)
        if key not in placed:
            stems = [outer, outer[:-2]] if outer.endswith("Kt") and len(outer) > 2 else [outer]
            tails = ["/".join((*name.package, stem + ext)) for stem in stems for ext in JVM_SOURCE_SUFFIXES]
            found = {
                rel
                for tail in tails
                for rel in by_name.get(tail.rsplit("/", 1)[-1], ())
                if (rel == tail or rel.endswith("/" + tail))
                and (name.package or _at_source_root(rel))
                and path_origin(rel) not in ("vendored", "generated")
            }
            placed[key] = found.pop() if len(found) == 1 else None
        rel = placed[key]
        if rel is None:
            out.append(frame)
            continue
        language: FrameLanguage = "kotlin" if rel.endswith(".kt") else "java"
        out.append(replace(frame, path=f"{base}/{rel}", language=language, line=frame.line or name.line))
    return tuple(out)


#: Where a class in the default package can sit: a JVM source root, or the checkout's root.
_JVM_SOURCE_ROOTS = ("src/main/java", "src/main/kotlin", "src/test/java", "src/test/kotlin", "src", "java", "kotlin")


def _at_source_root(rel: str) -> bool:
    """Whether *rel* sits directly in a source root, as a default-package class's file does.

    Without a package, a file name alone would match ``com/acme/Main.java``
    for a frame of the default package's ``Main``.
    """
    parent = rel.rpartition("/")[0]
    return parent == "" or any(parent == r or parent.endswith("/" + r) for r in _JVM_SOURCE_ROOTS)


async def load_jvm_source_paths(session: Any, repository_id: str) -> list[str]:
    """The indexed ``.java`` and ``.kt`` files, for :func:`place_jvm_frames`."""
    from sqlalchemy import or_, select

    from repowise.core.persistence.models import HealthFileMetric, WikiSymbol

    paths: set[str] = set()
    for model in (WikiSymbol, HealthFileMetric):
        rows = await session.execute(
            select(model.file_path).distinct().where(
                model.repository_id == repository_id,
                or_(*(model.file_path.like(f"%{suffix}") for suffix in JVM_SOURCE_SUFFIXES)),
            )
        )
        paths.update(r[0] for r in rows)
    return sorted(paths)


def _jvm(frame: ProfileFrame, symbols: Sequence[IndexedSymbol], *, kotlin: bool) -> _Hit | SkipReason:
    name = parse_jvm_frame(frame.function)
    if name is None:
        return "no_second_signal"
    method, segments = name.method, name.cls.split("$")
    # Compiler-made trampolines with no source of their own: the caller keeps the time.
    if (
        method.startswith("access$")
        or method.endswith("$default")
        or method == "$values"
        or "" in segments  # ``Outer$$Lambda``, ``Outer$$EnhancerBySpringCGLIB$$1a2b``
        # javac's access constructor ``Main(Main$1)`` (Java 10 and older) takes a marker class.
        or any(p.isdigit() for p in name.params or ())
    ):
        return "generated"
    if method == "<clinit>":
        return (None, False)
    # A local class is ``Outer$1Local``: the index names it ``Local``.
    segments = [s if s.isdigit() else s.lstrip("0123456789") for s in segments]
    named = [s for s in segments if not s.isdigit()]
    anonymous = segments[-1].isdigit()
    lam = _JVM_LAMBDA_RE.fullmatch(method)
    enclosing = (lam["a"] or lam["b"]) if lam else None
    owner = segments[-1]
    if kotlin and len(segments) == 1 and owner.endswith("Kt"):
        owner = ""  # a top-level function: the index nests it in no class
    init = method == "<init>"
    wanted = named[-1] if init else method
    callables = [s for s in symbols if s.kind in _CALLABLE_KINDS]
    # A compiled parameter count is the source's for a Java method, and for a
    # top-level class's constructor; not for a nested class's (its outer
    # instance), an enum's (name and ordinal) or Kotlin's (an extension's receiver).
    by_count = not kotlin and (not init or (len(segments) == 1 and any(
        s.name == wanted and s.kind == "class" for s in symbols
    )))
    if frame.line < 1:
        if lam is not None or anonymous:
            return "no_second_signal"  # only a line tells which lambda or anonymous class
        return _name_join(callables, owner=owner, name=wanted, params=name.params, by_count=by_count)
    symbol = symbol_at(callables, frame.line)
    if symbol is None:
        if enclosing == "static" or (anonymous and init):
            # A lambda or anonymous class in a static field: load-time code, the file's.
            return (None, True)
        if init and not anonymous:
            # A field initializer runs in the constructor, on the field's line.
            return _name_join(callables, owner=owner, name=wanted, params=name.params, by_count=by_count)
        if lam is None and (anonymous or any(
            s.start_line == frame.line and s.name == named[-1] for s in symbols
        )):
            # javac puts a bridge method (a generic override's erased twin) on its class's line.
            return "generated"
        return "no_span"
    if lam is None and not anonymous and symbol.name == wanted and _id_parent(symbol) == owner:
        # The line found the method; the frame's parameters, where it has them, must agree,
        # or a line that drifted onto a neighbouring overload would join it.
        return (symbol, False) if _params_agree(symbol, name.params, by_count) else "second_signal_mismatch"
    if enclosing is not None:
        parent = named[-1] if enclosing == "new" else enclosing
        if symbol.name == parent and _jvm_in_class(named, symbol):
            return (symbol, True)
        return "second_signal_mismatch"
    if anonymous and _jvm_in_class(named, symbol):
        # The index holds an anonymous class's methods under the method or field
        # that declares it (``create::TypeAdapter::read``). Its constructor runs
        # where it is declared, and a Kotlin lambda class (``Foo$bar$1``) is
        # named after its function: both fold into the method at the line.
        if not init and symbol.name == method:
            return (symbol, False)
        if init or (len(segments) > 1 and segments[-2] == symbol.name):
            return (symbol, True)
        # On the declaring line: a bridge javac wrote, called through the interface.
        return "generated"
    return "second_signal_mismatch"


def _id_parent(symbol: IndexedSymbol) -> str:
    """The scope an id nests *symbol* in: ``A.java::Adapter::write#1`` -> ``Adapter``; ``""`` for none."""
    from repowise.core.ingestion.symbol_identity import id_segment_name, local_symbol_id

    segments = local_symbol_id(symbol.symbol_id).split("::")
    return id_segment_name(segments[-2]) if len(segments) > 1 else ""


def _jvm_in_class(named: Sequence[str], symbol: IndexedSymbol) -> bool:
    """Whether the frame's named classes appear, in order, in *symbol*'s qualified name."""
    parts = iter(symbol.qualified_name.split("."))
    return all(any(p == n for p in parts) for n in named)


def _name_join(
    callables: Sequence[IndexedSymbol],
    *,
    owner: str,
    name: str,
    params: Sequence[str] | None,
    by_count: bool,
) -> _Hit | SkipReason:
    """The one callable named *name* directly in *owner*, for a frame its line does not join.

    The seam for any language whose frames carry a type and a method (a JVM
    collapsed stack without lines, a .NET trace): *owner* is the innermost
    type as the id spells it (``""`` for a top-level function), *params* the
    frame's erased simple parameter types when it has them. Several
    candidates narrow by the parameter count in the id (``#2``) only with
    *by_count*, since a compiled count can differ from the source's (an inner
    class's constructor takes its outer instance, an enum's its name and
    ordinal, a Kotlin extension its receiver); then by the types the id spells
    when a count is shared (``#2(Object,Type)``), which must equal the
    frame's. Still several: ``ambiguous``; none: ``no_span``. One of several
    overloads is never picked by name alone.
    """
    from repowise.core.ingestion.symbol_identity import local_symbol_id, split_symbol_id

    # Direct members only: an anonymous class's method is ided under the method
    # that declares it (``C::m::Impl::f``), and its supertype's name is no owner.
    found = [
        s
        for s in callables
        if s.name == name and _id_parent(s) == owner and local_symbol_id(s.symbol_id).count("::") <= 1
    ]
    if len(found) == 1:
        return (found[0], False) if _params_agree(found[0], params, by_count) else "second_signal_mismatch"
    if not found:
        return "no_span"
    if params is None:
        return "ambiguous"
    payloads = {s.symbol_id: split_symbol_id(s.symbol_id)[1] or "" for s in found}
    # Narrow only when every member carries its count: an id without one is not an overload.
    if by_count and all(payloads[s.symbol_id][:1].isdigit() for s in found):
        found = [s for s in found if payloads[s.symbol_id].partition("(")[0] == str(len(params))]
        if len(found) == 1:
            return (found[0], False)
    types = ",".join(params) + ")"
    typed = [s for s in found if payloads[s.symbol_id].partition("(")[1:] == ("(", types)]
    return (typed[0], False) if len(typed) == 1 else "ambiguous"


def _params_agree(symbol: IndexedSymbol, params: Sequence[str] | None, by_count: bool) -> bool:
    """Whether the frame's parameters fit the count and types *symbol*'s id spells.

    Checked only where a compiled count is the source's (*by_count*); an id
    with no count says nothing, and a frame with no parameters is not checked.
    """
    from repowise.core.ingestion.symbol_identity import split_symbol_id

    payload = split_symbol_id(symbol.symbol_id)[1]
    if params is None or not by_count or not payload or not payload[:1].isdigit():
        return True
    count, paren, types = payload.partition("(")
    if int(count) != len(params):
        return False
    return not paren or types == ",".join(params) + ")"


# -- JavaScript ----------------------------------------------------------------


def _javascript(
    frame: ProfileFrame,
    symbols: Sequence[IndexedSymbol],
    lines: Callable[[], list[str] | None],
) -> _Hit | SkipReason:
    if frame.column is None or frame.column < 1:
        return "no_second_signal"
    if _js_module_body(frame):
        return (None, False)
    callables = [s for s in symbols if s.kind in _CALLABLE_KINDS]
    starting = [s for s in callables if s.start_line == frame.line]
    if len(starting) > 1:
        return "ambiguous"
    text = lines()
    if text is None or frame.line > len(text):
        return "no_source"
    if starting:
        return _js_starting(frame.line, frame.column, starting[0], callables, text)
    return _js_fold(frame.line, callables, text, "no_span")


def _js_module_body(frame: ProfileFrame) -> bool:
    """The CommonJS wrapper (and an ES module's top level) starts at 1:1, unnamed."""
    return frame.line == 1 and frame.column == 1 and not frame.function


def _js_starting(
    line: int,
    column: int,
    symbol: IndexedSymbol,
    callables: Sequence[IndexedSymbol],
    text: list[str],
) -> _Hit | SkipReason:
    """*symbol*, the one function starting on *line*, when the column picks it out."""
    # V8 counts columns in UTF-16 code units.
    units = text[line - 1].encode("utf-16-le")
    if (column - 1) * 2 > len(units):
        return "second_signal_mismatch"
    head = units[: (column - 1) * 2].decode("utf-16-le", errors="replace")
    if _JS_HEAD_RE.fullmatch(head):
        return (symbol, False)
    return _js_fold(line, callables, text, "second_signal_mismatch")


def _js_fold(
    line: int,
    callables: Sequence[IndexedSymbol],
    text: list[str],
    no_enclosing: SkipReason,
) -> _Hit | SkipReason:
    """The indexed function whose body holds *line*, for a function folded into it."""
    enclosing = symbol_at(callables, line)
    if enclosing is None:
        return no_enclosing
    if _in_body(line, enclosing, text):
        return (enclosing, True)
    return "second_signal_mismatch"


def _in_body(line: int, symbol: IndexedSymbol, text: list[str]) -> bool:
    """Whether 1-based *line* is a body line of *symbol*, short of its last line.

    A function starting on the signature lines may be a default parameter,
    and one starting on the last line may follow the symbol's closing brace.
    """
    if symbol.end_line > len(text):
        return False
    first, last = body_span(text, symbol.start_line - 1, symbol.end_line - 1)
    return first + 1 <= line <= min(last + 1, symbol.end_line - 1)


def _read_once(
    read_lines: Callable[[str], Sequence[str] | None],
) -> Callable[[str], list[str] | None]:
    """*read_lines* with each file read at most once, as a list."""
    cache: dict[str, list[str] | None] = {}

    def read(rel: str) -> list[str] | None:
        if rel not in cache:
            text = read_lines(rel)
            cache[rel] = list(text) if text is not None else None
        return cache[rel]

    return read


class _PathMap:
    """Frame paths to repo-relative ones, under one checkout root only.

    A Windows root compares without case, and the relative part then takes
    the index's spelling when exactly one indexed file matches it.
    """

    def __init__(
        self,
        root: str,
        symbols_by_file: Mapping[str, Sequence[IndexedSymbol]],
        dirty_paths: Collection[str],
    ) -> None:
        self._root = posixpath.normpath(root.replace("\\", "/"))
        # Windows paths (a drive, "C:" alone, or a UNC share) compare without case.
        self._fold = bool(re.match(r"[A-Za-z]:(?:/|$)", self._root)) or self._root.startswith("//")
        self._files = symbols_by_file
        self._dirty = {self._key(posixpath.normpath(p.replace("\\", "/"))) for p in dirty_paths}
        self._folded: dict[str, str | None] = {}
        if self._fold:
            for path in symbols_by_file:
                key = path.casefold()
                self._folded[key] = None if key in self._folded else path

    def _key(self, rel: str) -> str:
        return rel.casefold() if self._fold else rel

    def relative(self, raw: str) -> str | None:
        path = raw.replace("\\", "/")
        if path[:7].lower() == "file://":
            path = unquote(path[len("file://") :])
            if _DRIVE_RE.match(path[1:]):
                path = path[1:]  # file:///C:/x -> C:/x
        if not (path.startswith("/") or _DRIVE_RE.match(path)):
            return None  # relative: under no root in particular
        path = posixpath.normpath(path)
        prefix = self._root if self._root.endswith("/") else self._root + "/"
        if not self._key(path).startswith(self._key(prefix)):
            return None
        return path[len(prefix) :] or None

    def indexed(self, rel: str) -> str | None:
        if rel in self._files:
            return rel
        return self._folded.get(rel.casefold()) if self._fold else None

    def is_dirty(self, rel: str) -> bool:
        return self._key(posixpath.normpath(rel.replace("\\", "/"))) in self._dirty


__all__ = [
    "JVM_SOURCE_SUFFIXES",
    "FrameLanguage",
    "FrameResolution",
    "JvmFrameName",
    "ProfileFrame",
    "ResolvedFrame",
    "SkipReason",
    "checkout_reader",
    "dirty_path_checker",
    "frame_paths",
    "load_frame_symbols",
    "load_jvm_source_paths",
    "parse_jvm_frame",
    "place_jvm_frames",
    "resolve_frames",
]
