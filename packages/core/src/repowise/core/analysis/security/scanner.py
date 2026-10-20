"""Source scan orchestration: ``scan_source`` and ``scan_source_map``."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from repowise.core.analysis.secret_allowlist import secret_path_allowlist
from repowise.core.code_origin import code_origin

from .calls import _call_findings, _tls_verify_off_lines
from .credentials import (
    _PEM_BODY_LINE,
    _PEM_HEADER_LINE,
    _assignment_name,
    _is_secret_value,
    _redaction,
    _snippet,
    _value_span,
)
from .masking import mask_comments_and_strings, mask_dialect, source_lines
from .paths import (
    _PROSE_EXTENSIONS,
    _holds_no_written_code,
    _is_low_for_path,
    _is_low_severity_path,
)
from .patterns import (
    _ANY_PATTERN,
    _CALL_KINDS,
    _JS_ONLY_KINDS,
    _KEYWORD_KINDS,
    _MASKED_KINDS,
    _PATTERNS,
    _SECRET_SPECIFICITY,
    _SPANNING_PATTERNS,
    _SYMBOL_KEYWORDS,
    SECRET_KINDS,
)
from .refinements import (
    _interpolates_sql_value,
    _is_html_type_annotation,
    _match_starting_in,
    _weak_hash_is_an_id,
)
from .suppression import IGNORE_TOKEN, _allowlisted_lines, marker_silences


def scan_source(file_path: str, source: str, symbols: Iterable[Any] = ()) -> list[dict]:
    """Scan *source* text and symbol names; return finding dicts. No I/O."""
    findings: list[dict] = []
    lines = source_lines(source)
    is_prose = file_path.lower().endswith(_PROSE_EXTENSIONS)
    origin = code_origin(file_path, source)
    secrets_only = is_prose or _holds_no_written_code(file_path, origin)

    # Masking is the costly step and most files match no pattern at all, so
    # each form is built once, on first use. Prose has no comments or strings
    # to set apart: its text is read as written.
    masks: dict[bool, tuple[str, list[str]]] = {}

    def mask(strings: bool = True) -> tuple[str, list[str]]:
        if strings not in masks:
            text = (
                source
                if is_prose
                else mask_comments_and_strings(
                    source, strings=strings, **mask_dialect(file_path)
                )
            )
            masks[strings] = (text, lines if is_prose else source_lines(text))
        return masks[strings]

    def masked_line(lineno: int, line: str, *, strings: bool = True) -> str:
        masked_lines = mask(strings)[1]
        return masked_lines[lineno - 1] if lineno <= len(masked_lines) else line

    if not secrets_only:
        findings.extend(_call_findings(file_path, source, lambda: mask()[0]))

    # Line-by-line pattern scan. A line's edges and the newline around it read
    # alike to every lookaround in ``_ANY_PATTERN``, so a file the whole-source
    # search rejects has no matching line, and most files are such.
    is_low_sev_file = _is_low_severity_path(file_path)
    is_python = file_path.lower().endswith((".py", ".pyi"))
    tls_lines: list[set[int] | None] = []
    scan_lines = lines if _ANY_PATTERN.search(source) else []
    for lineno, line in enumerate(scan_lines, start=1):
        if not _ANY_PATTERN.search(line):
            continue
        snippet: str | None = None
        secret_hits: list[tuple[dict, str, int]] = []
        for pattern, kind, severity in _PATTERNS:
            if kind in _CALL_KINDS:
                continue
            if secrets_only and kind not in SECRET_KINDS:
                continue
            if is_python and kind in _JS_ONLY_KINDS:
                continue
            if kind in _MASKED_KINDS:
                match = pattern.search(masked_line(lineno, line))
                if match and kind == "tls_verify_false" and is_python:
                    if not tls_lines:
                        tls_lines.append(_tls_verify_off_lines(source))
                    if tls_lines[0] is not None and lineno not in tls_lines[0]:
                        match = None
            elif kind == "fstring_sql":
                # The ``f`` must open a literal in code, not sit inside another
                # string, and the query must interpolate a value.
                match = _match_starting_in(pattern, line, masked_line(lineno, line))
                if match and not _interpolates_sql_value(line, match.start(), lines, lineno):
                    match = None
            elif kind == "weak_hash":
                # Written in code, and not a declared or visible id (refinements).
                match = _match_starting_in(pattern, line, masked_line(lineno, line))
                if match and _weak_hash_is_an_id(line, mask()[1], lineno, match.start()):
                    match = None
            elif kind in _KEYWORD_KINDS:
                # ``password = "..."`` in a comment or docstring is an example.
                match = _match_starting_in(pattern, line, masked_line(lineno, line))
            elif kind == "public_env_secret":
                # A comment naming the variable is not code reading it. Comments
                # alone: the name is often quoted (``ENV["NEXT_PUBLIC_KEY"]``).
                uncommented = masked_line(lineno, line, strings=False)
                match = _match_starting_in(pattern, line, uncommented)
            else:
                # Vendor key shapes are real wherever they sit, comments included.
                match = pattern.search(line)
                if match and kind == "unsafe_inner_html" and _is_html_type_annotation(line, match):
                    match = None
            if match:
                value_span = _value_span(kind, match, line) if match.groups() else (0, 0)
                value = line[slice(*value_span)] if match.groups() else ""
                if kind in SECRET_KINDS:
                    name = _assignment_name(line[: value_span[0]])
                    if not _is_secret_value(kind, value, name):
                        continue
                    if is_low_sev_file:
                        severity = "low"
                if snippet is None:
                    snippet = _snippet(line)
                finding = {
                    "kind": kind,
                    "severity": severity,
                    "snippet": snippet,
                    "line": lineno,
                }
                findings.append(finding)
                if kind in SECRET_KINDS:
                    secret_hits.append((finding, value, _SECRET_SPECIFICITY.get(kind, 2)))
        # One secret, one finding: the highest-ranked kind that saw it names it.
        # Either value can hold the other: a keyword's runs to the closing quote.
        for finding, value, rank in secret_hits:
            if any(r > rank and (o in value or value in o) for _, o, r in secret_hits):
                findings.remove(finding)

    # Calls that open on one line and set ``shell=True`` on a later one; reported
    # on the opening line, unless the per-line pass already did.
    pem_body_lines: set[int] = set()
    for pattern, kind, severity in _SPANNING_PATTERNS:
        if secrets_only and kind not in SECRET_KINDS:
            continue
        if kind in _MASKED_KINDS:
            # The masked pattern opens with this literal, which masking keeps
            # verbatim, so a source without it cannot match.
            if "subprocess." not in source:
                continue
            text = mask()[0]
        else:
            text = source
        for match in pattern.finditer(text):
            if kind in SECRET_KINDS:
                val = match.group(1) if match.groups() else ""
                if not _is_secret_value(kind, val):
                    continue
                if is_low_sev_file:
                    severity = "low"
            start_line = source.count("\n", 0, match.start()) + 1
            end_line = source.count("\n", 0, match.end()) + 1
            if kind == "private_key_pem":
                end = source.find("-----END", match.end())
                end_line = source.count("\n", 0, end if end != -1 else match.end()) + 1
                pem_body_lines.update(range(start_line + 1, end_line + (end == -1)))
            if any(f["kind"] == kind and f["line"] == start_line for f in findings):
                continue
            line_start = source.rfind("\n", 0, match.start()) + 1
            line_end = source.find("\n", match.start())
            if line_end == -1:
                line_end = len(source)
            findings.append(
                {
                    "kind": kind,
                    "severity": severity,
                    "snippet": _snippet(source[line_start:line_end]),
                    "line": start_line,
                    # The last line the match spans, so a diff-scoped reader
                    # counts an edit anywhere inside the call or key.
                    "end_line": end_line,
                }
            )

    # The base64 lines after a header that ends its line are body whether or not
    # the key validated: an indented YAML block or a key with no END line never
    # matches the pattern above, and its body lines would otherwise reach another
    # kind's snippet. The run stops at the first line that is not base64, so a
    # header constant in code that assembles PEM text marks nothing after it.
    in_body = False
    for lineno, line in enumerate(lines, start=1):
        if in_body and _PEM_BODY_LINE.search(line):
            pem_body_lines.add(lineno)
        else:
            in_body = bool(_PEM_HEADER_LINE.search(line))

    # A key body line matches nothing of its own, so a hit there is stray and its
    # snippet would carry key material: mask the whole line.
    for finding in findings:
        if finding["line"] in pem_body_lines:
            finding["snippet"] = _redaction(lines[finding["line"] - 1].strip())

    for finding in findings:
        if _is_low_for_path(finding["kind"], is_low_sev_file, origin):
            finding["severity"] = "low"

    # Symbol-name scan (informational / low). Machine output and declarations
    # hold no written code, so their names are not ours to flag.
    for sym in () if secrets_only else symbols:
        name = getattr(sym, "name", "") or getattr(sym, "qualified_name", "") or ""
        if name and _SYMBOL_KEYWORDS.search(name):
            findings.append(
                {
                    "kind": "security_sensitive_symbol",
                    "severity": "low",
                    "snippet": name,
                    "line": getattr(sym, "start_line", 0) or 0,
                }
            )

    allowed = _allowlisted_lines(file_path, source)
    return [f for f in findings if f["kind"] not in SECRET_KINDS or f["line"] not in allowed]


def _repo_root(file_info: Any) -> str | None:
    """The directory *file_info*'s repo-relative path is taken from, or ``None``."""
    rel = PurePosixPath(file_info.path)
    absolute = Path(getattr(file_info, "abs_path", "") or "")
    if not (rel.parts and absolute.is_absolute()):
        return None
    if absolute.parts[-len(rel.parts) :] != rel.parts:
        return None
    return str(absolute.parents[len(rel.parts) - 1])


def scan_source_map(
    parsed_files: Iterable[Any],
    source_map: Mapping[str, bytes | str],
) -> tuple[dict[str, list[dict]], list[str]]:
    """Return ``(findings_by_file, scanned_paths)`` for ``replace_findings``.

    A path missing from *source_map* still gets the symbol-name scan. Secret
    findings drop in a path the repository's own secret-scanner config
    allowlists; the root it is read from is the one the parsed files sit under.
    """
    findings_by_file: dict[str, list[dict]] = {}
    scanned_paths: list[str] = []
    allowlisted: Callable[[str], bool] | None = None
    for pf in parsed_files:
        path = pf.file_info.path
        if allowlisted is None:
            allowlisted = secret_path_allowlist(_repo_root(pf.file_info))
        raw = source_map.get(path, b"")
        if isinstance(raw, (bytes, bytearray)):
            source_text = raw.decode("utf-8", errors="replace")
        else:
            source_text = raw or ""
        scanned_paths.append(path)
        findings = scan_source(path, source_text, pf.symbols)
        if findings and allowlisted(path):
            findings = [f for f in findings if f["kind"] not in SECRET_KINDS]
        if findings and IGNORE_TOKEN in source_text:
            # The gate keeps a silenced finding to report it as suppressed; the
            # stored scan is what the dashboard and agents read, so it drops it.
            lines = source_lines(source_text)
            findings = [
                f
                for f in findings
                if not (0 < f["line"] <= len(lines) and marker_silences(lines[f["line"] - 1], f["kind"]))
            ]
        if findings:
            findings_by_file[path] = findings
    return findings_by_file, scanned_paths
