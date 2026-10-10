"""What a signature change does to an existing caller.

Comparing two signature strings says only *that* a signature changed. A
reflowed parameter list, a trailing comma and an appended optional argument all
differ as text, and none of them can break a caller. This module parses both
parameter lists and asks the narrower question: could a call site written
against the old signature stop working?

The reading depends on the language. Where callers can name arguments (Python,
C#, Kotlin), a rename breaks them; where they cannot (JavaScript, Go, Java),
it does not. Where types are checked at the call (TypeScript, Rust, Go, Java),
a retyped parameter or return breaks callers; where they are not (Python,
JavaScript), it is only an annotation.

It is conservative in one direction only. A form it cannot parse is reported as
``unknown``, which consumers treat as breaking: not knowing is not the same as
knowing it is safe.

Pure string work over the indexed signatures. No source, no graph.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Values for :attr:`SignatureEffect.effect`, loosest to strictest.
EFFECT_NONE = "none"  # same contract, different text
EFFECT_COMPATIBLE = "compatible"  # changed, but every existing call still works
EFFECT_BREAKING = "breaking"  # an existing call can stop working
EFFECT_UNKNOWN = "unknown"  # unparseable, or a change this module cannot judge

# Callers can pass arguments by name, so a parameter's name is contract.
# Unlisted languages (and ``None``) are treated as named: fail closed.
_POSITIONAL_ONLY_LANGUAGES = frozenset(
    {"javascript", "typescript", "go", "java", "c", "cpp", "rust"}
)
# Annotations are not checked at the call, so a retype is not a break.
# Unlisted languages (and ``None``) are treated as typed: fail closed.
_LENIENT_TYPE_LANGUAGES = frozenset({"python", "javascript", "ruby", "php"})
# ``Type name`` rather than ``name: Type``.
_TYPE_FIRST_LANGUAGES = frozenset({"java", "csharp", "c", "cpp", "dart", "php"})
_HASH_COMMENT_LANGUAGES = frozenset({"python", "ruby", "php"})

_PAIRS = {")": "(", "]": "[", "}": "{"}
_ASYNC = re.compile(r"^\s*async\b")
# A Go method signature leads with its receiver: ``func (r *T) Name(a int)``.
_GO_RECEIVER = re.compile(r"^\s*func\s*\(")
# ``operator()(int a)``: the first parentheses are the operator's name.
_CALL_OPERATOR = re.compile(r"\boperator\s*\(\s*\)")
_RUST_RECEIVER = re.compile(r"^&?\s*(?:'\w+\s+)?(?:mut\s+)?self\b")
_PY_RECEIVERS = frozenset({"self", "cls"})
# A user-defined type name, possibly an alias of the other one.
_NAMED_TYPE = re.compile(r"^[A-Z][\w.]*$")
_TYPE_FIRST_NAME = re.compile(r"^(?P<type>.*?[\s*&\]>])?(?P<name>\$?\w+)$")
_LAMBDA_OPEN = re.compile(r"=\s*lambda\b[^:]*$")

_VARIADIC_KINDS = ("args", "kwargs", "block")


@dataclass(frozen=True, slots=True)
class SignatureEffect:
    """The verdict, and the specific difference behind it."""

    effect: str
    reason: str


@dataclass(slots=True)
class _Param:
    name: str
    #: ``param`` | ``args`` | ``kwargs`` | ``block`` | ``kw_marker`` (Python's
    #: bare ``*``) | ``pos_marker`` (Python's ``/``).
    kind: str = "param"
    #: How the variadic was spelled (``*``, ``**``, ``...``, ``&``), for reasons.
    prefix: str = ""
    has_default: bool = False
    default: str = ""
    #: TypeScript's ``x?: T``: same caller-visible effect as a default.
    optional: bool = False
    #: Ruby's ``key:``, which no caller can pass by position.
    keyword_only: bool = False
    type: str = ""
    # Filled in by ``_passability``.
    position: int | None = None
    by_keyword: bool = False

    @property
    def required(self) -> bool:
        return self.kind == "param" and not (self.has_default or self.optional)


@dataclass(slots=True)
class _Signature:
    params: list[_Param]
    returns: str
    receiver: str | None = None


@dataclass
class _Notes:
    breaking: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    compatible: list[str] = field(default_factory=list)

    def add(self, effect: str, reason: str) -> None:
        bucket = {
            EFFECT_BREAKING: self.breaking,
            EFFECT_UNKNOWN: self.unknown,
            EFFECT_COMPATIBLE: self.compatible,
        }[effect]
        if reason not in bucket:
            bucket.append(reason)

    def verdict(self) -> SignatureEffect:
        for effect, reasons in (
            (EFFECT_BREAKING, self.breaking),
            (EFFECT_UNKNOWN, self.unknown),
            (EFFECT_COMPATIBLE, self.compatible),
        ):
            if reasons:
                return SignatureEffect(effect, _join(reasons))
        return SignatureEffect(EFFECT_NONE, "only formatting changed; the parameters are the same")


# ---------------------------------------------------------------------------
# Lexing
# ---------------------------------------------------------------------------


def _quotes(lang: str | None) -> str:
    # Rust's ``'a`` is a lifetime, never a string.
    quotes = '"' if lang == "rust" else "\"'"
    return quotes + "`" if lang in ("javascript", "typescript") else quotes


def _strip_comments(text: str, lang: str | None) -> str:
    """Drop comments outside strings, so an apostrophe in one cannot open a quote."""
    quotes = _quotes(lang)
    slash = lang not in ("python", "ruby")
    hashed = lang in _HASH_COMMENT_LANGUAGES
    out: list[str] = []
    quote: str | None = None
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if quote is not None:
            out.append(text[i : i + 2] if ch == "\\" else ch)
            if ch == quote:
                quote = None
            i += 2 if ch == "\\" else 1
            continue
        if ch in quotes:
            quote = ch
        elif slash and text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            out.append(" ")
            continue
        elif (slash and text.startswith("//", i)) or (
            hashed and ch == "#" and text[i + 1 : i + 2] != "["
        ):
            end = text.find("\n", i)
            i = n if end < 0 else end
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _normalize(signature: str, lang: str | None) -> str:
    return re.sub(r"\s+", " ", _strip_comments(signature or "", lang)).strip()


def _top_level(text: str, lang: str | None) -> list[bool] | None:
    """Per character, whether it sits outside every bracket and string.

    ``None`` when brackets or quotes do not balance. ``<`` opens a generic only
    right after a name (``Map<``), never in Python, and never as ``<<``/``<=``,
    so shifts and comparisons in a default value stay operators.
    """
    top = [False] * len(text)
    stack: list[str] = []
    quotes = _quotes(lang)
    quote: str | None = None
    i = 0
    while i < len(text):
        ch = text[i]
        prev = text[i - 1] if i else ""
        if quote is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in quotes:
            quote = ch
        elif ch in "([{":
            stack.append(ch)
        elif (
            ch == "<"
            and lang != "python"
            and (prev.isalnum() or prev in "_.$")
            and text[i + 1 : i + 2] not in ("<", "=")
        ):
            stack.append("<")
        elif ch == ">" and stack and stack[-1] == "<" and prev not in ("=", "-"):
            stack.pop()
        elif ch in _PAIRS:
            if not stack or stack[-1] != _PAIRS[ch]:
                return None
            stack.pop()
        else:
            top[i] = not stack
        i += 1
    return None if stack or quote is not None else top


def _split(text: str, lang: str | None, seps: str = ",") -> list[str] | None:
    top = _top_level(text, lang)
    if top is None:
        return None
    parts: list[str] = []
    start = 0
    for i, ch in enumerate(text):
        if ch in seps and top[i]:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    if lang == "python":
        # ``key=lambda a, b: a`` holds a comma before its colon.
        merged: list[str] = []
        for part in parts:
            if merged and _LAMBDA_OPEN.search(merged[-1]):
                merged[-1] += "," + part
            else:
                merged.append(part)
        parts = merged
    return [p.strip() for p in parts if p.strip()]


def _find_top(text: str, lang: str | None, needle: str) -> int | None:
    top = _top_level(text, lang)
    if top is None:
        return None
    for i, ch in enumerate(text):
        if ch != needle or not top[i]:
            continue
        # ``==``, ``!=``, ``<=``, ``>=``, ``=>`` and ``:=`` are not defaults.
        if needle == "=" and (
            text[i - 1 : i] in ("=", "!", "<", ">", ":") or text[i + 1 : i + 2] in ("=", ">")
        ):
            continue
        return i
    return None


def _close_paren(text: str, open_at: int, lang: str | None) -> int | None:
    """Index of the ``)`` closing ``text[open_at]``, skipping strings."""
    quotes = _quotes(lang)
    quote: str | None = None
    depth = 0
    i = open_at
    while i < len(text):
        ch = text[i]
        if quote is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in quotes:
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_signature(sig: str, lang: str | None, kind: str | None) -> _Signature | None:
    start = 0
    receiver: str | None = None
    if _GO_RECEIVER.match(sig):
        open_at = sig.find("(")
        close = _close_paren(sig, open_at, lang)
        if close is None:
            return None
        receiver = sig[open_at + 1 : close].strip()
        start = close + 1
    elif (m := _CALL_OPERATOR.search(sig)) is not None:
        start = m.end()
    open_at = sig.find("(", start)
    if open_at < 0:
        return None
    close = _close_paren(sig, open_at, lang)
    if close is None:
        return None
    raw = _split(sig[open_at + 1 : close], lang)
    if raw is None:
        return None
    if lang in ("c", "cpp") and raw == ["void"]:
        raw = []
    parse = (
        _parse_go
        if lang == "go"
        else _parse_type_first
        if lang in _TYPE_FIRST_LANGUAGES
        else _parse_colon
    )
    params = [parse(text, n, lang) for n, text in enumerate(raw)]
    if lang == "go":
        _group_go(params)
    if params and lang == "rust" and _RUST_RECEIVER.match(raw[0]):
        receiver = raw[0]
        params = params[1:]
    elif params and lang == "python" and kind != "function" and params[0].name in _PY_RECEIVERS:
        receiver = params[0].name
        params = params[1:]
    returns = re.sub(r"^(->|:)\s*", "", sig[close + 1 :].strip()).strip()
    return _Signature(params=params, returns=returns, receiver=receiver)


def parameter_names(
    signature: str, language: str | None = None, kind: str | None = None
) -> tuple[str | None, list[str]] | None:
    """The receiver (Python's ``self`` / ``cls``, a Rust ``&self``, a Go
    receiver; None when there is none) and the names a call passes in order.

    None when the list cannot be parsed or a call could not fill it by
    position alone: a variadic, a keyword-only marker or argument, or a
    destructured parameter.
    """
    lang = (language or "").lower() or None
    parsed = _parse_signature(_normalize(signature, lang), lang, kind)
    if parsed is None:
        return None
    names: list[str] = []
    for param in parsed.params:
        if param.kind != "param" or param.keyword_only or not param.name.isidentifier():
            return None
        names.append(param.name)
    return parsed.receiver, names


def _split_default(text: str, lang: str | None, param: _Param) -> str:
    at = _find_top(text, lang, "=")
    if at is None:
        return text
    param.has_default = True
    param.default = text[at + 1 :].strip()
    return text[:at].strip()


def _parse_colon(text: str, position: int, lang: str | None) -> _Param:
    """``name: Type = default``: Python, TypeScript, Rust, Kotlin, Swift, Scala, Ruby."""
    param = _Param(name="")
    if lang == "python" and text in ("*", "/"):
        return _Param(name=text, kind="kw_marker" if text == "*" else "pos_marker")
    for prefix, kind in (("**", "kwargs"), ("...", "args"), ("*", "args"), ("vararg ", "args")):
        if text.startswith(prefix):
            param.kind, param.prefix, text = kind, prefix.strip(), text[len(prefix) :]
            break
    else:
        if text.startswith("&") and lang == "ruby":
            param.kind, param.prefix, text = "block", "&", text[1:]

    if lang == "ruby":
        # Ruby's colon marks a keyword argument; what follows it is the default.
        at = _find_top(text, lang, ":")
        if at is not None:
            param.keyword_only = True
            param.default = text[at + 1 :].strip()
            param.has_default = bool(param.default)
            text = text[:at]
        else:
            text = _split_default(text, lang, param)
    else:
        text = _split_default(text, lang, param)
        at = _find_top(text, lang, ":")
        if at is not None:
            param.type = text[at + 1 :].strip()
            text = text[:at].strip()
    if param.type.endswith(("*", "...")) and lang in ("scala", "swift"):
        param.kind, param.prefix = "args", "..."

    text = text.strip()
    if lang == "rust":
        text = re.sub(r"^mut\s+", "", text)  # a local binding, not the contract
    # A destructured parameter has no caller-visible name, so it is keyed by
    # position: reading one more field out of it is not a contract change.
    if text.startswith(("{", "[")):
        param.name = f"<destructured:{position}>"
    else:
        param.optional = text.endswith("?")
        param.name = text.rstrip("?").strip() or f"<anonymous:{position}>"
    return param


def _parse_type_first(text: str, position: int, lang: str | None) -> _Param:
    """``Type name = default``: Java, C#, C, C++, Dart, PHP."""
    param = _Param(name="")
    if text.startswith(("{", "[")):
        # Dart's named or optional group: any edit inside it is judged whole.
        return _Param(name=f"<group:{position}>", type=text)
    text = _split_default(text, lang, param)
    text = re.sub(r"^(?:final|const)\s+", "", text)
    if text.startswith("params "):
        param.kind, text = "args", text[7:]
    if text == "...":
        return _Param(name=f"<varargs:{position}>", kind="args")
    if "..." in text:
        param.kind, text = "args", text.replace("...", " ")
    m = _TYPE_FIRST_NAME.match(text.strip())
    if m is not None and (m.group("type") or m.group("name").startswith("$") or lang == "dart"):
        param.name = m.group("name")
        param.type = _normalize(m.group("type") or "", lang)
    else:
        # C's unnamed ``int``: the type is all there is.
        param.name = f"<anonymous:{position}>"
        param.type = text.strip()
    return param


def _parse_go(text: str, position: int, lang: str | None) -> _Param:
    """``name Type``, ``name ...Type``, or a bare ``Type``; grouping comes after."""
    name, _, rest = text.partition(" ")
    param = _Param(name=name, type=rest.strip())
    if param.type.startswith("..."):
        param.kind, param.type = "args", param.type[3:].strip()
    elif name.startswith("..."):
        param.kind, param.name, param.type = "args", f"<anonymous:{position}>", name[3:]
    return param


def _group_go(params: list[_Param]) -> None:
    """``a, b int`` is ``a int, b int``; with no names at all, each bare token is a type."""
    if not any(p.type for p in params):
        for n, p in enumerate(params):
            if p.kind == "param":
                p.name, p.type = f"<anonymous:{n}>", p.name
        return
    pending: list[_Param] = []
    for p in params:
        if p.type:
            for q in pending:
                q.type = p.type
            pending = []
        else:
            pending.append(p)


def _passability(params: list[_Param], lang: str | None) -> list[_Param]:
    """Stamp how a caller can pass each parameter; return the real parameters."""
    named = lang not in _POSITIONAL_ONLY_LANGUAGES
    slash = next((n for n, p in enumerate(params) if p.kind == "pos_marker"), None)
    positional = True
    index = 0
    out: list[_Param] = []
    for n, p in enumerate(params):
        if p.kind in ("kw_marker", "args"):
            positional = False
        if p.kind != "param":
            if p.kind not in ("kw_marker", "pos_marker"):
                out.append(p)
            continue
        if positional and not p.keyword_only:
            p.position = index
            index += 1
        p.by_keyword = named and (slash is None or n > slash)
        out.append(p)
    return out


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def classify_signature_change(
    before: str, after: str, language: str | None = None, kind: str | None = None
) -> SignatureEffect:
    """Compare two indexed signatures of the same symbol, from a caller's side.

    *language* is the repowise language name of the file; ``None`` reads it
    strictly (names and types both count). *kind* is the symbol kind: a Python
    ``function`` has no receiver, so a leading ``self`` there is a parameter.
    """
    lang = (language or "").lower() or None
    lhs, rhs = _normalize(before, lang), _normalize(after, lang)
    if lhs == rhs:
        return SignatureEffect(EFFECT_NONE, "only whitespace or comments changed")

    # ``async`` is a real contract: a caller that did not await now gets a
    # coroutine, and one that did now awaits a plain value.
    was_async, is_async = bool(_ASYNC.match(lhs)), bool(_ASYNC.match(rhs))
    if was_async != is_async:
        verb = "became async" if is_async else "is no longer async"
        return SignatureEffect(EFFECT_BREAKING, f"{verb}, so its callers' await changes")

    old, new = _parse_signature(lhs, lang, kind), _parse_signature(rhs, lang, kind)
    if old is None or new is None:
        return SignatureEffect(EFFECT_UNKNOWN, "the parameter list could not be parsed")

    notes = _Notes()
    _compare_receivers(old.receiver, new.receiver, lang, notes)
    _compare_params(_passability(old.params, lang), _passability(new.params, lang), lang, notes)
    _compare_type(old.returns, new.returns, lang, None, notes)
    return notes.verdict()


def _compare_receivers(old: str | None, new: str | None, lang: str | None, notes: _Notes) -> None:
    if old == new:
        return
    if lang == "python":
        # ``K.f(a)`` stops working once ``f`` needs an instance.
        if new == "self":
            notes.add(
                EFFECT_BREAKING, "became an instance method, so calls through the class break"
            )
        else:
            notes.add(EFFECT_COMPATIBLE, "no longer needs an instance")
    elif lang in ("go", None):
        # Only a Go receiver is ever extracted without a language.
        was_ptr, is_ptr = "*" in (old or ""), "*" in (new or "")
        if is_ptr and not was_ptr:
            # A value of the type no longer satisfies interfaces through it.
            notes.add(EFFECT_BREAKING, "moved to a pointer receiver")
        elif was_ptr and not is_ptr:
            notes.add(EFFECT_COMPATIBLE, "moved to a value receiver")
    else:
        notes.add(
            EFFECT_BREAKING, f"changed its receiver from `{old or 'none'}` to `{new or 'none'}`"
        )


def _compare_params(
    before: list[_Param], after: list[_Param], lang: str | None, notes: _Notes
) -> None:
    old_regular = {p.name: p for p in before if p.kind == "param"}
    new_regular = {p.name: p for p in after if p.kind == "param"}
    removed = [p for p in old_regular.values() if p.name not in new_regular]
    added = [p for p in new_regular.values() if p.name not in old_regular]
    pairs = [(p, new_regular[p.name]) for p in old_regular.values() if p.name in new_regular]

    # A parameter gone and one arrived in the same slot is a rename.
    for gone in list(removed):
        arrived = next(
            (q for q in added if gone.position is not None and q.position == gone.position), None
        )
        if arrived is None:
            continue
        removed.remove(gone)
        added.remove(arrived)
        pairs.append((gone, arrived))
        if gone.by_keyword:
            notes.add(EFFECT_BREAKING, f"renamed `{gone.name}` to `{arrived.name}`")
        else:
            notes.add(
                EFFECT_COMPATIBLE,
                f"renamed `{gone.name}` to `{arrived.name}`, which callers never name",
            )

    old_var = {p.kind: p for p in before if p.kind in _VARIADIC_KINDS}
    new_var = {p.kind: p for p in after if p.kind in _VARIADIC_KINDS}

    # Java's ``T[] a`` becoming ``T... a`` still accepts the array.
    arrived_args = new_var.get("args")
    if arrived_args is not None and "args" not in old_var:
        for gone in removed:
            if gone.type.replace(" ", "") == arrived_args.type.replace(" ", "") + "[]":
                removed.remove(gone)
                old_var["args"] = arrived_args
                notes.add(EFFECT_COMPATIBLE, f"now takes `{gone.name}` as varargs")
                break

    for p in removed:
        label = "the required " if p.required else ""
        notes.add(EFFECT_BREAKING, f"removed {label}`{p.name}`")

    for q in added:
        if q.required:
            notes.add(EFFECT_BREAKING, f"added the required `{q.name}`")
        elif q.position is not None and "args" in old_var:
            # Extra positional arguments used to land in the variadic.
            notes.add(EFFECT_BREAKING, f"inserted `{q.name}` before `{_display(old_var['args'])}`")
        else:
            notes.add(EFFECT_COMPATIBLE, f"added optional `{q.name}`")

    for p, q in pairs:
        _compare_pair(p, q, added, removed, lang, notes)

    for var_kind in _VARIADIC_KINDS:
        p, q = old_var.get(var_kind), new_var.get(var_kind)
        if p is not None and q is None:
            notes.add(EFFECT_BREAKING, f"removed `{_display(p)}`")
        elif p is None and q is not None:
            notes.add(EFFECT_COMPATIBLE, f"added `{_display(q)}`")
        elif p is not None and q is not None and p is not q:
            _compare_type(p.type, q.type, lang, f"`{_display(q)}`", notes)


def _compare_pair(
    p: _Param,
    q: _Param,
    added: list[_Param],
    removed: list[_Param],
    lang: str | None,
    notes: _Notes,
) -> None:
    name = q.name
    if p.position is not None and q.position is None:
        notes.add(EFFECT_BREAKING, f"made `{name}` keyword-only")
    elif p.position is None and q.position is not None:
        notes.add(EFFECT_COMPATIBLE, f"`{name}` can now be passed by position")
    elif p.position is not None and q.position is not None and p.position != q.position:
        inserted = [a for a in added if a.position is not None and a.position < q.position]
        if inserted:
            for a in inserted:
                notes.add(EFFECT_BREAKING, f"inserted `{a.name}` before `{name}`")
        elif not any(r.position is not None and r.position < p.position for r in removed):
            notes.add(EFFECT_BREAKING, "reordered its parameters")
    if p.by_keyword and not q.by_keyword:
        notes.add(EFFECT_BREAKING, f"made `{name}` positional-only")
    elif q.by_keyword and not p.by_keyword:
        notes.add(EFFECT_COMPATIBLE, f"`{name}` can now be passed by name")
    if p.required and not q.required:
        notes.add(EFFECT_COMPATIBLE, f"`{name}` is now optional")
    elif q.required and not p.required:
        notes.add(EFFECT_BREAKING, f"`{name}` is now required")
    elif p.has_default and q.has_default and p.default != q.default:
        notes.add(EFFECT_COMPATIBLE, f"changed the default of `{name}`")
    _compare_type(p.type, q.type, lang, _label(name), notes)


def _compare_type(old: str, new: str, lang: str | None, subject: str | None, notes: _Notes) -> None:
    """A parameter's type (``subject`` names it) or, with ``subject=None``, the return."""
    if old == new:
        return
    if lang in _LENIENT_TYPE_LANGUAGES:
        if subject is None:
            notes.add(EFFECT_COMPATIBLE, "changed its return annotation")
        else:
            notes.add(EFFECT_COMPATIBLE, f"{'retyped' if old else 'annotated'} {subject}")
        return
    what = f"the type of {subject}" if subject else "its return type"
    if not old or not new:
        # An inferred type made explicit, or the reverse: it may be the same type.
        notes.add(EFFECT_UNKNOWN, f"{'declared' if new else 'dropped'} {what}")
    elif old.startswith("{") and old.endswith("}") and new.startswith("{") and new.endswith("}"):
        _compare_members(old, new, lang, notes)
    elif _widens(old, new, lang) if subject else _widens(new, old, lang):
        # A parameter accepting more, or a return promising less, keeps every call.
        verb = "widened" if subject else "narrowed"
        notes.add(EFFECT_COMPATIBLE, f"{verb} {what} from `{old}` to `{new}`")
    elif _NAMED_TYPE.match(old) and _NAMED_TYPE.match(new):
        # Two type names may be aliases of one another; the text cannot say.
        notes.add(EFFECT_UNKNOWN, f"changed {what} from `{old}` to `{new}`, which may be an alias")
    else:
        notes.add(EFFECT_BREAKING, f"changed {what} from `{old}` to `{new}`")


def _widens(narrow: str, wide: str, lang: str | None) -> bool:
    """Whether every member of the union *narrow* is a member of the union *wide*."""
    lhs, rhs = _split(narrow, lang, seps="|"), _split(wide, lang, seps="|")
    return lhs is not None and rhs is not None and set(lhs) < set(rhs)


def _members(literal: str, lang: str | None) -> dict[str, tuple[bool, str]] | None:
    """``{a: string; b?: number}`` as ``{name: (optional, type)}``."""
    parts = _split(literal[1:-1], lang, seps=";,")
    if parts is None:
        return None
    out: dict[str, tuple[bool, str]] = {}
    for part in parts:
        part = re.sub(r"^readonly\s+", "", part)
        at = _find_top(part, lang, ":")
        paren = part.find("(")
        if paren >= 0 and (at is None or paren < at):
            name, mtype = part[:paren].strip(), part[paren:].strip()
        elif at is not None:
            name, mtype = part[:at].strip(), part[at + 1 :].strip()
        else:
            name, mtype = part.strip(), ""
        out[name.rstrip("?").strip()] = (name.endswith("?"), mtype)
    return out


def _compare_members(old: str, new: str, lang: str | None, notes: _Notes) -> None:
    before, after = _members(old, lang), _members(new, lang)
    if before is None or after is None:
        notes.add(EFFECT_UNKNOWN, "changed an inline type it could not parse")
        return
    for name, (optional, _) in after.items():
        if name not in before:
            if optional:
                notes.add(EFFECT_COMPATIBLE, f"added optional member `{name}`")
            else:
                notes.add(EFFECT_BREAKING, f"added the required member `{name}`")
    for name, (was_optional, old_type) in before.items():
        if name not in after:
            notes.add(EFFECT_BREAKING, f"removed member `{name}`")
            continue
        is_optional, new_type = after[name]
        if was_optional and not is_optional:
            notes.add(EFFECT_BREAKING, f"member `{name}` is now required")
        elif is_optional and not was_optional:
            notes.add(EFFECT_COMPATIBLE, f"member `{name}` is now optional")
        _compare_type(old_type, new_type, lang, f"member `{name}`", notes)


def _display(p: _Param) -> str:
    return f"{p.prefix or '...'}{p.name}"


def _label(name: str) -> str:
    if name.startswith("<destructured"):
        return "the destructured parameter"
    if name.startswith("<"):
        return f"parameter {int(name.rsplit(':', 1)[1].rstrip('>')) + 1}"
    return f"`{name}`"


def _join(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + f" and {items[-1]}"
