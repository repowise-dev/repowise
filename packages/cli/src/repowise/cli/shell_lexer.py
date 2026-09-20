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
    "READONLY_SEGMENT_TOOLS",
    "SAFE_FINAL_TOOLS",
    "Pipeline",
    "Token",
    "analyze_pipeline",
    "is_plain_stdin_filter",
    "is_read_only_segment",
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


def is_plain_stdin_filter(words: list[str]) -> bool:
    """True if *words* is a one-shot stdin filter safe to end a stage.

    The same test ``analyze_pipeline`` applies to a final stage, exposed for
    callers that walk a multi-stage chain themselves and so never build a
    :class:`Pipeline`. Sharing it matters: the disqualifiers are subtle
    (``grep -f`` reads a pattern file, ``tail -F`` never closes the pipe),
    and a caller re-deriving them from ``SAFE_FINAL_TOOLS`` alone gets them
    wrong.
    """
    if not words:
        return False
    tool = _basename(words[0])
    return tool in SAFE_FINAL_TOOLS and not _disqualifies_final_stage(tool, words[1:])


#: Tools admitted as inert chain segments, each with the flags that keep them
#: read-only. An allowlist rather than a blocklist of the write flags: a
#: blocklist has to know every way each tool can be made to write, and the
#: cost of being wrong is a rewrite that is auto-allowed. A flag nobody
#: listed declines the whole chain, which is the failure direction we want.
#:
#: Short flags are matched per letter so bundles (``-rn``) work. Long forms
#: are listed whole; a ``--flag=value`` spelling is matched on its stem.
_READONLY_SEGMENT_FLAGS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    # ``cat`` writes nothing itself -- ``cat > f`` is the redirect, which the
    # caller's redirect rule already declines. These are its display flags.
    "cat": (frozenset("AbeEnstTuv"), frozenset({"--number", "--number-nonblank",
        "--show-all", "--show-ends", "--show-nonprinting", "--show-tabs",
        "--squeeze-blank"})),
    "wc": (frozenset("clLmw"), frozenset({"--bytes", "--chars", "--lines",
        "--max-line-length", "--words"})),
    # ``-o``/``--output`` is the whole reason this is an allowlist: it makes
    # ``sort`` a writer, and it is the one flag a reader would forget.
    "sort": (frozenset("bdfghikMnrstuVz"), frozenset({"--dictionary-order",
        "--general-numeric-sort", "--human-numeric-sort", "--ignore-case",
        "--ignore-leading-blanks", "--key", "--month-sort", "--numeric-sort",
        "--reverse", "--sort", "--stable", "--unique", "--version-sort",
        "--zero-terminated"})),
    # ``sed`` is admitted on its script as well as its flags -- see
    # ``_sed_script_is_read_only``. ``-e`` and ``-f`` are absent on purpose:
    # ``-f`` reads a script file this cannot vet, and ``-e`` moves the script
    # into a position the one-script rule below does not model.
    "sed": (frozenset("nrsEz"), frozenset({"--quiet", "--silent",
        "--regexp-extended", "--separate", "--null-data"})),
}

READONLY_SEGMENT_TOOLS = frozenset(_READONLY_SEGMENT_FLAGS)


def _is_sed_address(part: str) -> bool:
    """True for a line number. Regex addresses are not admitted.

    ``$`` (last line) is a valid sed address and is deliberately absent: the
    hook's chain gate bails on ``$`` anywhere in the command before a segment
    is examined, so admitting it here would be unreachable code that reads as
    a supported shape. Rejecting is the safe direction for any other caller.
    """
    return part.isdigit()


def _sed_script_is_read_only(operands: list[str]) -> bool:
    """True when ``sed``'s script is an optional line address/range then ``p``.

    Deliberately far narrower than "scripts that do not write". Vetting flags
    alone is not enough for ``sed``, because the script is a language:
    ``w``/``W`` and ``s///w`` write files, ``e`` and ``s///e`` execute shell
    commands, and ``r``/``R`` splice files in. A regex address would also
    have to be parsed to find where the command letter even starts.

    Every ``sed`` in the measured corpus is ``sed -n '<range>p' <file>``, so
    the narrow rule costs nothing real and needs no argument about which
    script commands are safe. Written as a hand parser rather than a regex
    because this module commits to importing nothing, and ``test_rewrite_perf``
    pins that.

    The first non-flag operand is the script and the rest are files, which
    holds only while ``-e``/``-f`` are rejected -- which is why they are.
    """
    if not operands:
        return False
    script = operands[0].strip("\"'").strip()
    if script.endswith(";"):
        script = script[:-1].strip()
    if not script.endswith("p"):
        return False
    address = script[:-1].strip()
    if not address:
        return True  # a bare ``p``: print every line
    start, separator, end = address.partition(",")
    if separator and not _is_sed_address(end.strip()):
        return False
    return _is_sed_address(start.strip())


def is_read_only_segment(words: list[str]) -> bool:
    """True when *words* is a read-only invocation of an admitted tool.

    The chain gate treats such a segment as inert: wrapping a chain whose
    every segment is inert or already recognized grants the agent nothing it
    could not already run, and a rewrite is auto-allowed, so "reads and
    cannot be made to write" is the bar rather than "usually harmless".

    A bare ``-`` is stdin, not a flag. Anything else starting with ``-`` must
    be in this tool's allowlist, and an unknown flag declines.
    """
    if not words:
        return False
    tool = _basename(words[0])
    rules = _READONLY_SEGMENT_FLAGS.get(tool)
    if rules is None:
        return False
    short, long = rules
    operands: list[str] = []
    for arg in words[1:]:
        bare = arg.strip("\"'")
        if bare.startswith("--"):
            if bare.split("=", 1)[0] not in long:
                return False
        elif bare.startswith("-") and bare != "-":
            cluster = _short_cluster(bare)
            # A cluster of unknown letters, or one that swallowed a value
            # (``-o out``), declines: every admitted letter is a pure switch.
            if not cluster or any(letter not in short for letter in cluster):
                return False
        else:
            operands.append(arg)
    if tool == "sed":
        return _sed_script_is_read_only(operands)
    return True


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
