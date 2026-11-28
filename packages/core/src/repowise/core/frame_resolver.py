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

FrameLanguage = Literal["go", "python", "javascript", "rust", "java", "kotlin", "pascal", "csharp"]

#: JVM frames: no file of their own, placed by class name (:func:`place_jvm_frames`).
_JVM = frozenset({"java", "kotlin"})
#: Frames that name a type and a method, not a line: a JVM frame, a .NET one (:func:`place_clr_frames`).
_BY_NAME = frozenset({*_JVM, "csharp"})
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
    "no_source",  # JavaScript and Pascal: the source needed to check the frame could not be read
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

    *line* is 1-based: the executing line for Go, Python, Pascal and the JVM,
    the function's start line for JavaScript (V8 records no executing line); 0
    when the profiler wrote none. *column* is the 1-based column V8 gives for
    a function's start. *start_line* is the function's start line where the
    format records it (Go ``s=``). *function* is the name the profiler gave:
    Python's ``co_qualname`` (or ``co_name``), Go's function name, V8's
    ``functionName``, Pascal's DWARF name, a JVM ``class.method`` with its
    parameter types or descriptor when the profiler wrote them
    (:func:`parse_jvm_frame`), a .NET ``Module!Namespace.Type.Method(params)``
    (:func:`parse_clr_frame`), which carries no line.
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
    checks, Python decorator lines and Pascal nested routines; without it those
    frames are skipped.

    Second signals, after the span lookup: Go needs the index start line to
    equal the frame's ``start_line``; Python needs the frame's qualified name
    to be a dotted tail of the symbol's; JavaScript joins by position only:
    exactly one indexed function starts on the frame's line, and nothing but
    names, keywords, ``=`` and ``:`` stands before the frame's column, so it is
    the first function the line can start; Pascal needs the frame's name, in any
    case, to be a dotted tail of the symbol's. A JVM frame (Java, Kotlin) needs
    the method name and class to agree with the innermost method at its line;
    with no line it joins by name (:func:`_name_join`): the one method of that
    name in that class, else the one whose parameter count (and types) match
    the frame's, else ``ambiguous``. A C# frame (``dotnet-trace``) has no line
    and always joins by name, the same way, its CLR parameter types read as
    C# spells them (``int32`` as ``int``, ``System.String`` as ``string``).
    Rust needs the demangled name
    (``crate::module::Type::method``, ``<Type as Trait>::method``; hash and
    generic arguments cut) to agree with the innermost function at its line,
    or with the function an attribute on its line (``#[instrument]``) sits on;
    a Rust frame with no line joins by name only when the file defines one
    ``fn`` of that name. A frame is never put on one overload of several by
    name alone.

    Folding, when the frame's own function has no symbol: a Go closure
    (``Outer.func1``) whose start line lies inside ``Outer``; a Python frame
    whose qualified name runs through ``<locals>`` or ends in ``<lambda>``,
    ``<genexpr>`` or a comprehension, charged to the symbol its enclosing name
    matches; a JavaScript function that starts on a body line of an indexed
    function; a Rust closure or async body (``{{closure}}``, ``{closure#0}``,
    ``{async_block#0}``) whose enclosing name matches, or one outside any
    function into the file; a Pascal nested routine declared inside the symbol
    above the frame's line. Module bodies (Python ``<module>``, the Go package
    initializer, the CommonJS wrapper at 1:1, a JVM static initializer, a
    Pascal program or unit block) resolve to the file, and a Python frame on a
    decorator line resolves to the function it decorates. A JVM lambda
    (``lambda$name$0``, Kotlin ``name$lambda$0``) and a method of an anonymous class (``Outer$1``) fold
    into the method at their line; compiler-made accessors and bridges
    (``access$000``, ``$default``, ``$$Lambda``, ``$$`` proxies) are
    ``generated``. A C# lambda (``<Run>b__2_0``), local function
    (``<Run>g__Local|2_0``) or iterator or async state machine
    (``<Run>d__3.MoveNext``) folds into the method it is written in, by name;
    a lambda in a static field initializer, into the file.
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
    if frame.language not in ("go", "python", "javascript", "rust", "pascal", *_BY_NAME):
        return "unsupported_language"
    # A JVM or .NET frame without a line still has its type, method and parameters;
    # a Rust one its demangled path (:func:`_rust_by_name`).
    if frame.line < 1 and frame.language not in (*_BY_NAME, "rust"):
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
    elif frame.language == "csharp":
        outcome = _csharp(frame, symbols)
    elif frame.language == "rust":
        outcome = _rust(frame, symbols, lines)
    elif frame.language == "pascal":
        outcome = _pascal(frame, symbols, lines)
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


# -- Rust ----------------------------------------------------------------------

# rustc's legacy mangling ends a path in ``::h`` and 16 hex digits; v0 has no hash.
_RUST_HASH_RE = re.compile(r"::h[0-9a-f]{16}$")
# A closure, async block or async fn body: legacy ``{{closure}}``, v0 ``{closure#0}``.
_RUST_ANONYMOUS_RE = re.compile(r"::\{\{?(?:closure|async_block|async_fn|async_gen)(?:#\d+)?\}\}?$")
_RUST_QSELF_RE = re.compile(r"\s+as\s+")


def _rust_path(name: str) -> tuple[list[str], list[str], bool]:
    """A demangled Rust frame as ``(path segments, owner names, folded)``.

    The hash and any closure or async suffixes are cut (``folded`` when there
    were any), and generic arguments dropped. A qualified self
    (``<T as Trait>::m``, ``<T>::m``) gives the segments of ``T`` then ``m``,
    and owners ``T`` and ``Trait``: rust-analyzer-style indexes put a trait
    impl's method under the type, a provided method under the trait.
    """
    name = _RUST_HASH_RE.sub("", name)
    folded = False
    while (m := _RUST_ANONYMOUS_RE.search(name)) is not None:
        name, folded = name[: m.start()], True
    owners: list[str] = []
    if name.startswith("<"):
        close = _rust_matching_angle(name)
        if close < 0:
            return [], [], folded
        self_type, *trait = _rust_split_as(name[1:close])
        name = self_type + name[close + 1 :]
        owners = [p for p in (_rust_last(self_type), *map(_rust_last, trait)) if p]
    segments = [s for s in _rust_drop_generics(name).split("::") if s]
    if not owners and len(segments) >= 2:
        owners = [segments[-2]]
    return segments, owners, folded


def _rust_angle_steps(text: str) -> list[int]:
    """Angle-bracket depth after each character; the ``>`` of ``->`` does not close."""
    depth, steps = 0, []
    for i, ch in enumerate(text):
        depth += (ch == "<") - (ch == ">" and text[i - 1 : i] != "-")
        steps.append(depth)
    return steps


def _rust_split_as(qself: str) -> list[str]:
    """``T as Trait`` split at its own ``as``, not one inside ``T``'s generic arguments."""
    steps = _rust_angle_steps(qself)
    for m in _RUST_QSELF_RE.finditer(qself):
        if steps[m.start()] == 0:
            return [qself[: m.start()], qself[m.end() :]]
    return [qself]


def _rust_matching_angle(text: str) -> int:
    """The index of the ``>`` closing the ``<`` *text* opens with, or -1."""
    return next((i for i, depth in enumerate(_rust_angle_steps(text)) if depth == 0), -1)


def _rust_drop_generics(text: str) -> str:
    steps = _rust_angle_steps(text)
    return "".join(
        ch for i, ch in enumerate(text) if steps[i] == 0 and (i == 0 or steps[i - 1] == 0)
    )


def _rust_last(path: str) -> str:
    """The type a self type or trait path names: ``&mut dyn a::Tr<T>`` -> ``Tr``."""
    name = _rust_drop_generics(path).strip().lstrip("&").removeprefix("mut ").removeprefix("dyn ")
    return name.rsplit("::", 1)[-1].strip()


def _rust_agrees(symbol: IndexedSymbol, segments: list[str], owners: list[str]) -> bool:
    """Whether the frame's name is *symbol*'s: its name, and for a method its type or trait."""
    if not segments or segments[-1] != symbol.name:
        return False
    if symbol.kind != "method" or len(segments) == 1:
        return True
    parts = symbol.qualified_name.split(".")
    return len(parts) >= 2 and parts[-2] in owners


def _rust_attributed(
    line: int, symbols: Sequence[IndexedSymbol], lines: Callable[[], list[str] | None]
) -> IndexedSymbol | None:
    """The function an attribute on *line* applies to, or None.

    A Rust span starts at ``fn``, below its attributes; code an attribute
    macro writes (``#[instrument]``, ``#[tokio::main]``) runs on the
    attribute's line. The line must read ``#[``, and only attributes,
    comments or blank lines may stand between it and the nearest function
    opening below. Read from the source, not the index: an index that lost a
    function (two ``impl From<_> for T`` share one id) must not hand its
    lines to the next one. The caller still checks the name.
    """
    below = [s for s in symbols if s.start_line > line and s.kind in _CALLABLE_KINDS]
    text = lines() if below else None
    if not below or text is None or line > len(text) or not text[line - 1].lstrip().startswith("#["):
        return None
    nearest = symbol_at(below, min(s.start_line for s in below))
    if nearest is None or nearest.start_line > len(text) + 1:
        return None
    between = (t.strip() for t in text[line : nearest.start_line - 1])
    if any(_RUST_ITEM_RE.match(t) or (t and not t.startswith(("#[", "//", ")", "]")) and not _RUST_ATTR_ARGS_RE.match(t)) for t in between):
        return None
    return nearest


# A continuation line of a multi-line attribute's arguments: no item or block.
_RUST_ATTR_ARGS_RE = re.compile(r"^[^{};]*$")
# A line that opens an item: never part of an attribute.
_RUST_ITEM_RE = re.compile(r"^(?:pub\b|fn\b|struct\b|enum\b|impl\b|trait\b|mod\b|use\b|const\b|static\b|type\b|unsafe\b|async\b|extern\b|macro_rules!)")
_RUST_FN_RE = r"\bfn\s+{}\b"


def _rust_by_name(
    segments: list[str],
    owners: list[str],
    callables: Sequence[IndexedSymbol],
    lines: Callable[[], list[str] | None],
) -> IndexedSymbol | None:
    """The one function a frame with no line names, or None.

    pprof-rs writes line 0 for some inlined code. The frame joins only when
    one indexed function of the file agrees with its name and the source
    defines one ``fn`` of that name: an index that folded two
    ``impl From<_> for T`` into one id never decides which ran.
    """
    found = [s for s in callables if _rust_agrees(s, segments, owners)]
    if len(found) != 1 or (text := lines()) is None:
        return None
    pattern = re.compile(_RUST_FN_RE.format(re.escape(segments[-1])))
    return found[0] if sum(len(pattern.findall(t)) for t in text) == 1 else None


def _rust(
    frame: ProfileFrame, symbols: Sequence[IndexedSymbol], lines: Callable[[], list[str] | None]
) -> _Hit | SkipReason:
    """A Rust frame by line and demangled name (pprof-rs writes no start line).

    The innermost function or method at the line, or the function an
    attribute on the line belongs to, must carry the frame's name (and for a
    method its type or trait). A closure or async body folds into the function
    around it; one outside any function folds into the file. A frame that names
    no indexed function is never put on the file: derive and macro output on a
    type's line is ``no_span``. A frame with no line joins by name only
    (:func:`_rust_by_name`), else stays ``bad_line``.
    """
    if not frame.function:
        return "no_second_signal" if frame.line >= 1 else "bad_line"
    segments, owners, folded = _rust_path(frame.function)
    callables = [s for s in symbols if s.kind in _CALLABLE_KINDS]
    if frame.line < 1:
        named = _rust_by_name(segments, owners, callables, lines)
        return (named, folded) if named is not None else "bad_line"
    at_line = symbol_at(callables, frame.line)
    if at_line is not None and _rust_agrees(at_line, segments, owners):
        return (at_line, folded)
    # An attribute on a fn nested in another lies on the outer fn's lines.
    attributed = _rust_attributed(frame.line, symbols, lines)
    if attributed is not None and _rust_agrees(attributed, segments, owners):
        return (attributed, folded)
    if at_line is None and attributed is None:
        return (None, True) if folded else "no_span"
    return "second_signal_mismatch"


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
            placed[key] = _class_file(name.package, outer, by_name)
        rel = placed[key]
        if rel is None:
            out.append(frame)
            continue
        language: FrameLanguage = "kotlin" if rel.endswith(".kt") else "java"
        out.append(replace(frame, path=f"{base}/{rel}", language=language, line=frame.line or name.line))
    return tuple(out)


def _class_file(package: tuple[str, ...], outer: str, by_name: Mapping[str, Sequence[str]]) -> str | None:
    """The one maintained indexed file of class *outer* in *package*, or None."""
    stems = [outer, outer[:-2]] if outer.endswith("Kt") and len(outer) > 2 else [outer]
    tails = ["/".join((*package, stem + ext)) for stem in stems for ext in JVM_SOURCE_SUFFIXES]
    found = {
        rel
        for tail in tails
        for rel in by_name.get(tail.rsplit("/", 1)[-1], ())
        if (rel == tail or rel.endswith("/" + tail))
        and (package or _at_source_root(rel))
        and path_origin(rel) not in ("vendored", "generated")
    }
    return found.pop() if len(found) == 1 else None


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


@dataclass(frozen=True)
class _JvmCall:
    """What a JVM frame claims ran, in the index's terms."""

    name: JvmFrameName
    #: The class chain, local classes (``1Local``) named as the index names them.
    segments: tuple[str, ...]
    #: ``segments`` without anonymous classes (``1``, ``2``).
    named: tuple[str, ...]
    #: The id parent segment the method must sit under (``""``: a Kotlin top-level function).
    owner: str
    #: The method name to find: the class name for a constructor.
    wanted: str
    #: ``name`` in ``lambda$name$0`` (``static``, ``new`` included), or None.
    enclosing: str | None
    #: Whether the compiled parameter count is the source's (see :func:`_name_join`).
    by_count: bool

    @property
    def anonymous(self) -> bool:
        return self.segments[-1].isdigit()

    @property
    def init(self) -> bool:
        return self.name.method == "<init>"


def _jvm(frame: ProfileFrame, symbols: Sequence[IndexedSymbol], *, kotlin: bool) -> _Hit | SkipReason:
    name = parse_jvm_frame(frame.function)
    if name is None:
        return "no_second_signal"
    if _jvm_synthetic(name):
        # Compiler-made trampolines with no source of their own: the caller keeps the time.
        return "generated"
    if name.method == "<clinit>":
        return (None, False)
    call = _jvm_call(name, symbols, kotlin=kotlin)
    callables = [s for s in symbols if s.kind in _CALLABLE_KINDS]
    if frame.line < 1:
        if call.enclosing is not None or call.anonymous:
            return "no_second_signal"  # only a line tells which lambda or anonymous class
        return _name_join(callables, owner=call.owner, name=call.wanted, params=name.params, by_count=call.by_count)
    symbol = symbol_at(callables, frame.line)
    if symbol is None:
        return _jvm_off_method(call, frame.line, symbols, callables)
    if call.enclosing is None and not call.anonymous:
        return _jvm_method(call, symbol, callables)
    return _jvm_nested(call, symbol)


def _jvm_method(call: _JvmCall, symbol: IndexedSymbol, callables: Sequence[IndexedSymbol]) -> _Hit | SkipReason:
    """A named method's frame at a line *symbol* holds."""
    if symbol.name != call.wanted or _id_parent(symbol) != call.owner:
        return "second_signal_mismatch"
    # The line found the method; the frame's parameters, where it has them, must
    # agree, or a line that drifted onto a neighbouring overload would join it.
    siblings = [s for s in callables if s is not symbol and s.name == symbol.name and _id_parent(s) == call.owner]
    agree = _params_agree(symbol, call.name.params, call.by_count, siblings)
    return (symbol, False) if agree else "second_signal_mismatch"


def _jvm_synthetic(name: JvmFrameName) -> bool:
    """``access$000``, ``foo$default``, ``$values``, ``Outer$$Lambda``, a CGLIB proxy, and
    javac's access constructor ``Main(Main$1)`` (Java 10 and older), whose parameter is a marker class."""
    method = name.method
    return (
        method.startswith("access$")
        or method.endswith("$default")
        or method == "$values"
        or "" in name.cls.split("$")
        or any(p.isdigit() for p in name.params or ())
    )


def _jvm_call(name: JvmFrameName, symbols: Sequence[IndexedSymbol], *, kotlin: bool) -> _JvmCall:
    # A local class is ``Outer$1Local``: the index names it ``Local``.
    segments = tuple(s if s.isdigit() else s.lstrip("0123456789") for s in name.cls.split("$"))
    named = tuple(s for s in segments if not s.isdigit())
    lam = _JVM_LAMBDA_RE.fullmatch(name.method)
    owner = segments[-1]
    if kotlin and len(segments) == 1 and owner.endswith("Kt"):
        owner = ""  # a top-level function: the index nests it in no class
    init = name.method == "<init>"
    wanted = named[-1] if init and named else name.method
    # A compiled parameter count is the source's for a Java method, and for a
    # top-level class's constructor; not for a nested class's (its outer
    # instance), an enum's (name and ordinal) or Kotlin's (an extension's receiver).
    top_class = len(segments) == 1 and any(s.name == wanted and s.kind == "class" for s in symbols)
    return _JvmCall(
        name=name,
        segments=segments,
        named=named,
        owner=owner,
        wanted=wanted,
        enclosing=(lam["a"] or lam["b"]) if lam else None,
        by_count=not kotlin and (not init or top_class),
    )


def _jvm_off_method(
    call: _JvmCall, line: int, symbols: Sequence[IndexedSymbol], callables: Sequence[IndexedSymbol]
) -> _Hit | SkipReason:
    """A JVM frame whose line no indexed method holds."""
    if call.enclosing == "static" or (call.anonymous and call.init):
        # A lambda or anonymous class in a static field: load-time code, the file's.
        return (None, True)
    if call.init:
        # A field initializer runs in the constructor, on the field's line.
        return _name_join(
            callables, owner=call.owner, name=call.wanted, params=call.name.params, by_count=call.by_count
        )
    if call.enclosing == "new":
        # A lambda in an instance field initializer folds into the constructor, when there is one.
        hit = _name_join(callables, owner=call.owner, name=call.named[-1], params=None, by_count=False)
        return (hit[0], True) if isinstance(hit, tuple) else hit
    if call.enclosing is None and (
        call.anonymous or any(s.start_line == line and s.name == call.named[-1] for s in symbols)
    ):
        # javac puts a bridge method (a generic override's erased twin) on its class's line.
        return "generated"
    return "no_span"


def _jvm_nested(call: _JvmCall, symbol: IndexedSymbol) -> _Hit | SkipReason:
    """A lambda or an anonymous class's member, at a line *symbol* holds."""
    if call.enclosing is not None:
        parent = call.named[-1] if call.enclosing == "new" else call.enclosing
        if symbol.name == parent and _jvm_in_class(call.named, symbol):
            return (symbol, True)
        return "second_signal_mismatch"
    if not _jvm_in_class(call.named, symbol):
        return "second_signal_mismatch"
    # The index holds an anonymous class's methods under the method or field that
    # declares it (``create::TypeAdapter::read``). Its constructor runs where it is
    # declared, and a Kotlin lambda class (``Foo$bar$1``) is named after its
    # function: both fold into the method at the line.
    if not call.init and symbol.name == call.name.method:
        return (symbol, False)
    if call.init or (len(call.segments) > 1 and call.segments[-2] == symbol.name):
        return (symbol, True)
    # On the declaring line: a bridge javac wrote, called through the interface.
    return "generated"


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
    from repowise.core.ingestion.symbol_identity import local_symbol_id

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
    left = _overloads(found, params, by_count)
    if not left:
        return "no_span"
    return (left[0], False) if len(left) == 1 else "ambiguous"


def _overloads(
    found: Sequence[IndexedSymbol], params: Sequence[str], by_count: bool
) -> list[IndexedSymbol]:
    """The overloads in *found* that fit *params*: by id count, then by the types the id spells.

    Empty when none has the frame's count; several when nothing tells them apart.
    """
    from repowise.core.ingestion.symbol_identity import overload_signature

    payloads = {s.symbol_id: overload_signature(s.symbol_id) or "" for s in found}
    # Narrow only when every member carries its count: an id without one is not an overload.
    if by_count and all(payloads[s.symbol_id][:1].isdigit() for s in found):
        found = [s for s in found if payloads[s.symbol_id].partition("(")[0] == str(len(params))]
        if len(found) <= 1:
            return list(found)
    typed = [s for s in found if _same_types(_id_types(s), params)]
    return typed if len(typed) == 1 else list(found)


def _params_agree(
    symbol: IndexedSymbol,
    params: Sequence[str] | None,
    by_count: bool,
    siblings: Sequence[IndexedSymbol] = (),
) -> bool:
    """Whether the frame's parameters fit the count and types *symbol*'s id spells.

    Where a compiled count is the source's (*by_count*), the count and any
    spelled types must equal the frame's. Where it is not (Kotlin, a nested
    class's constructor), types the compiler may spell otherwise (``T`` as
    ``Object``) cannot refute a join on their own: the frame disagrees only
    when its types differ from *symbol*'s and equal a sibling overload's.
    An id with no count says nothing, and a frame with no parameters is not checked.
    """
    from repowise.core.ingestion.symbol_identity import overload_signature

    payload = overload_signature(symbol.symbol_id)
    if params is None or not payload or not payload[:1].isdigit():
        return True
    if not by_count:
        frame = tuple(params)
        return _id_types(symbol) in (None, frame) or not any(_id_types(s) == frame for s in siblings)
    count, paren, _ = payload.partition("(")
    if int(count) != len(params):
        return False
    return not paren or _same_types(_id_types(symbol), params)


#: Kotlin's names for the JVM types a frame spells (``Int`` is ``int``).
_KOTLIN_JVM_TYPES = {
    "Int": "int", "Long": "long", "Short": "short", "Byte": "byte", "Char": "char",
    "Boolean": "boolean", "Float": "float", "Double": "double", "Any": "Object",
    "IntArray": "int[]", "LongArray": "long[]", "ByteArray": "byte[]", "CharArray": "char[]",
    "BooleanArray": "boolean[]", "DoubleArray": "double[]", "FloatArray": "float[]", "ShortArray": "short[]",
}


def _id_types(symbol: IndexedSymbol) -> tuple[str, ...] | None:
    """The parameter types *symbol*'s id spells, as a JVM frame spells them; None when it spells none."""
    from repowise.core.ingestion.symbol_identity import overload_signature

    payload = overload_signature(symbol.symbol_id) or ""
    _, paren, types = payload.partition("(")
    if not paren:
        return None
    spelled = [t for t in types.removesuffix(")").split(",") if t]
    if symbol.file_path.endswith(".kt"):
        spelled = [_KOTLIN_JVM_TYPES.get(t.rstrip("?"), t.rstrip("?")) for t in spelled]
    elif symbol.file_path.endswith(".cs"):
        spelled = [_csharp_keyword(t) for t in spelled]
    return tuple(spelled)


def _same_types(spelled: tuple[str, ...] | None, params: Sequence[str]) -> bool:
    """Whether an id's parameter types are the frame's.

    A C# id spells a generic type's arity (``Func`2``) only where two
    overloads would otherwise share an id; elsewhere ``Func`` stands for any
    arity, and a frame's ``Func`2`` matches it.
    """
    if spelled is None or len(spelled) != len(params):
        return False
    return all(
        s == p or ("`" in p and "`" not in s and s == _CLR_ARITY_RE.sub("", p))
        for s, p in zip(spelled, params, strict=True)
    )


# -- .NET (C#) -------------------------------------------------------------------

# A managed frame as dotnet-trace (TraceEvent) names it:
# ``Calc!Shop.Billing.Cart+Line.Price()``, ``Calc!Shop.Storage.Bag`1[System.Int32]..ctor()``,
# ``Calc!Shop.Billing.Cart.Total(class System.String)``. The module before ``!``
# keeps the names of other runtimes out; the parameters are always written.
_CLR_FRAME_RE = re.compile(r"(?P<module>[^!\s()]+)!(?P<name>[^()!]+?)\((?P<params>[^()]*)\)")
_CLR_ARITY_RE = re.compile(r"`\d+")
# A generic instantiation, innermost first: ``Bag`1[System.Int32]``, ``Map`2[System.__Canon,System.Int32]``.
_CLR_INSTANTIATION_RE = re.compile(r"\[[^\[\]]*\]")
# Compiler-made members: a lambda ``<Sum>b__2_0``, a local function ``<Fill>g__Weigh|1_0``.
_CLR_NESTED_METHOD_RE = re.compile(r"<(?P<enclosing>.+)>[a-z]__.*")
# A compiler-made type: an iterator or async state machine ``<Run>d__3`` (an async
# lambda's is ``<<Main>b__0_0>d``), a closure class ``<>c`` or ``<>c__DisplayClass2_0``.
_CLR_STATE_MACHINE_RE = re.compile(r"<(?P<enclosing>.+)>d(?:__\d+)?")
# The innermost ``<name>`` is the source method: a lambda inside a lambda is
# ``<<Main>b__0_0>b__0_1``, and both were written in ``Main``.
_CLR_INNERMOST_RE = re.compile(r"<(?P<enclosing>[^<>]+)>[a-z]__")


def _clr_enclosing(name: str, fallback: str) -> str:
    """The source method a compiler-made *name* was written in, else *fallback*."""
    inner = _CLR_INNERMOST_RE.search(name)
    return inner["enclosing"] if inner else fallback


_CLR_PRIMITIVES = {
    "void": "void", "bool": "bool", "char": "char", "string": "string", "object": "object",
    "int8": "sbyte", "uint8": "byte", "unsigned int8": "byte",
    "int16": "short", "uint16": "ushort", "unsigned int16": "ushort",
    "int32": "int", "uint32": "uint", "unsigned int32": "uint",
    "int64": "long", "uint64": "ulong", "unsigned int64": "ulong",
    "float32": "float", "float64": "double", "native int": "nint", "native unsigned int": "nuint",
}
# The C# keyword for a BCL type, as a source parameter spells it more often than not.
_CSHARP_KEYWORDS = {
    "String": "string", "Object": "object", "Boolean": "bool", "Char": "char",
    "SByte": "sbyte", "Byte": "byte", "Int16": "short", "UInt16": "ushort",
    "Int32": "int", "UInt32": "uint", "Int64": "long", "UInt64": "ulong",
    "Single": "float", "Double": "double", "Decimal": "decimal",
    "IntPtr": "nint", "UIntPtr": "nuint", "Void": "void",
}
_CLR_TYPE_KINDS = frozenset({"class", "struct", "interface", "enum"})


@dataclass(frozen=True)
class ClrFrameName:
    """A .NET frame's name, split: ``Shop.Billing`` / ``("Cart", "Line")`` / ``Price`` / ``()``."""

    namespace: str
    #: The declared types, outermost first, without generic arity or compiler-made types.
    types: tuple[str, ...]
    #: The method as the runtime names it: ``Total``, ``.ctor``, ``<Sum>b__2_0``, ``MoveNext``.
    method: str
    #: C# simple parameter types (``int``, ``string``, ``List`1``, ``int[]``); generic
    #: parameters stay ``!0``/``!!0``, which no source spelling equals.
    params: tuple[str, ...]
    #: The method a lambda, local function or state machine was written in, or None.
    enclosing: str | None = None
    #: The method's owner is compiler-made: a closure class ``<>c__DisplayClass2_0`` or ``<>c``.
    compiler_owner: bool = False

    @property
    def member_known(self) -> bool:
        """Whether a source member can be named for this frame.

        A static initializer (``.cctor``, or a lambda written in one) is no
        member, and neither is the constructor of a compiler-made closure
        class: it runs inside some method's body, and its name does not say which.
        """
        if ".cctor" in (self.method, self.enclosing):
            return False
        return not (self.compiler_owner and self.enclosing is None and self.method == ".ctor")

    @property
    def type_name(self) -> str:
        """The type's full name as placement keys it: ``Shop.Billing.Cart+Line``."""
        nested = "+".join(self.types)
        return f"{self.namespace}.{nested}" if self.namespace else nested

    @property
    def wanted(self) -> str:
        """The source method this frame ran in: a constructor is named after its type."""
        method = self.enclosing or self.method
        return self.types[-1] if method in (".ctor", ".cctor") else method


def parse_clr_frame(text: str) -> ClrFrameName | None:
    """*text* as a .NET frame name, or None when it is not one."""
    m = _CLR_FRAME_RE.fullmatch(text.strip())
    if m is None:
        return None
    name = m["name"]
    while _CLR_INSTANTIATION_RE.search(name):
        name = _CLR_INSTANTIATION_RE.sub("", name)
    for ctor in ("..cctor", "..ctor"):
        if name.endswith(ctor):
            owner, method = name[: -len(ctor)], ctor[1:]
            break
    else:
        cut = _clr_last_dot(name)
        if cut < 1:
            return None
        owner, method = name[:cut], name[cut + 1 :]
    segments = owner.split("+")
    namespace, _, outer = segments[0].rpartition(".")
    types: list[str] = []
    enclosing = None
    for segment in (outer, *segments[1:]):
        if segment.startswith("<"):
            machine = _CLR_STATE_MACHINE_RE.fullmatch(segment)
            enclosing = _clr_enclosing(segment, machine["enclosing"]) if machine else enclosing
        else:
            types.append(_CLR_ARITY_RE.sub("", segment))
    nested = _CLR_NESTED_METHOD_RE.fullmatch(method)
    if nested is not None:
        enclosing = _clr_enclosing(method, nested["enclosing"])
    if not types or not method:
        return None
    params = tuple(_clr_param(p) for p in _clr_split_params(m["params"]))
    return ClrFrameName(
        namespace, tuple(types), _CLR_ARITY_RE.sub("", method), params, enclosing,
        compiler_owner=segments[-1].startswith("<"),
    )


def _clr_last_dot(name: str) -> int:
    """The ``.`` before the method: the last one outside ``<...>`` (``<.cctor>b__0_0`` holds one)."""
    depth, cut = 0, -1
    for i, ch in enumerate(name):
        if ch == "<":
            depth += 1
        elif ch == ">" and depth:
            depth -= 1
        elif ch == "." and depth == 0:
            cut = i
    return cut


def _clr_split_params(text: str) -> list[str]:
    """Parameters split on the commas outside ``<...>`` and ``[...]``."""
    out: list[str] = []
    depth, start = 0, 0
    for i, ch in enumerate(text):
        if ch in "<[":
            depth += 1
        elif ch in ">]":
            depth -= 1
        elif ch == "," and depth == 0:
            out.append(text[start:i])
            start = i + 1
    out.append(text[start:])
    return [p.strip() for p in out if p.strip()]


def _clr_param(text: str) -> str:
    """``class System.String[]`` -> ``string[]``, ``valuetype System.Nullable`1<int32>`` -> ``int?``."""
    text = re.sub(r"^(?:class|valuetype)\s+", "", text.strip()).rstrip("&").strip()
    marks = ""
    while (m := re.search(r"(?:\[[^\[\]]*\]|\*)$", text)) is not None:
        marks = ("*" if m[0] == "*" else "[]") + marks
        text = text[: m.start()].rstrip()
    args = ""
    if text.endswith(">") and "<" in text:
        text, _, args = text.partition("<")
    if text in _CLR_PRIMITIVES:
        return _CLR_PRIMITIVES[text] + marks
    simple = text.rsplit(".", 1)[-1].rsplit("+", 1)[-1]
    if simple == "Nullable`1" and args:
        return _clr_param(args[:-1]) + "?" + marks
    return _csharp_keyword(simple) + marks


def _csharp_keyword(spelled: str) -> str:
    """``Int32[]`` -> ``int[]``: a BCL type a C# id or frame spells, as its keyword."""
    base = spelled.rstrip("[]*?")
    return _CSHARP_KEYWORDS.get(base, base) + spelled[len(base) :]


def place_clr_frames(
    frames: Sequence[ProfileFrame], *, root: str, symbols_by_file: Mapping[str, Sequence[IndexedSymbol]]
) -> tuple[ProfileFrame, ...]:
    """*frames* with each .NET frame put on the indexed ``.cs`` file that declares its type.

    A ``dotnet-trace`` export names types, not files, and a C# namespace need
    not follow the directories, so the type's file is found from the index:
    *symbols_by_file* holds each ``.cs`` file's namespaces, types and
    callables (:func:`load_clr_symbols`). A type's full name is its enclosing
    namespaces (block or file-scoped) and types, by span: ``Shop.Billing.Cart+Line``.
    A partial type declared in several files goes to the one that declares
    the frame's method; with none or several, or no such type (the
    top-level statements' ``Program``), the frame keeps its path and stays
    outside the checkout.
    """
    by_type = _clr_files_by_type(symbols_by_file)
    base = posixpath.normpath(root.replace("\\", "/")).rstrip("/")
    out: list[ProfileFrame] = []
    for frame in frames:
        name = parse_clr_frame(frame.function) if frame.language == "csharp" else None
        if name is None or "/" in frame.path.replace("\\", "/"):
            out.append(frame)
            continue
        file = _clr_type_file(name, by_type.get(name.type_name, []), symbols_by_file)
        out.append(frame if file is None else replace(frame, path=f"{base}/{file}"))
    return tuple(out)


def _clr_files_by_type(symbols_by_file: Mapping[str, Sequence[IndexedSymbol]]) -> dict[str, list[str]]:
    """Each type's full name to the checkout ``.cs`` files that declare it."""
    by_type: dict[str, list[str]] = {}
    for rel, symbols in symbols_by_file.items():
        if rel.endswith(".cs") and path_origin(rel) not in ("vendored", "generated"):
            for full in _clr_type_names(symbols):
                by_type.setdefault(full, []).append(rel)
    return by_type


def _clr_type_file(
    name: ClrFrameName, files: list[str], symbols_by_file: Mapping[str, Sequence[IndexedSymbol]]
) -> str | None:
    """The one file of *files* the frame belongs on: a partial type's goes by its method."""
    if len(files) > 1 and not name.member_known:
        # No member to look for: any file of a partial type would be a guess.
        return None
    if len(files) > 1:
        files = [
            rel for rel in files
            if any(s.kind in _CALLABLE_KINDS and s.name == name.wanted and _id_parent(s) == name.types[-1]
                   for s in symbols_by_file[rel])
        ]
    return files[0] if len(files) == 1 else None


def _clr_type_names(symbols: Sequence[IndexedSymbol]) -> list[str]:
    """The full names of the types *symbols* declare: ``Shop.Billing.Cart``, ``Shop.Billing.Cart+Line``."""
    namespaces = [s for s in symbols if s.kind == "module"]
    types = [s for s in symbols if s.kind in _CLR_TYPE_KINDS]
    out = []
    for t in types:
        # A block namespace holds the type; a file-scoped one (``namespace X;``) ends before it.
        spaces = [
            m for m in namespaces
            if (m.start_line <= t.start_line and t.end_line <= m.end_line)
            or (m.start_line == m.end_line and m.end_line < t.start_line)
        ]
        spaces.sort(key=lambda s: (s.start_line, -s.end_line))
        namespace = ".".join(m.name for m in spaces)
        nested = "+".join([*_clr_outer_types(t, types), t.name])
        out.append(f"{namespace}.{nested}" if namespace else nested)
    return out


def _clr_outer_types(t: IndexedSymbol, types: Sequence[IndexedSymbol]) -> list[str]:
    """The names of the types *t* is nested in, outermost first.

    Spans alone cannot tell ``class A { class B { } }`` from two sibling
    types on one line, so each step must also be the parent the index
    records in the qualified name (``<module>.A.B``: ``A`` holds ``B``),
    which starts with the file's dotted path (``src/Cart.cs``: ``src.Cart``).
    """
    module = posixpath.splitext(t.file_path.replace("\\", "/"))[0].replace("/", ".") + "."
    out: list[str] = []
    cur = t
    for _ in types:
        qualified = cur.qualified_name
        parts = qualified[len(module):].split(".") if qualified.startswith(module) else []
        if len(parts) < 2:
            break
        holders = [
            u for u in types
            if u is not cur and u.name == parts[-2]
            and u.start_line <= cur.start_line and cur.end_line <= u.end_line
        ]
        if not holders:
            break
        cur = max(holders, key=lambda s: (s.start_line, -s.end_line))
        out.insert(0, cur.name)
    return out


async def load_clr_symbols(session: Any, repository_id: str) -> dict[str, list[IndexedSymbol]]:
    """The indexed ``.cs`` files' namespaces, types and callables, for :func:`place_clr_frames`."""
    from sqlalchemy import select

    from repowise.core.persistence.models import WikiSymbol
    from repowise.core.workspace.repo_index import IndexedSymbol

    rows = await session.execute(
        select(
            WikiSymbol.symbol_id, WikiSymbol.name, WikiSymbol.qualified_name,
            WikiSymbol.kind, WikiSymbol.file_path, WikiSymbol.start_line,
            WikiSymbol.end_line,
        ).where(
            WikiSymbol.repository_id == repository_id,
            WikiSymbol.file_path.like("%.cs"),
            WikiSymbol.kind.in_(sorted({"module", *_CLR_TYPE_KINDS, *_CALLABLE_KINDS})),
        )
    )
    symbols: dict[str, list[IndexedSymbol]] = {}
    for r in rows:
        symbols.setdefault(r.file_path, []).append(
            IndexedSymbol(
                symbol_id=r.symbol_id, name=r.name, qualified_name=r.qualified_name,
                kind=r.kind, signature="", file_path=r.file_path,
                start_line=r.start_line, end_line=r.end_line, visibility="",
            )
        )
    return symbols


def _csharp(frame: ProfileFrame, symbols: Sequence[IndexedSymbol]) -> _Hit | SkipReason:
    """A .NET frame joined by name: it carries a type, a method and parameters, never a line.

    The method must be a direct member of the frame's innermost type, and an
    overload is picked by its parameters (:func:`_name_join`), never by name
    alone. A lambda, local function or iterator/async state machine is
    charged to the method it was written in. A static initializer
    (``.cctor``, or a lambda in one) and a closure class's constructor name
    no member (:attr:`ClrFrameName.member_known`): they go to the file.
    """
    name = parse_clr_frame(frame.function)
    if name is None:
        return "no_second_signal"
    if not name.member_known:
        return (None, True)
    callables = [s for s in symbols if s.kind in _CALLABLE_KINDS]
    owner = name.types[-1]
    if name.enclosing is None:
        return _name_join(callables, owner=owner, name=name.wanted, params=name.params, by_count=True)
    hit = _name_join(callables, owner=owner, name=name.wanted, params=None, by_count=True)
    return (hit[0], True) if isinstance(hit, tuple) else hit


# -- Pascal --------------------------------------------------------------------

# The program's main block and a unit's initialization and finalization, as
# FPC names them: ``$main``, ``$UNIT_$$_init$``, ``$UNIT_$$_finalize$``.
_PAS_BLOCK_RE = re.compile(r"\$main|\$.+_\$\$_(?:init|finalize)\$")


def _pascal(
    frame: ProfileFrame,
    symbols: Sequence[IndexedSymbol],
    lines: Callable[[], list[str] | None],
) -> _Hit | SkipReason:
    # FPC records no start line, so the second signal is the name, and Pascal
    # names have no case.
    name = frame.function.casefold()
    if not name:
        return "no_second_signal"
    symbol = symbol_at((s for s in symbols if s.kind in _CALLABLE_KINDS), frame.line)
    if symbol is None:
        return (None, False) if _PAS_BLOCK_RE.fullmatch(name) else "no_span"
    if _qualname_tail(name, symbol.qualified_name.casefold()):
        return (symbol, False)
    # A nested routine has no symbol of its own and DWARF gives its bare name:
    # only its declaration inside the symbol ties it to the symbol.
    text = lines()
    if text is None:
        return "no_source"
    declared = re.compile(rf"\s*(?:procedure|function)\s+{re.escape(name)}(?!\w)", re.IGNORECASE)
    if any(declared.match(line) for line in text[symbol.start_line : frame.line]):
        return (symbol, True)
    return "second_signal_mismatch"


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
    "ClrFrameName",
    "FrameLanguage",
    "FrameResolution",
    "JvmFrameName",
    "ProfileFrame",
    "ResolvedFrame",
    "SkipReason",
    "checkout_reader",
    "dirty_path_checker",
    "frame_paths",
    "load_clr_symbols",
    "load_frame_symbols",
    "load_jvm_source_paths",
    "parse_clr_frame",
    "parse_jvm_frame",
    "place_clr_frames",
    "place_jvm_frames",
    "resolve_frames",
]
