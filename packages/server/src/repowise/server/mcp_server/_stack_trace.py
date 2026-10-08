"""Stack-trace frames pasted into a question, and the repo files they name.

Stdlib only. Reads Python, JS/TS, Java/Kotlin, Go, Rust and C# traces.
Frames from the language runtime are dropped; frames from installed
dependencies (site-packages, node_modules, the Go module cache, the cargo
registry) are kept behind the user's frames, keyed on their package-relative
path, so a trace through an installed copy of this repo still lands on it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import NamedTuple


class Frame(NamedTuple):
    path: str  # forward slashes; a package-derived path or a bare file name
    line: int
    function: str | None
    vendored: bool = False


_PY_RE = re.compile(r'File "([^"]+)", line (\d+)(?:, in ([^\s]+))?')
# JS/TS ``at f (path:l:c)`` / ``at path:l:c``; Rust ``at path.rs:l:c``.
_AT_COL_RE = re.compile(r"^\s*at (?:(.+?) \()?(\S+?):(\d+):(\d+)\)?\s*$")
_JAVA_RE = re.compile(r"^\s*at (?:[\w.@-]+/+)?([\w.$<>]+)\(([\w$]+\.(?:java|kt|kts|scala|groovy)):(\d+)\)")
_CS_RE = re.compile(r"^\s*at (.+?)\(.*?\) in (.+?):line (\d+)\s*$")
_GO_FILE_RE = re.compile(r"^\s+(\S+\.go):(\d+)(?: \+0x[0-9a-f]+)?\s*$")
_RUST_FUNC_RE = re.compile(r"^\s*\d+:\s+(?:0x[0-9a-f]+ - )?(\S+)\s*$")
_RUST_PANIC_RE = re.compile(r"panicked at (?:'.*?', )?([^\s:'][^\s:]*\.rs):(\d+):\d+")

_JVM_RUNTIME = ("java.", "javax.", "jdk.", "sun.", "com.sun.", "kotlin.", "kotlinx.", "scala.")
_DOTNET_RUNTIME = ("System.", "Microsoft.")

# Installed dependency roots: the path after the match is package-relative.
_VENDORED_RES = (
    re.compile(r"(?:^|/)(?:site|dist)-packages/(.+)$"),
    re.compile(r"(?:^|/)node_modules/(.+)$"),
    re.compile(r"(?:^|/)pkg/mod/([^@]+)@[^/]+/(.+)$"),  # module path kept, version dropped
    re.compile(r"(?:^|/)\.cargo/registry/src/[^/]+/[^/]+/(.+)$"),
    re.compile(r"(?:^|/)\.cargo/git/checkouts/[^/]+/[^/]+/(.+)$"),
)
_RUNTIME_RES = (
    re.compile(r"^<.*>$"),  # <frozen importlib._bootstrap>, <string>, <anonymous>
    re.compile(r"(?:^|/)lib/python\d[\d.]*/(?!.*(?:site|dist)-packages/)", re.IGNORECASE),
    re.compile(r"(?:^|/)python\d*/lib/(?!.*(?:site|dist)-packages/)", re.IGNORECASE),
    re.compile(r"^(?:node:|internal/|native\b)"),
    re.compile(r"(?:^|/)(?:go|libexec)/src/[^./]+/"),  # GOROOT: no dot in the package root
    re.compile(r"^/rustc/|(?:^|/)\.rustup/"),
)


def _classify(path: str) -> tuple[str, bool] | None:
    """``(path, vendored)`` for a frame worth keeping, ``None`` for runtime."""
    p = path.replace("\\", "/").removeprefix("file://").removeprefix("./")
    if any(r.search(p) for r in _RUNTIME_RES):
        return None
    for r in _VENDORED_RES:
        m = r.search(p)
        if m:
            return "/".join(m.groups()), True
    return p, False


def _java_path(qualified: str, file_name: str) -> str:
    """``io.javalin.Javalin.start`` + ``Javalin.java`` -> ``io/javalin/Javalin.java``."""
    package = []
    for part in qualified.split(".")[:-1]:
        if part[:1].isupper():
            break
        package.append(part)
    return "/".join([*package, file_name])


def _go_function(line: str) -> str | None:
    """``pkg.(*T).m(0xc0, {0x1})`` -> ``pkg.(*T).m``; the line above a Go file line."""
    s = re.sub(r" in goroutine \d+$", "", line.strip()).removeprefix("created by ")
    if s.endswith(")"):
        s = s[: s.rfind("(")]
    return s if s and " " not in s else None


def _python(lines: list[str]) -> list[Frame]:
    frames = [
        Frame(m.group(1), int(m.group(2)), m.group(3))
        for line in lines
        if (m := _PY_RE.search(line))
    ]
    return frames[::-1]  # printed outermost first


def _innermost_first(lines: list[str]) -> list[Frame]:
    """JS/TS, Rust, Java/Kotlin, C# and Go: printed innermost first."""
    frames: list[Frame] = []
    for i, line in enumerate(lines):
        prev = lines[i - 1] if i else ""
        if m := _CS_RE.match(line):
            fn = m.group(1).strip()
            if not fn.startswith(_DOTNET_RUNTIME):
                frames.append(Frame(m.group(2), int(m.group(3)), fn))
        elif m := _JAVA_RE.match(line):
            fn = m.group(1)
            if not fn.startswith(_JVM_RUNTIME):
                frames.append(Frame(_java_path(fn, m.group(2)), int(m.group(3)), fn))
        elif m := _AT_COL_RE.match(line):
            fn = m.group(1)
            if fn is None and (rm := _RUST_FUNC_RE.match(prev)):
                fn = rm.group(1)
            frames.append(Frame(m.group(2), int(m.group(3)), fn))
        elif m := _GO_FILE_RE.match(line):
            frames.append(Frame(m.group(1), int(m.group(2)), _go_function(prev)))
        elif m := _RUST_PANIC_RE.search(line):
            frames.append(Frame(m.group(1), int(m.group(2)), None))
    return frames


def parse_trace(text: str) -> list[Frame]:
    """Frames in ``text``, innermost first: the user's frames, then installed
    dependencies'. Unique per ``(path, line, function)``; runtime frames dropped."""
    lines = text.splitlines()
    user: list[Frame] = []
    vendored: list[Frame] = []
    seen: set[tuple[str, int, str | None]] = set()
    for frame in (*_python(lines), *_innermost_first(lines)):
        kept = _classify(frame.path)
        if kept is None:
            continue
        path, is_vendored = kept
        key = (path, frame.line, frame.function)
        if key in seen:
            continue
        seen.add(key)
        (vendored if is_vendored else user).append(Frame(path, frame.line, frame.function, is_vendored))
    return user + vendored


def frame_basenames(frames: Iterable[Frame]) -> set[str]:
    return {f.path.rsplit("/", 1)[-1] for f in frames}


# A file name more repo files share than this names none of them.
_MAX_AMBIGUOUS = 3


def _segment_suffix(longer: str, shorter: str) -> bool:
    return longer == shorter or longer.endswith("/" + shorter)


def map_to_repo_paths(frames: Iterable[Frame], indexed_paths: Iterable[str]) -> list[str]:
    """Repo files the frames name, in frame order, deduped.

    A frame matches an indexed path when either is a ``/``-bounded suffix of
    the other: an absolute frame ends with the repo path, a relative or
    package-derived one is the tail of it. A frame matching more than
    ``_MAX_AMBIGUOUS`` files is dropped. Vendored frames never match on a bare
    file name, which would only say some installed package has that file.
    """
    indexed = sorted({p.replace("\\", "/") for p in indexed_paths})
    out: list[str] = []
    for frame in frames:
        if frame.vendored and "/" not in frame.path:
            continue
        hits = [p for p in indexed if _segment_suffix(frame.path, p) or _segment_suffix(p, frame.path)]
        if len(hits) > _MAX_AMBIGUOUS:
            continue
        out.extend(h for h in hits if h not in out)
    return out
