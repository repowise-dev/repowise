"""Secret value validation, redaction and masked snippets."""

from __future__ import annotations

import re
from collections.abc import Iterable

from .patterns import _KEYWORD_KINDS, _PATTERNS, SECRET_KINDS

_CREDENTIAL_EXACT_PLACEHOLDERS: frozenset[str] = frozenset({"password", "changeit"})

_CREDENTIAL_SUBSTRING_PLACEHOLDERS: tuple[str, ...] = (
    "example",
    "changeme",
    "placeholder",
    "dummy",
    "sample",
    "fake",
    "xxx",
    "your_",
    "your-",
    "...",
    "\u2026",  # a typographic ellipsis
    "fixture",
    "redacted",
)


# An angle-bracket slot (``sk-<your key>``) is a template, wherever it sits.
_ANGLE_SLOT = re.compile(r"<[^<>]*>")


def _is_valid_credential_value(val: str) -> bool:
    """True when *val* is at least 8 chars and not a known placeholder."""
    if len(val) < 8:
        return False
    v = val.lower().strip()
    if v.startswith("<") or _ANGLE_SLOT.search(v) or v in _CREDENTIAL_EXACT_PLACEHOLDERS:
        return False
    if len(set(v)) == 1 and not v.isalnum():
        return False
    return not any(p in v for p in _CREDENTIAL_SUBSTRING_PLACEHOLDERS)


# A snake_case or kebab-case name (``x-api-key``, ``repowise-security-ignore``)
# is a key's or marker's name, a constant holding its own name, and a template
# placeholder or shell substitution is filled in when it runs; none of them is a
# credential. Letters only: a digit or capital makes it look like a key.
# ``{!Contact.Name}`` is a Salesforce merge field (Visualforce, formulas, flows,
# email templates) and ``{#v.value}`` an Aura unbound expression.
_TEMPLATE_VALUE = re.compile(r"\{\{.*?\}\}|\$\{.*?\}|\$\(.*?\)|\{[!#].*?\}")
_SCHEME_TEMPLATE_VALUE = re.compile(
    r"(?i:(?:bearer|basic|token))\s+(?:\{\{.*?\}\}|\$\{.*?\}|\$\(.*?\))"
)
# A character range with a quantifier is a regular expression (``(_D[0-9]+D_)``).
_REGEX_VALUE = re.compile(r"\[\^?[^\]]*\w-\w[^\]]*\][+*?{]")
_OWN_NAME_VALUE = re.compile(r"[A-Za-z_-]+")
_CONSTANT_NAME = re.compile(r"[A-Z][A-Z0-9_]*")
_KEY_NAME_VALUE = re.compile(r"[a-z]+(?:[_-][a-z]+)+|[a-z_]*_[a-z_]*")
# Uppercase env-var names and sentinels can have short version numbers. Four
# consecutive digits are credential-shaped, so keep those values reportable.
_UPPER_KEY_NAME_VALUE = re.compile(r"[A-Z_][A-Z0-9_]*")
_LONG_DIGIT_RUN = re.compile(r"[0-9]{4,}")


def _is_own_name_value(value: str, name: str) -> bool:
    """Whether a letter-only constant holds its own name or a suffix of it."""
    if not _OWN_NAME_VALUE.fullmatch(value):
        return False
    normalized_name = re.sub(r"[_-]", "", name).lower()
    normalized_value = re.sub(r"[_-]", "", value).lower()
    return bool(normalized_name) and normalized_name.endswith(normalized_value)


def _assignment_name(prefix: str) -> str:
    """Name assigned to the literal immediately following *prefix*."""
    assignment = re.search(r"([A-Za-z_][A-Za-z0-9_-]*)\s*=\s*['\"]?$", prefix)
    return assignment.group(1) if assignment else ""


def _is_secret_value(kind: str, val: str, name: str = "") -> bool:
    """True when *val*, captured by a *kind* pattern, looks like a real credential."""
    if not _is_valid_credential_value(val):
        return False
    if kind == "git_url_credentials":
        # ``ci:deploykey@``, ``user:pass1234@``: a word, not a token, and
        # ``USER:PERSONAL_ACCESS_TOKEN@`` or ``user:glpat-secret@`` names one. A
        # real token is long and mixed, or short and mixes three of lower,
        # upper, digits and symbols.
        if (
            _is_plain_word(val)
            or _CONSTANT_NAME.fullmatch(val)
            or _KEY_NAME_VALUE.fullmatch(val)
        ):
            return False
        classes = (str.islower, str.isupper, str.isdigit, lambda c: not c.isalnum())
        mixed = sum(any(map(f, val)) for f in classes)
        return mixed >= 3 or (mixed >= 2 and len(val) >= 12)
    if kind not in _KEYWORD_KINDS:
        return True
    value = val.strip()
    if kind == "hardcoded_secret" and (
        _KEY_NAME_VALUE.fullmatch(value)
        or (
            "_" in value
            and _UPPER_KEY_NAME_VALUE.fullmatch(value)
            and not _LONG_DIGIT_RUN.search(value)
        )
    ):
        return False
    if _is_own_name_value(value, name):
        return False
    # A leading ``--`` is a CSS custom property or a CLI flag, not a key.
    return not (
        _TEMPLATE_VALUE.search(value)
        or _SCHEME_TEMPLATE_VALUE.search(value)
        or _REGEX_VALUE.search(value)
        or _is_plain_word(value)
        or value.startswith("--")
    )


def _is_plain_word(value: str) -> bool:
    """A dictionary-shaped word: the dummy a client insists on (``api_key="lmstudio"``).

    Bounded, because a long run of letters in mixed or single case is as random
    as any key.
    """
    words = value.split(" ")
    single_case = all(word.islower() or word.isupper() or word.istitle() for word in words)
    return (
        1 <= len(words) <= 4
        and all(word.isalpha() for word in words)
        and single_case
        and len(value) <= 20
    )


def _redaction(val: str) -> str:
    return (val[:4] + "****") if len(val) >= 4 else "****"


# ``public_env_secret`` has no capture group, so its value is found here. In
# ``SECRET_KINDS`` the empty capture would fail validation and drop the finding.
# After the name: the name's own closing quote or bracket, then an operator
# (``=``, ``:``, ``=>``, ``??``, ``||``) or plain whitespace (a Dockerfile
# ``ENV NAME value``), then the value, quoted or not.
_PUBLIC_VALUE = re.compile(r"""\A['"\]]*(?:\s*[:=>?|]+\s*|\s+)(['"`]?)(?=[^\s;,)])""")
# Blank template lines (``VITE_API_TOKEN=``, ``NEXT_PUBLIC_KEY=""``, or with
# trailing comment) in .env or config files carry no secret.
_PUBLIC_EMPTY_ASSIGNMENT = re.compile(
    r"""\A['"\]]*\s*(?:=(?!=)|:=|:|=>)\s*(?:""|'')?\s*(?:[,;])?\s*(?:#.*)?\Z"""
)

_SECRET_PATTERNS = [(p, kind) for p, kind, _ in _PATTERNS if kind in SECRET_KINDS]
_PUBLIC_ENV_PATTERN = next(p for p, kind, _ in _PATTERNS if kind == "public_env_secret")
# A PEM body written on its header's line (escaped ``\n`` breaks, or spaces when
# an environment variable flattened it), up to ``-----END``.
_PEM_INLINE_BODY = re.compile(
    r"(?i:-----BEGIN[ A-Z0-9_-]{0,100}PRIVATE KEY-----)(?:\\[rn])*"
    r"((?:[A-Za-z0-9+/=]|\\[rn]|[ \t])+)"
)
# A header that ends its line: the body follows on the next lines.
_PEM_HEADER_LINE = re.compile(r"(?i:-----BEGIN[ A-Z0-9_-]{0,100}PRIVATE KEY-----)['\"]?\s*\Z")
# A line of key body: one base64 run, however indented.
_PEM_BODY_LINE = re.compile(r"\A\s*[A-Za-z0-9+/=]{16,}\s*\Z")
_SECRET_PREFILTER = re.compile(
    "|".join(
        f"(?:{p.pattern})"
        for p in [*(p for p, _ in _SECRET_PATTERNS), _PUBLIC_ENV_PATTERN, _PEM_INLINE_BODY]
    )
)

_SNIPPET_MAX = 120
_MARKER = "****"


def _string_end(line: str, start: int) -> int:
    """Where the string literal holding *start* closes, else the end of *line*.

    The keyword captures stop at the first quote of either kind, so a value
    holding the other quote would otherwise be masked only up to it.
    """
    quote = line[start - 1] if start > 0 else ""
    i = start
    while i < len(line):
        if line[i] == "\\":
            i += 2
        elif line[i] == quote:
            return i
        else:
            i += 1
    return len(line)


def _value_span(kind: str, match: re.Match, line: str) -> tuple[int, int]:
    """The captured credential's span; a keyword value runs to its closing quote."""
    if kind == "public_env_secret":
        return _public_env_value(line, match.end()) or (0, 0)
    if not match.groups():
        return (0, 0)
    start, end = match.span(1)
    return (start, _string_end(line, start)) if kind in _KEYWORD_KINDS else (start, end)


def _public_env_value(line: str, name_end: int) -> tuple[int, int] | None:
    """The value assigned to a public env name ending at *name_end*, if any."""
    value = _PUBLIC_VALUE.search(line[name_end:])
    if value is None:
        return None
    start = name_end + value.end()
    if value.group(1):
        end = _string_end(line, start)
        return (start, end) if end > start else None
    stop = next((i for i in range(start, len(line)) if line[i].isspace()), len(line))
    return (start, stop) if stop > start else None


def _is_empty_public_env_assignment(line: str, name_end: int) -> bool:
    """Whether the public env name ending at *name_end* is followed by an empty assignment."""
    return _PUBLIC_EMPTY_ASSIGNMENT.search(line[name_end:]) is not None


def _secret_spans(line: str) -> list[tuple[int, int]]:
    """Every credential value on *line*, including repeats of one elsewhere on it."""
    spans: list[tuple[int, int]] = []
    for pattern, kind in _SECRET_PATTERNS:
        for match in pattern.finditer(line):
            start, end = _value_span(kind, match, line)
            if _is_secret_value(kind, line[start:end]):
                spans.append((start, end))
    for match in _PEM_INLINE_BODY.finditer(line):
        if _is_valid_credential_value(match.group(1)):
            spans.append(match.span(1))
    for match in _PUBLIC_ENV_PATTERN.finditer(line):
        if (span := _public_env_value(line, match.end())) is not None:
            spans.append(span)
    for val in {line[start:end] for start, end in spans}:
        if len(val) < 4:
            continue
        at = line.find(val)
        while at != -1:
            spans.append((at, at + len(val)))
            at = line.find(val, at + 1)
    return spans


def _mask_line(line: str, extra: Iterable[tuple[int, int]] = ()) -> str:
    spans = list(extra)
    if _SECRET_PREFILTER.search(line):
        spans += _secret_spans(line)
    if not spans:
        return line
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    out: list[str] = []
    pos = 0
    for start, end in merged:
        out.append(line[pos:start])
        out.append(_redaction(line[start:end]))
        pos = end
    out.append(line[pos:])
    return "".join(out)


def masked_snippet(line: str, spans: Iterable[tuple[int, int]] = ()) -> str:
    """:func:`_snippet` with the ``(start, end)`` *spans* of *line* masked as secrets too.

    For secret shapes defined outside the registry. A line that reads as PEM
    key body is masked whole, as the scan does for body lines.
    """
    if _PEM_BODY_LINE.search(line):
        return _redaction(line.strip())
    return _snippet(line, spans)


def _snippet(line: str, extra: Iterable[tuple[int, int]] = ()) -> str:
    """*line* as stored: every secret on it masked, then stripped and trimmed.

    Masking comes first because a value cut by the trim no longer matches.
    """
    text = _mask_line(line, extra).strip()
    cut = _SNIPPET_MAX
    if len(text) > cut:
        # Never end inside a marker: a bare ``*`` run defeats line verification.
        marker = text.rfind(_MARKER, 0, cut + len(_MARKER) - 1)
        if marker != -1 and marker < cut < marker + len(_MARKER):
            cut = marker
    return text[:cut]
