"""Lightweight security signal extractor.

Scans indexed symbols and source for keyword/regex patterns that indicate
authentication, secret handling, raw SQL, dangerous deserialization, etc.

Two scan surfaces share the same pattern registry and persistence layer:

* working-tree scans (during indexing) — ``scan_source`` / ``scan_source_map`` +
  ``replace_findings`` with no commit provenance;
* full-history scans (``repowise security scan --history``) — iterate every
  tracked revision of every source file and persist hits tagged with the
  introducing commit's SHA + author date.

Both paths land in the ``security_findings`` table. The
``(repository_id, file_path, kind, line_number, commit_sha)`` unique
constraint (migration 0041) makes re-runs idempotent.
"""

# Compatibility shim: the scanner lives in ``repowise.core.analysis.security``.
# Every name this module defined, private ones included, is re-exported here.
# ruff: noqa: F401

from __future__ import annotations

from repowise.core.analysis.security.calls import (
    _CP_MODULE,
    _CP_NAMED,
    _CP_NAMESPACE,
    _CP_REQUIRE_CALL,
    _EXEC_EXPORT,
    _HTTP_CALLS,
    _HTTP_LIBS,
    _JS_NAME,
    _call_findings,
    _child_process_exec_calls,
    _dotted,
    _http_bindings,
    _root,
    _tls_verify_off_lines,
)
from repowise.core.analysis.security.credentials import (
    _ANGLE_SLOT,
    _CONSTANT_NAME,
    _CREDENTIAL_EXACT_PLACEHOLDERS,
    _CREDENTIAL_SUBSTRING_PLACEHOLDERS,
    _KEY_NAME_VALUE,
    _LONG_DIGIT_RUN,
    _MARKER,
    _OWN_NAME_VALUE,
    _PEM_BODY_LINE,
    _PEM_HEADER_LINE,
    _PEM_INLINE_BODY,
    _PUBLIC_EMPTY_ASSIGNMENT,
    _PUBLIC_ENV_PATTERN,
    _PUBLIC_VALUE,
    _SCHEME_TEMPLATE_VALUE,
    _SECRET_PATTERNS,
    _SECRET_PREFILTER,
    _SNIPPET_MAX,
    _TEMPLATE_VALUE,
    _UPPER_KEY_NAME_VALUE,
    _assignment_name,
    _is_empty_public_env_assignment,
    _is_own_name_value,
    _is_plain_word,
    _is_secret_value,
    _is_valid_credential_value,
    _mask_line,
    _public_env_value,
    _redaction,
    _secret_spans,
    _snippet,
    _string_end,
    _value_span,
    masked_snippet,
)
from repowise.core.analysis.security.masking import (
    _BLOCK_STOP,
    _CODE_STOP,
    _NESTED_COMMENT_SUFFIXES,
    _NOT_NEWLINE,
    _RAW_BACKTICK_STOP,
    _RAW_BACKTICK_SUFFIXES,
    _STRING_STOP,
    _TEMPLATE_STOP,
    _block_comment_end,
    _template_stop,
    mask_comments_and_strings,
    mask_dialect,
    source_lines,
)
from repowise.core.analysis.security.paths import (
    _DECLARATION_SUFFIXES,
    _LOW_SEVERITY_PATH_TOKENS,
    _MACHINE_ORIGINS,
    _NON_SHIPPING_ORIGINS,
    _PROSE_EXTENSIONS,
    _holds_no_written_code,
    _is_low_for_path,
    _is_low_severity_path,
)
from repowise.core.analysis.security.patterns import (
    _ANY_PATTERN,
    _CALL_KINDS,
    _CALL_PATTERNS,
    _JS_ONLY_KINDS,
    _KEYWORD_KINDS,
    _MASKED_KINDS,
    _PATTERNS,
    _SECRET_SPECIFICITY,
    _SPANNING_PATTERNS,
    _SYMBOL_KEYWORDS,
    SECRET_KINDS,
    SECURITY_SCANNER_VERSION,
    SYMBOL_NAME_KINDS,
)
from repowise.core.analysis.security.refinements import (
    _NOT_FOR_SECURITY,
    _SQL_FROM,
    _SQL_VALUE,
    _STRING_CONTINUATION,
    _TS_TYPE_VALUE,
    _TYPE_OPENER,
    _call_text,
    _interpolates_sql_value,
    _is_html_type_annotation,
    _match_starting_in,
)
from repowise.core.analysis.security.scanner import (
    _repo_root,
    scan_source,
    scan_source_map,
)
from repowise.core.analysis.security.store import (
    SecurityScanner,
    _is_missing_table_error,
    logger,
)
from repowise.core.analysis.security.suppression import (
    _ALLOWLIST_NEXT_RE,
    _ALLOWLIST_SAME_RE,
    _ALLOWLIST_TOKENS,
    _IGNORE_RE,
    IGNORE_TOKEN,
    _allowlisted_lines,
    marker_silences,
)
