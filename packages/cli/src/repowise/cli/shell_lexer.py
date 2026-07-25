"""A single-pass shell tokenizer for the rewrite hot path.

``rewrite_hook`` fires before every shell command an AI agent runs, so this
module is stdlib-only and import-cheap by design: no ``repowise.core``, no
third-party imports, no module-level work beyond building a few frozensets.
``test_rewrite_perf`` pins both properties.

It replaces the hook's older approach — a regex that bailed on *any* of
``[|&;<>`]`` plus a blanket bail on any quote — with a state machine that
knows where quoting starts and stops. Two payoffs:

1. **Fewer false bails.** ``git commit -m "fix a|b"`` used to bail because it
   contains both a quote and a pipe, even though the pipe is inside the
   quotes. The lexer sees one segment and no operator.
2. **Structural pipeline analysis.** A single pipe into a stdin-consuming
   filter (``grep``/``rg``/``head``/``tail``) is recognized by shape rather
   than by a bespoke regex per tail shape, so the producer stage can be
   classified while the pipeline still runs verbatim.

Nothing here decides policy about *executing* a command; it reports
structure. The hook owns the platform gate and the quoting rules.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "SAFE_FINAL_TOOLS",
    "Pipeline",
    "Token",
    "analyze_pipeline",
    "render",
    "tokenize",
]


@dataclass(frozen=True, slots=True)
class Token:
    """One lexed piece of a command line.

    ``kind`` is one of ``"arg"`` (a word, with any quoting preserved
    verbatim), ``"op"`` (something that ends a simple command: ``&&``,
    ``||``, ``;``, ``&``, a newline, a backtick, ``$(``), ``"pipe"``
    (``|`` or the stderr-pipe ``|&``), or ``"redirect"`` (``>``, ``>>``,
    ``<``, ``2>&1``, …).
    """

    kind: str
    text: str


def tokenize(command: str) -> list[Token]:
    """Split *command* into structural tokens in a single left-to-right pass.

    Quotes and backslash escapes bind their contents into the surrounding
    argument (and are kept verbatim, so ``" ".join`` of the argument texts
    round-trips a normally-spaced command). Operators, pipes, and redirects
    split arguments apart.
    """
    tokens: list[Token] = []
    buffer: list[str] = []
    index, length = 0, len(command)

    def flush() -> None:
        if buffer:
            tokens.append(Token("arg", "".join(buffer)))
            buffer.clear()

    while index < length:
        char = command[index]
        if char in " \t":
            flush()
            index += 1
        elif char in "\r\n":
            # A newline separates commands exactly like ``;`` does; treating
            # it as whitespace would silently merge two commands into one.
            flush()
            tokens.append(Token("op", "\n"))
            index += 2 if command[index : index + 2] == "\r\n" else 1
        elif char == "\\" and index + 1 < length:
            if command[index + 1] in "\r\n":
                # Line continuation: the command spans lines, which callers
                # are told is a bailout. Surface it as one rather than
                # hiding the newline inside an argument.
                flush()
                tokens.append(Token("op", "\\\n"))
                index += 2
                continue
            buffer.append(command[index : index + 2])
            index += 2
        elif char in "\"'":
            # Copy the quoted run verbatim, quotes included.
            quote = char
            end = index + 1
            while end < length and command[end] != quote:
                if command[end] == "\\" and quote == '"' and end + 1 < length:
                    end += 2
                    continue
                end += 1
            if end >= length:
                # Unterminated: everything after the opening quote is
                # unparseable, so no structural claim about it is honest.
                # Emit an operator and let every caller bail.
                flush()
                tokens.append(Token("op", quote))
                break
            buffer.append(command[index : end + 1])
            index = end + 1
        elif char == "|":
            flush()
            following = command[index + 1] if index + 1 < length else ""
            if following == "|":
                tokens.append(Token("op", "||"))
                index += 2
            elif following == "&":
                tokens.append(Token("pipe", "|&"))
                index += 2
            else:
                tokens.append(Token("pipe", "|"))
                index += 1
        elif char in "&;":
            flush()
            if char == "&" and command[index + 1 : index + 2] == "&":
                tokens.append(Token("op", "&&"))
                index += 2
            else:
                tokens.append(Token("op", char))
                index += 1
        elif char in "<>":
            # A leading file-descriptor digit (the ``2`` in ``2>&1``) abuts
            # the operator with no space, so it is sitting in the buffer as
            # its own word — pull it back into the redirect token. Without
            # this the command re-renders as ``cmd 2 >&1``, which is a
            # different (and broken) command.
            descriptor = ""
            pending = "".join(buffer)
            if pending.isdigit():
                descriptor = pending
                buffer.clear()
            flush()
            end = index
            while end < length and command[end] in "<>":
                end += 1
            # ``>&2`` / ``>&-`` duplicate or close a descriptor; a bare ``&``
            # after the operator is the background operator instead, and must
            # stay its own token (``cmd 2>&1 &`` is two things, not one).
            target = command[end + 1 : end + 2]
            if command[end : end + 1] == "&" and target and target in "-0123456789":
                end += 2
                while end < length and command[end].isdigit():
                    end += 1
            tokens.append(Token("redirect", descriptor + command[index:end]))
            index = end
        elif char == "`":
            flush()
            tokens.append(Token("op", "`"))
            index += 1
        elif char == "$" and command[index + 1 : index + 2] == "(":
            flush()
            tokens.append(Token("op", "$("))
            index += 2
        else:
            buffer.append(char)
            index += 1
    flush()
    return tokens


def render(tokens: list[Token]) -> str:
    """Re-render *tokens* as a command string with single-space separation.

    Argument text keeps its original quoting and redirects keep their file
    descriptor (``2>&1`` never becomes ``2 >&1``), so the result means what
    the original meant. It is not a byte round-trip: runs of whitespace
    collapse to one space, and a redirect written flush against its target
    (``2>/dev/null``) gains a space before it.
    """
    return " ".join(token.text for token in tokens).strip()


#: Final pipeline stages whose only job is to filter stdin, so the stage
#: before them still owns the interesting output. The pattern-file forms of
#: grep/rg (``-f``/``--file``) are excluded by ``analyze_pipeline``: they read
#: a file as config, which changes what the stage consumes.
SAFE_FINAL_TOOLS = frozenset({"grep", "egrep", "fgrep", "rg", "head", "tail"})


@dataclass(frozen=True, slots=True)
class Pipeline:
    """Structure of a command that is at most one plain ``|`` pipeline.

    ``producer`` is the re-rendered first stage (the whole command when
    there is no pipe), ``final_tool`` is the bare tool name of the filtering
    stage or None when there is no pipe, and ``redirects`` collects every
    redirect token seen in any stage so callers can apply their own policy.
    """

    producer: str
    final_tool: str | None
    redirects: tuple[str, ...]


def _basename(word: str) -> str:
    """Bare tool name from an argument, ignoring quoting and any path."""
    return word.strip("\"'").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]


def _short_cluster(arg: str) -> str:
    """The bundled short flags in *arg*, or "" if it is not a short group."""
    if len(arg) > 1 and arg[0] == "-" and arg[1] != "-":
        return arg[1:]
    return ""


def _disqualifies_final_stage(tool: str, args: list[str]) -> bool:
    """True if *args* stop *tool* from being a plain one-shot stdin filter.

    Two different reasons, so the check is tool-aware rather than a shared
    flag blocklist (``-F`` means "fixed strings" to grep and "follow" to
    tail; blocking it for both would cost real coverage):

    - ``head``/``tail`` in follow mode never close the pipe, so the producer
      is never signalled and the capture never returns.
    - ``grep``/``rg`` given ``-f``/``--file`` read a file as their pattern
      list, which is a different command than the one being reasoned about.
    """
    if tool in ("head", "tail"):
        for arg in args:
            if arg == "--follow" or arg.startswith("--follow="):
                return True
            cluster = _short_cluster(arg)
            if "f" in cluster or "F" in cluster:
                return True
        return False
    for arg in args:
        if arg in ("-f", "--file") or arg.startswith("--file="):
            return True
        # ``-f`` takes a value, so in a bundle it is always last (``-if x``)
        # or carries the value attached (``-fx``).
        if "f" in _short_cluster(arg):
            return True
    return False


def analyze_pipeline(command: str) -> Pipeline | None:
    """Describe *command* as a simple pipeline, or return None to bail.

    None means the command carries structure a wrapper cannot preserve:
    chaining or backgrounding (``&&``, ``||``, ``;``, ``&``, newline),
    substitution (backticks, ``$(``), a stderr pipe (``|&``), three or more
    stages, or a final stage that is not one of ``SAFE_FINAL_TOOLS``.
    Redirects are *reported*, not rejected, since which ones are tolerable
    depends on the caller.
    """
    tokens = tokenize(command)
    if any(token.kind == "op" for token in tokens):
        return None

    segments: list[list[Token]] = [[]]
    for token in tokens:
        if token.kind == "pipe":
            if token.text != "|":
                return None  # ``|&`` also pipes stderr: not a plain filter
            segments.append([])
        else:
            segments[-1].append(token)

    redirects = tuple(t.text for t in tokens if t.kind == "redirect")
    if len(segments) == 1:
        producer = render(segments[0])
        return Pipeline(producer, None, redirects) if producer else None
    if len(segments) != 2:
        return None  # 3+ stages: not worth the hot-path complexity

    producer_tokens, final_tokens = segments
    final_args = [t.text for t in final_tokens if t.kind == "arg"]
    if not final_args:
        return None
    tool = _basename(final_args[0])
    if tool not in SAFE_FINAL_TOOLS or _disqualifies_final_stage(tool, final_args[1:]):
        return None
    producer = render(producer_tokens)
    if not producer:
        return None
    return Pipeline(producer, tool, redirects)
