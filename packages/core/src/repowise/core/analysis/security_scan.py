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

from __future__ import annotations

import ast
import logging
import re
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.secret_allowlist import secret_path_allowlist
from repowise.core.code_origin import code_origin
from repowise.core.support_paths import DOC_EXTENSIONS
from repowise.core.test_paths import is_test_related_path

logger = logging.getLogger(__name__)

# Bump whenever a change alters what the scanner reports: a new/removed
# pattern, a changed severity, a precision fix to an existing rule. ``update``
# compares this against the value stamped in state.json and, on a mismatch,
# rescans every tracked working-tree file instead of only the changed ones,
# the same way ``HEALTH_ANALYZER_VERSION`` forces a full health re-score
# (#3072). Without this, a scanner fix only ever reaches a file a user
# happens to edit.
SECURITY_SCANNER_VERSION = 3

# The inline markers other secret scanners honour, in a comment on the
# finding's line: detect-secrets, gitleaks, TruffleHog, Bandit and gosec
# ``nosec``, and Ruff's hard-coded-password codes. detect-secrets also has a
# marker for the line below.
_ALLOWLIST_SAME_RE = re.compile(
    r"(?:#|//|/\*)[ \t]*(?:pragma:[ \t]*allowlist[ \t]+secret|gitleaks:allow|trufflehog:ignore"
    r"|nosec|noqa:[ \t\w,]*\bS10[5-7])\b"
)
_ALLOWLIST_NEXT_RE = re.compile(
    r"(?:#|//|/\*)[ \t]*pragma:[ \t]*allowlist[ \t]+nextline[ \t]+secret\b"
)
_ALLOWLIST_TOKENS = ("allowlist", "gitleaks:allow", "trufflehog:ignore", "nosec", "noqa:")


def _allowlisted_lines(file_path: str, source: str) -> set[int]:
    """Line numbers an allow marker in a comment (not a string) covers.

    Prose has no comments to set apart, so a marker anywhere on its line counts.
    """
    if not any(token in source for token in _ALLOWLIST_TOKENS):
        return set()
    uncommented = (
        None
        if file_path.lower().endswith(_PROSE_EXTENSIONS)
        else mask_comments_and_strings(source, strings=False, **mask_dialect(file_path))
    )
    allowed: set[int] = set()
    for pattern, below in ((_ALLOWLIST_SAME_RE, 0), (_ALLOWLIST_NEXT_RE, 1)):
        for m in pattern.finditer(source):
            if uncommented is None or uncommented[m.start() : m.end()] != m.group():
                allowed.add(source.count("\n", 0, m.start()) + 1 + below)
    return allowed


IGNORE_TOKEN = "repowise-security-ignore"
_IGNORE_RE = re.compile(
    rf"{IGNORE_TOKEN}(?![\w-])"
    r"(?:[ \t]*:[ \t]*([\w:-]+(?:[ \t]*,[ \t]*[\w:-]+)*))?"
    # A colon no kind list follows is a malformed scope, which silences nothing.
    r"(?P<stray>[ \t]*:)?"
)


def marker_silences(line: str, kind: str) -> bool:
    """Whether a marker on *line* silences *kind*: the bare token all kinds, ``: a, b`` those."""
    if IGNORE_TOKEN not in line:
        return False
    match = _IGNORE_RE.search(line)
    if match is None or match.group("stray"):
        return False
    if not match.group(1):
        return True
    return kind in {k.strip() for k in match.group(1).split(",")}


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

# Broader than ``is_test_related_path`` on purpose. That one asks whether a file
# is a test and keeps bare ``fixtures/``, ``mocks/`` and ``spec/`` as production
# outside a test tree; this asks whether a match there is probably fake, and a
# ``high`` that is only probably real is not one to raise.
_LOW_SEVERITY_PATH_TOKENS: frozenset[str] = frozenset(
    {
        "test",
        "tests",
        "__tests__",
        "__test__",
        "fixtures",
        "__fixtures__",
        "spec",
        "specs",
        "mock",
        "mocks",
        "__mocks__",
        "example",
        "examples",
    }
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


def _is_low_severity_path(file_path: str) -> bool:
    """True when *file_path* is test material or under fixture, spec, mock, or example directories."""
    posix_path = file_path.replace("\\", "/")
    parts = [p.lower() for p in PurePosixPath(posix_path).parts]
    return any(p in _LOW_SEVERITY_PATH_TOKENS for p in parts) or is_test_related_path(posix_path)


# Code that runs at build or release time, or only in docs: a sink there is not
# a shipped one.
_NON_SHIPPING_ORIGINS = frozenset({"tooling", "docs_example"})
# A bundler's or generator's output, or a copied library: nobody here wrote its
# calls, so only a secret it carries is news.
_MACHINE_ORIGINS = frozenset({"generated", "vendored"})
_DECLARATION_SUFFIXES = (".d.ts", ".d.mts", ".d.cts")


def _holds_no_written_code(file_path: str, origin: str) -> bool:
    """Whether only secret kinds apply: machine output, or a TS declaration file, which never runs."""
    return origin in _MACHINE_ORIGINS or file_path.lower().endswith(_DECLARATION_SUFFIXES)


def _is_low_for_path(kind: str, is_low_sev_file: bool, origin: str) -> bool:
    """Test and fixture material never carries a shipped sink, whatever the kind.

    Nor do scripts, tools or docs examples, though a secret there still leaks.
    """
    return is_low_sev_file or (origin in _NON_SHIPPING_ORIGINS and kind not in SECRET_KINDS)


# ---------------------------------------------------------------------------
# Pattern registry: (compiled_pattern, kind_label, severity)
# ---------------------------------------------------------------------------
_CALL_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (
        re.compile(r"(?<![\w$])(?:[A-Za-z_$][\w$]*\s*\.\s*)*eval\s*\("),
        "eval_call",
        "high",
    ),
    (
        re.compile(r"(?<![\w$])(?:[A-Za-z_$][\w$]*\s*\.\s*)*exec\s*\("),
        "exec_call",
        "high",
    ),
]
_CALL_KINDS = frozenset(kind for _, kind, _ in _CALL_PATTERNS)

# ``exec`` is not a global in JavaScript; the name belongs to
# ``RegExp.prototype.exec``. The receiver-chain prefix above therefore matches
# ``re.exec(str)``, ``/x/.exec(s)`` and ``pattern.exec(xml)`` — idiomatic parsing
# code, reported at ``high``. Measured on a 17-repo TypeScript corpus, every
# ``exec_call`` hit outside Python was a regex match and none was a process
# spawn, so the kind was pure noise on those languages.
#
# The dangerous call in JavaScript comes from ``child_process``, so only a call
# through a name bound to that module counts: a namespace (``cp.exec(``), a named
# import (``exec(`` after ``import { exec } from "child_process"``) or the
# ``require`` result itself. Only ``exec`` and ``execSync`` run a shell;
# ``execFile`` passes its arguments as argv, like a Python ``subprocess.run``
# without ``shell=True``, which does not fire either. A file's own function named ``exec``, or a regex's
# ``.exec`` next to an unrelated ``spawn`` import, is not a shell. ``eval`` needs
# no such gate: it is a genuine global there.
_CP_MODULE = r"""['"](?:node:)?child_process['"]"""
_JS_NAME = r"[A-Za-z_$][\w$]*"
_CP_NAMESPACE = re.compile(
    rf"import\s+(?:\*\s*as\s+)?({_JS_NAME})\s*(?:,\s*\{{[^}}]*\}}\s*)?from\s*{_CP_MODULE}"
    rf"|(?:const|let|var)\s+({_JS_NAME})\s*=\s*require\(\s*{_CP_MODULE}\s*\)"
)
_CP_NAMED = re.compile(
    rf"import\s*(?:{_JS_NAME}\s*,\s*)?\{{([^}}]*)\}}\s*from\s*{_CP_MODULE}"
    rf"|(?:const|let|var)\s*\{{([^}}]*)\}}\s*=\s*require\(\s*{_CP_MODULE}\s*\)"
)
_CP_REQUIRE_CALL = re.compile(
    rf"require\(\s*{_CP_MODULE}\s*\)\s*\.\s*exec(?:Sync)?\s*\("
)
_EXEC_EXPORT = re.compile(r"exec(?:Sync)?")


def _child_process_exec_calls(source: str, masked: str) -> list[int]:
    """Offsets of ``child_process`` exec-family calls, found through the file's own bindings.

    Imports are read from raw source because the module name is a string
    literal, which masking blanks; calls are read from masked source, so a
    comment or string naming one does not count.
    """
    namespaces = {n for m in _CP_NAMESPACE.finditer(source) for n in m.groups() if n}
    functions: set[str] = set()
    for m in _CP_NAMED.finditer(source):
        for item in (m.group(1) or m.group(2)).split(","):
            # ``exec as run`` (import) and ``exec: run`` (destructuring) rename.
            parts = re.split(r"\s+as\s+|\s*:\s*", item.strip().removeprefix("type "))
            if parts[0] and _EXEC_EXPORT.fullmatch(parts[0]):
                functions.add(parts[-1].strip())
    offsets = [m.start() for m in _CP_REQUIRE_CALL.finditer(source) if not masked[m.start()].isspace()]
    names = [re.escape(n) for n in namespaces]
    if names:
        receiver = rf"(?<![\w$.])(?:{'|'.join(names)})\s*\.\s*exec(?:Sync)?\s*\("
        offsets += [m.start() for m in re.finditer(receiver, masked)]
    if functions:
        bare = rf"(?<![\w$.])(?:{'|'.join(map(re.escape, functions))})\s*\("
        offsets += [m.start() for m in re.finditer(bare, masked)]
    return sorted(offsets)

_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    *_CALL_PATTERNS,
    # Not ``pickle.loads(pickle.dumps(x))``: a round trip of the program's own
    # object, which is how a copy or a picklability test is written.
    (re.compile(r"pickle\.loads(?!\s*\(\s*pickle\.dumps\s*\()"), "pickle_loads", "high"),
    (re.compile(r"subprocess\..*shell\s*=\s*True"), "subprocess_shell_true", "high"),
    (re.compile(r"os\.system"), "os_system", "high"),
    # Case-insensitive: a credential pinned in source is usually written as a
    # SCREAMING_CASE constant assigned a quoted literal, and the lowercase-only
    # patterns walked straight past that form. Found by scanning a corpus in
    # which a live n8n key sat unreported under exactly that spelling.
    #
    # Case-insensitivity is written as a scoped inline group rather than the
    # ``re.IGNORECASE`` flag on purpose: ``_ANY_PATTERN`` below is built by
    # concatenating these patterns' *source text*, which drops per-pattern
    # flags. A flag here would leave the prefilter case-sensitive and it would
    # reject the line before the pattern ever ran.
    (re.compile(r"(?i:password)\s*=\s*['\"]([^'\"]*)"), "hardcoded_password", "high"),
    (
        re.compile(r"(?i:api_?key|secret|token|access_?key)\s*=\s*['\"]([^'\"]*)"),
        "hardcoded_secret",
        "high",
    ),
    # Value-shape patterns for common vendor credential formats (gitleaks'
    # rule set, MIT-licensed, is the reference for these shapes). These fire
    # regardless of the variable name a key is assigned to, so a vendor key
    # bound to an unlisted name (``client_id``) or passed inline is still
    # caught. Each carries a capture group around the credential value itself
    # so the ``SECRET_KINDS`` gate below (length + placeholder check) applies
    # to it the same as the keyword patterns above.
    (re.compile(r"\b((?:AKIA|ASIA)[A-Z2-7]{16})\b"), "aws_access_key", "high"),
    (
        re.compile(r"\b(gh[oprsu]_[0-9A-Za-z]{36}|github_pat_\w{82})\b"),
        "github_token",
        "high",
    ),
    # Slack writes the prefix in lowercase, at a token's start: ``xOxP-`` inside
    # a document id is not one.
    (re.compile(r"(?<![A-Za-z0-9])(xox[baprs]-[0-9a-zA-Z-]{10,72})"), "slack_token", "high"),
    # Bounded by lookarounds, not ``\b``: a key can end in ``-``, and a base64 run
    # (a lockfile ``sha512-`` integrity hash) must not yield one from its middle.
    (
        re.compile(r"(?<![0-9A-Za-z+/_-])(AIza[0-9A-Za-z_-]{35})(?![0-9A-Za-z_-])"),
        "google_api_key",
        "high",
    ),
    # Secret and restricted keys only: a publishable ``pk_`` key is public by design.
    (
        re.compile(r"\b((?:sk|rk)_(?:live|test|prod)_[0-9A-Za-z]{10,99})\b"),
        "stripe_key",
        "high",
    ),
    # GitLab's prefixed tokens: personal (also the routable form, whose payload
    # ends in ``.`` plus a 9-char version and checksum), deploy, runner,
    # pipeline trigger, OAuth app secret, incoming mail, CI job, and the legacy
    # runner registration token. Minimum lengths, since newer forms run longer.
    (
        re.compile(
            r"(?<![\w-])("
            r"glpat-[\w-]{20,300}(?:\.[0-9a-z]{9})?"
            r"|(?:gldt|glrt)-[\w-]{20,300}"
            r"|glptt-[0-9a-f]{40}"
            r"|gloas-[\w-]{64,300}"
            r"|glimt-[\w-]{25,300}"
            r"|glcbt-[0-9A-Za-z]{1,5}_[\w-]{20,300}"
            r"|GR1348941[\w-]{20,300}"
            r")(?![\w-])"
        ),
        "gitlab_token",
        "high",
    ),
    # The 84-char Azure DevOps PAT carries ``AZDO`` at a fixed offset. The legacy
    # 52-char lowercase base32 PAT has no marker and reads like any hash, so it
    # is left to ``hardcoded_secret``, which needs a key-named variable.
    (re.compile(r"\b([0-9A-Za-z]{76}AZDO[0-9A-Za-z]{4})\b"), "azure_devops_pat", "high"),
    # ``https://user:password@host/repo``, only on a git remote: a forge host or a
    # path ending in ``.git``. Database and broker URLs are left alone, since most
    # in source are local dev defaults. A password that opens a variable
    # (``$TOKEN``, ``${VAR}``, ``%VAR%``) or a slot (``<token>``, ``****``) is a
    # template. ``git@host:o/r`` and ``ssh://git@host`` have no ``user:`` part.
    (
        re.compile(
            r"(?i:\b(?:https?|git\+https|ssh)://)[^\s:/?#@'\"`<>]{1,100}:"
            r"(?![$%{<*])([^\s/?#@'\"`<>]{1,200})@"
            r"(?!(?i:localhost|127\.0\.0\.1|(?:[\w-]+\.)*example\.(?:com|org)|[\w.-]*\.local)"
            r"(?![\w.-]))"
            r"(?:(?i:(?:[\w-]+\.)*(?:github\.com|gitlab\.com|bitbucket\.org|dev\.azure\.com"
            r"|visualstudio\.com)|(?:[\w-]+\.)*gitlab(?:\.[\w-]+)+)(?![\w.-])"
            r"|[\w.-]+(?::\d+)?/[^\s'\"`<>?#]*\.git(?![\w-]))"
        ),
        "git_url_credentials",
        "high",
    ),
    (re.compile(r'f[\'"].*SELECT.*\{.*\}'), "fstring_sql", "med"),
    (re.compile(r"\.execute\(\s*[\'\"]\s*SELECT.*\+"), "concat_sql", "med"),
    (re.compile(r"verify\s*=\s*False"), "tls_verify_false", "med"),
    # A weak algorithm put to use, not the word: a call (``md5(``, ``hashlib.sha1(``,
    # ``DigestUtils.md5Hex(``, ``MD5_Init(``), a type or module function
    # (``MD5.Create()``, ``md5.Sum(``, ``Sha1::new()``, ``Digest::SHA1``), ``hashlib.md5``
    # handed over as a digest, or the algorithm named to a hashing call
    # (``createHash("md5")``, ``getInstance("SHA-1")``). A ``sha1`` variable
    # holding a commit id, or the word in a comment, is neither.
    (
        re.compile(
            r"(?<![\w$])(?i:md5|sha1)(?i:_?(?:hex|file|hash|digest|init|update|final))?\s*\("
            r"|(?<![\w$])(?i:md5|sha1)\s*(?:\.|::)\s*"
            r"(?:New|Sum|Create|new|hexdigest|digest|compute)\b"
            r"|\b(?:MD5|SHA1)(?:CryptoServiceProvider|Managed)\b"
            r"|\bDigest::(?:MD5|SHA1)\b"
            r"|\bhashlib\s*\.\s*(?:md5|sha1)\b"
            r"|\b(?:new|createHash|createHmac|getInstance|hash|hash_hmac|hash_file|digest|Create)"
            r"\s*\(\s*['\"](?i:md5|sha-?1)['\"]"
            r"|\bdigestmod\s*=\s*['\"](?i:md5|sha1)['\"]"
        ),
        "weak_hash",
        "low",
    ),
    # -- JS/TS patterns (#1935 Tier 1) -------------------------------------
    # Measured on the same 17-repo, 1109-file corpus as the exec_call/secret
    # fixes in #1947. Three of these five needed tightening before they were
    # shippable; see docs/layers/SECURITY.md and the PR body for the
    # first-cut vs. after-tightening counts per pattern.
    #
    # ``__html: <value>`` is the React ``dangerouslySetInnerHTML`` shape. A
    # pinned string literal there is inert; the interesting case is a value
    # that is an identifier, a member access or a call. The value test has to
    # be stated positively (an identifier/`$`/`(` lead character) rather than
    # as a negative lookahead excluding quotes: after the variable-width
    # `\s*` before it, a negative lookahead is tried at the position *before*
    # the whitespace, where "not a quote" trivially succeeds and a string
    # literal slips through anyway. Severity is ``med``, not ``high``: on the
    # measured corpus every hit was a name reference to a source-pinned
    # constant (a stylesheet string assigned to a module-level name), which
    # this test cannot distinguish from a genuinely dynamic value without
    # dataflow, so the pattern is a places-to-read signal rather than a
    # confirmed sink. A type annotation (``__html: string``) is dropped by
    # ``_is_html_type_annotation``, which needs the syntax around it.
    (
        re.compile(r"__html\s*:\s*(?=[A-Za-z_$(])"),
        "unsafe_inner_html",
        "med",
    ),
    # Analogue of ``fstring_sql`` for a JS/TS template literal. The bare verb
    # `SELECT` or `UPDATE` is an ordinary English word and fires on prose
    # (`` `Order update failed: ${status}` ``) and even on class names
    # (`select-none`), so each verb needs its companion clause —
    # `SELECT`..`FROM`, `UPDATE`..`SET` — before an interpolation counts.
    (
        re.compile(r"`[^`\n]*\b(?:SELECT\b[^`\n]*\bFROM|UPDATE\b[^`\n]*\bSET)\b[^`\n]*\$\{"),
        "template_literal_sql",
        "med",
    ),
    # A secret-shaped name exposed through a `NEXT_PUBLIC_`/`VITE_` prefix
    # ships straight into the client bundle. The prefix needing the most care
    # against legitimate public config: an `..._ANON_...` name (a Supabase
    # anon key, public by design and meant to be paired with RLS) is excluded
    # rather than flagged, since that class made up most of the corpus noise.
    (
        re.compile(
            r"\b(?:NEXT_PUBLIC|VITE)_(?!\w*ANON)[A-Z0-9_]*"
            r"(?:API_?KEY|SECRET|TOKEN|PASSWORD)[A-Z0-9_]*\b"
        ),
        "public_env_secret",
        "high",
    ),
    (re.compile(r"\bnew\s+Function\s*\("), "new_function_call", "high"),
    # Analogue of ``tls_verify_false`` for Node's https/tls agent options.
    (re.compile(r"rejectUnauthorized\s*:\s*false"), "reject_unauthorized_false", "med"),
]

# Combined prefilter: one search per line rejects the (overwhelmingly common)
# clean lines before the per-pattern loop runs. Matches iff some pattern that
# loop runs matches, so findings are unchanged. Call kinds are found by
# ``_call_findings`` instead, and searching for them here cost a third of it.
_ANY_PATTERN = re.compile(
    "|".join(f"(?:{p.pattern})" for p, kind, _ in _PATTERNS if kind not in _CALL_KINDS)
)

# Patterns whose calls legitimately span multiple physical lines: the opening
# ``subprocess.<call>(`` lands on one line and ``shell=True`` on another. The
# per-line loop can never see such a call (``.*`` stops at the newline), so it
# gets an extra whole-source pass.
#
# Continuation is restricted to the same physical line or a newline that is
# followed by indentation (``(?:[^\n]|\n(?=[ \t]))``), so a closed
# ``subprocess.run(...)`` cannot jump to a later ``os.popen(..., shell=True)``
# on a column-0 line. The span is also capped (~200 chars) as a second bound.
_SPANNING_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (
        re.compile(r"subprocess\.[A-Za-z]+\((?:[^\n]|\n(?=[ \t])){0,200}?shell\s*=\s*True"),
        "subprocess_shell_true",
        "high",
    ),
    # A PEM header alone is not a leak: code that assembles PEM text
    # (``"-----BEGIN " + kind + " PRIVATE KEY-----"``) contains the header
    # string without ever holding key material. Requiring a base64-looking
    # body line right after the header is what tells the two apart, and the
    # capture group around that body line is the value the SECRET_KINDS gate
    # below checks (length + placeholder), same as every other secret kind.
    # The break may be an escaped ``\n``: JSON and .env files hold the key on one line.
    (
        re.compile(
            r"(?i:-----BEGIN[ A-Z0-9_-]{0,100}PRIVATE KEY-----)(?:\r?\n|\\n)([A-Za-z0-9+/=]{20,})"
        ),
        "private_key_pem",
        "high",
    ),
]

# Symbol names that are informational security hotspots. Not ``session`` or
# ``token``: a symbol named only that is a database session, a pytest fixture or
# a lexer token far more often than auth code.
_SYMBOL_KEYWORDS = re.compile(r"\b(auth|password|jwt|crypto)\b", re.IGNORECASE)

# Kinds that name a dangerous call. They are matched against source with
# comments and string literals blanked, so a docstring, a comment or a regex
# literal that merely mentions the call does not fire.
_MASKED_KINDS = frozenset(
    {
        "pickle_loads",
        "subprocess_shell_true",
        "os_system",
        "new_function_call",
        "tls_verify_false",
        "reject_unauthorized_false",
    }
)

# JavaScript sinks. Python has no JSX, template literals, ``new Function`` or
# Node TLS options, and no bundler reads a ``.py`` file into a client, so a
# Python file holding one of these is quoting it, most often as a test fixture.
_JS_ONLY_KINDS = frozenset(
    {
        "unsafe_inner_html",
        "template_literal_sql",
        "new_function_call",
        "reject_unauthorized_false",
        "public_env_secret",
    }
)

# ``verify=False`` turns TLS off only when an HTTP client receives it, so the
# file must import an HTTP client library, and a call through a plain name must
# go through that import or a value built from it (``s = requests.Session()``).
# Any other function is free to name a keyword ``verify``. Ceiling: a project's
# own wrapper that forwards ``verify``, imported from another module, is not seen.
_HTTP_LIBS = frozenset(
    {"requests", "httpx", "aiohttp", "urllib3", "boto3", "botocore", "aiobotocore", "aioboto3"}
)
_HTTP_CALLS = frozenset(
    {
        "get",
        "post",
        "put",
        "patch",
        "delete",
        "head",
        "options",
        "request",
        "send",
        "stream",
        "urlopen",
        "Client",
        "AsyncClient",
        "Session",
        "HTTPTransport",
        "AsyncHTTPTransport",
        "client",
        "resource",
    }
)

# ``fstring_sql`` asks for a query (``SELECT`` with its ``FROM``) that
# interpolates a value where a bound parameter belongs: after a comparison or
# ``LIKE``. A table, a column, a fragment or a placeholder list cannot be bound,
# so building those into the text is the only way to write them, and a value
# interpolated there has no parameter fix to offer. Ceiling: ``IN ({ids})`` of
# joined values, and an identifier taken from a request, are not seen.
_SQL_FROM = re.compile(r"\bFROM\b")
_SQL_VALUE = re.compile(r"""(?:[=<>]|\b(?i:LIKE|ILIKE))\s*['"%]*\{(?!\{)""")
# A string literal continuing the one before it (implicit concatenation).
_STRING_CONTINUATION = re.compile(r"""\A\s*[rRbBfFuU]{0,2}['"]""")


def _interpolates_sql_value(line: str, start: int, lines: list[str], lineno: int) -> bool:
    """Whether the f-string query opening at *start* on *line* puts a value in its text.

    Only the literal is read, and the one opening the next line when the query
    runs on into it.
    """
    triple = line[start + 1 : start + 4]
    if triple in ('"""', "'''"):
        # A triple-quoted query runs to its closing delimiter, often lines on.
        text = "\n".join(lines[lineno - 1 : lineno + 29])
        end = text.find(triple, start + 4)
        query = text[text.find("SELECT", start) : end if end != -1 else len(text)]
        return bool(_SQL_FROM.search(query) and _SQL_VALUE.search(query))
    body = start + 2  # past ``f`` and the opening quote
    query = line[line.find("SELECT", start) : _string_end(line, body)]
    following = lines[lineno] if lineno < len(lines) else ""
    if (cont := _STRING_CONTINUATION.match(following)) is not None:
        query += "\n" + following[cont.end() : _string_end(following, cont.end())]
    return bool(_SQL_FROM.search(query) and _SQL_VALUE.search(query))


def _dotted(node: ast.AST) -> str:
    """``a.b.c`` for a name or attribute chain, else ``""``."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and (base := _dotted(node.value)):
        return f"{base}.{node.attr}"
    return ""


def _root(node: ast.AST) -> ast.AST:
    """The expression a chain of calls and attributes starts from."""
    while isinstance(node, (ast.Call, ast.Attribute, ast.Await)):
        node = node.func if isinstance(node, ast.Call) else node.value
    return node


def _http_bindings(tree: ast.AST) -> set[str]:
    """Names and ``self.x`` targets bound to an HTTP client library or a value built from it."""
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in _HTTP_LIBS:
                    bound.add(alias.asname or alias.name.split(".")[0])
        elif (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and (node.module or "").split(".")[0] in _HTTP_LIBS
        ):
            bound.update(alias.asname or alias.name for alias in node.names)
    if not bound:
        return bound
    # Twice, so a value built from a bound value (``c = s.client()``) is bound too.
    for _ in range(2):
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                pairs = [(t, node.value) for t in node.targets]
            elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
                pairs = [(node.target, node.value)]
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                pairs = [(i.optional_vars, i.context_expr) for i in node.items if i.optional_vars]
            else:
                continue
            for target, value in pairs:
                if _dotted(_root(value)) in bound and (name := _dotted(target)):
                    bound.add(name)
    return bound


def _tls_verify_off_lines(source: str) -> set[int] | None:
    """Lines where ``verify=False`` reaches an HTTP client; None when *source* does not parse."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    bound = _http_bindings(tree)
    if not bound:
        return set()
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                name, receiver = func.attr, _root(func.value)
                # A plain name must be bound; ``self.x`` and call results are
                # taken on trust once the file uses an HTTP client library.
                reaches = (
                    _dotted(receiver) in bound
                    or _dotted(func.value) in bound
                    or not isinstance(receiver, ast.Name)
                    or receiver.id in ("self", "cls")
                )
            elif isinstance(func, ast.Name):
                name, reaches = func.id, func.id in bound
            else:
                continue
            if name in _HTTP_CALLS and reaches:
                found.update(
                    kw.lineno
                    for kw in node.keywords
                    if kw.arg == "verify"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is False
                )
        elif (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and node.value.value is False
        ):
            # ``session.verify = False``, or a ``verify = False`` setting handed
            # to a client later.
            found.update(
                target.lineno
                for target in node.targets
                if (isinstance(target, ast.Attribute) and target.attr == "verify")
                or (isinstance(target, ast.Name) and target.id == "verify")
            )
    return found


# Python's own declaration that a digest is a checksum or a cache key, not a
# security control (``hashlib.sha1(data, usedforsecurity=False)``).
_NOT_FOR_SECURITY = re.compile(r"\busedforsecurity\s*=\s*False\b")


def _call_text(masked: list[str], lineno: int, start: int, limit: int = 600) -> str:
    """The code from *start* on line *lineno* through the first call's closing paren.

    Read from *masked* (strings and comments blanked, offsets kept), so a ``)``
    or the keyword inside a string neither ends the call nor counts as code.
    Bounded by *limit* characters.
    """
    code = "\n".join(masked[lineno - 1 : lineno + 9])[start : start + limit]
    depth = 0
    for i, char in enumerate(code):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth <= 0:
                return code[: i + 1]
    return code


# Prose is documentation, not executable code. Only secret kinds scan it,
# because a key pasted into a README is still a leak.
_PROSE_EXTENSIONS = tuple(DOC_EXTENSIONS)

# Patterns whose matches are genuine leaked credentials (as opposed to the
# broader "code smell" patterns like os.system/eval). Full-history scans
# default to this subset: a historical commit that *once* called eval() is
# mostly noise, whereas a committed secret is actionable and persists in
# history. This positions history mode as complementary to gitleaks /
# trufflehog rather than a noisy replacement.
SECRET_KINDS: frozenset[str] = frozenset(
    {
        "hardcoded_password",
        "hardcoded_secret",
        "aws_access_key",
        "github_token",
        "slack_token",
        "google_api_key",
        "stripe_key",
        "gitlab_token",
        "azure_devops_pat",
        "git_url_credentials",
        "private_key_pem",
    }
)

# Kinds whose ``snippet`` is a symbol *name* rather than the text of the line
# it sits on (see the symbol-name scan below). Serve-time line verification
# must not treat these like pattern snippets: a bare identifier recurs all over
# a file, so relocating on it lands somewhere arbitrary and failing to find it
# does not mean the code is gone.
SYMBOL_NAME_KINDS: frozenset[str] = frozenset({"security_sensitive_symbol"})


_KEYWORD_KINDS: frozenset[str] = frozenset({"hardcoded_password", "hardcoded_secret"})

# Which kind names a value several kinds saw; vendor shapes are the unlisted 2.
# ``token = "https://oauth2:glpat-...@host"`` matches all three tiers at once.
# ``git_url_credentials`` ranks lowest so it only adds findings: a keyword finding
# that kept its kind keeps its baseline fingerprint.
_SECRET_SPECIFICITY: dict[str, int] = {
    "git_url_credentials": 0,
    "hardcoded_password": 1,
    "hardcoded_secret": 1,
}

# A snake_case or kebab-case name (``x-api-key``, ``repowise-security-ignore``)
# is a key's or marker's name, a constant holding its own name, and a template
# placeholder or shell substitution is filled in when it runs; none of them is a
# credential. Letters only: a digit or capital makes it look like a key.
_TEMPLATE_VALUE = re.compile(r"\{\{.*?\}\}|\$\{.*?\}|\$\(.*?\)")
_SCHEME_TEMPLATE_VALUE = re.compile(
    r"(?i:(?:bearer|basic|token))\s+(?:\{\{.*?\}\}|\$\{.*?\}|\$\(.*?\))"
)
_OWN_NAME_VALUE = re.compile(r"[A-Za-z_-]+")
_CONSTANT_NAME = re.compile(r"[A-Z][A-Z0-9_]*")
_KEY_NAME_VALUE = re.compile(r"[a-z]+(?:[_-][a-z]+)+|[a-z_]*_[a-z_]*")


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
    if kind == "hardcoded_secret" and _KEY_NAME_VALUE.fullmatch(value):
        return False
    if _is_own_name_value(value, name):
        return False
    # A leading ``--`` is a CSS custom property or a CLI flag, not a key.
    return not (
        _TEMPLATE_VALUE.search(value)
        or _SCHEME_TEMPLATE_VALUE.search(value)
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
    start, end = match.span(1)
    return (start, _string_end(line, start)) if kind in _KEYWORD_KINDS else (start, end)


def _public_env_value(line: str, name_end: int) -> tuple[int, int] | None:
    """The value assigned to a public env name ending at *name_end*, if any."""
    value = _PUBLIC_VALUE.search(line[name_end:])
    if value is None:
        return None
    start = name_end + value.end()
    if value.group(1):
        return start, _string_end(line, start)
    stop = next((i for i in range(start, len(line)) if line[i].isspace()), len(line))
    return start, stop


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


# Where each masking state can change. Every other character keeps the state,
# so the scan jumps from one of these to the next instead of walking each one.
_CODE_STOP = re.compile(r"[{}/#'\"`]")
_TEMPLATE_STOP = re.compile(r"\$\{|`|\\")
_STRING_STOP = {
    marker: re.compile(re.escape(marker) + r"|\\") for marker in ("'", '"', "'''", '"""')
}
_NOT_NEWLINE = re.compile(r"[^\n]")
_BLOCK_STOP = re.compile(r"/\*|\*/")
_RAW_BACKTICK_STOP = re.compile("`")
# Languages whose block comments nest, and Go, whose backtick string is raw: a
# backslash there is a character, not an escape.
_NESTED_COMMENT_SUFFIXES = (".kt", ".kts", ".scala", ".sc", ".swift", ".rs", ".dart")
_RAW_BACKTICK_SUFFIXES = (".go",)


def mask_dialect(file_path: str) -> dict[str, bool]:
    """The :func:`mask_comments_and_strings` options *file_path*'s language needs."""
    lowered = file_path.lower()
    return {
        "nested_comments": lowered.endswith(_NESTED_COMMENT_SUFFIXES),
        "raw_backticks": lowered.endswith(_RAW_BACKTICK_SUFFIXES),
    }


def _block_comment_end(source: str, start: int, nested: bool) -> int:
    """Offset just past the ``*/`` closing the block comment opened before *start*."""
    if not nested:
        stop = source.find("*/", start)
        return len(source) if stop == -1 else stop + 2
    stop, depth = start, 1
    while depth and (m := _BLOCK_STOP.search(source, stop)):
        depth += 1 if m.group() == "/*" else -1
        stop = m.end()
    return len(source) if depth else stop


def _template_stop(raw_backticks: bool) -> re.Pattern[str]:
    return _RAW_BACKTICK_STOP if raw_backticks else _TEMPLATE_STOP


def mask_comments_and_strings(
    source: str,
    *,
    strings: bool = True,
    nested_comments: bool = False,
    raw_backticks: bool = False,
    hash_comments: bool = True,
) -> str:
    """Blank common comments (and strings, unless *strings* is false), keeping offsets.

    Strings are still tracked when they are kept, so a ``#`` or ``//`` inside
    one does not open a comment. Newlines are never blanked. *nested_comments*
    and *raw_backticks* come from :func:`mask_dialect`; *hash_comments* false
    keeps a ``#`` line, which is code in C, Rust, C# or Swift (``#include``).
    """
    out: list[str] = []
    copied = 0

    def blank(start: int, end: int) -> None:
        nonlocal copied
        out.append(source[copied:start])
        out.append(_NOT_NEWLINE.sub(" ", source[start:end]))
        copied = end

    size = len(source)
    i = 0
    state = "code"
    quote = ""
    triple = False
    template_depth = 0
    while i < size:
        if state == "line_comment":
            stop = source.find("\n", i)
            stop = size if stop == -1 else stop
            blank(i, stop)
            i = stop + 1
            state = "code"
            continue
        if state == "block_comment":
            stop = _block_comment_end(source, i, nested_comments)
            blank(i, stop)
            i = stop
            state = "code"
            continue
        if state in {"string", "template"}:
            pattern = (
                _STRING_STOP[quote * (3 if triple else 1)]
                if state == "string"
                else _template_stop(raw_backticks)
            )
            match = pattern.search(source, i)
            # A backslash escapes the character after it, whatever it is.
            stop = size if match is None else min(match.end() + (match.group() == "\\"), size)
            if strings:
                blank(i, stop)
            i = stop
            if match is not None and match.group() != "\\":
                state = "code"
                template_depth = 1 if match.group() == "${" else template_depth
            continue

        match = _CODE_STOP.search(source, i)
        if match is None:
            break
        i = match.start()
        char = source[i]
        if template_depth and char == "{":
            template_depth += 1
            i += 1
        elif template_depth and char == "}":
            if strings:
                blank(i, i + 1)
            template_depth -= 1
            i += 1
            if template_depth == 0:
                state = "template"
        elif (hash_comments and char == "#") or source.startswith("//", i):
            width = 1 if char == "#" else 2
            blank(i, i + width)
            i += width
            state = "line_comment"
        elif source.startswith("/*", i):
            blank(i, i + 2)
            i += 2
            state = "block_comment"
        elif char == "`":
            if strings:
                blank(i, i + 1)
            i += 1
            state = "template"
        elif char in {"'", '"'}:
            quote = char
            triple = source.startswith(quote * 3, i)
            width = 3 if triple else 1
            if strings:
                blank(i, i + width)
            i += width
            state = "string"
        else:
            i += 1
    out.append(source[copied:])
    return "".join(out)


def source_lines(source: str) -> list[str]:
    """*source* split where git splits it, so line numbers agree with a diff.

    ``str.splitlines`` also breaks on form feeds and Unicode separators, which
    numbered every later finding past git's line for it.
    """
    return [line.removesuffix("\r") for line in source.split("\n")]


def _call_findings(file_path: str, source: str, masked: Callable[[], str]) -> list[dict]:
    """Find executable eval/exec calls with AST or bounded lexical fallback.

    *masked* returns the source with comments and strings blanked, built on
    first use and shared with the caller.
    """
    is_python = file_path.lower().endswith((".py", ".pyi"))
    # Masking only blanks characters, so every call found below names ``eval``
    # or ``exec`` in the source, and outside Python ``exec`` counts only through
    # ``child_process``. ``ast`` folds non-ASCII identifiers (NFKC), so such
    # Python files still parse.
    words = ("eval", "exec") if is_python else ("eval", "child_process")
    if not any(word in source for word in words) and (not is_python or source.isascii()):
        return []
    if is_python:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            pass
        else:
            lines = source_lines(source)
            findings = []
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if isinstance(node.func, ast.Name):
                    call_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    call_name = node.func.attr
                else:
                    continue
                if call_name not in {"eval", "exec"}:
                    continue
                findings.append(
                    {
                        "kind": f"{call_name}_call",
                        "severity": "high",
                        # ast also breaks lines on a lone ``\r``, which git does
                        # not; such a line number can outrun the split.
                        "snippet": _snippet(lines[node.lineno - 1])
                        if node.lineno <= len(lines)
                        else "",
                        "line": node.lineno,
                    }
                )
            return findings

    masked_source = masked()
    lines = source_lines(source)
    findings = []

    def add(kind: str, severity: str, offset: int) -> None:
        lineno = source.count("\n", 0, offset) + 1
        snippet = _snippet(lines[lineno - 1]) if lineno <= len(lines) else ""
        findings.append({"kind": kind, "severity": severity, "snippet": snippet, "line": lineno})

    for pattern, kind, severity in _CALL_PATTERNS:
        if kind == "exec_call" and not is_python:
            continue  # handled below, gated on child_process
        for match in pattern.finditer(masked_source):
            add(kind, severity, match.start())

    if not is_python:
        for offset in _child_process_exec_calls(source, masked_source):
            add("exec_call", "high", offset)

    return findings


def _is_missing_table_error(exc: Exception) -> bool:
    """True when *exc* is a 'no such table' / 'does not exist' failure.

    ``replace_findings`` runs against a DB that may not yet have migrated the
    ``security_findings`` table (pre-migration indexing). Those failures are
    expected and skipped silently; everything else is a real error.
    """
    message = str(exc).lower()
    return "no such table" in message or "does not exist" in message


_TS_TYPE_VALUE = re.compile(
    r"(?:string|number|boolean|any|unknown|never|undefined|null|TrustedHTML)\b"
    r"(?:\s*\|\s*[\w$.]+)*\s*"
)
# What opens an object type on the line: an annotation ``:``, a generic ``<``,
# ``as``, ``interface X`` or ``type X =``. A ternary's ``:`` reads the same; the
# rare ``c ? a : {__html: string}`` is the accepted miss.
_TYPE_OPENER = re.compile(
    r"(?:[:<]|\bas|\binterface\s+[\w$]+[^{=]*|\btype\s+[\w$]+[^=]*=)\s*$"
)


def _is_html_type_annotation(line: str, match: re.Match) -> bool:
    """Whether ``__html: <type keyword>`` sits in a type, not an object value.

    A type keyword counts only in a type position: a member closed by ``;``, or
    an object type opened by ``_TYPE_OPENER``. ``{{__html: string}}`` and
    ``= {__html: string}`` read a variable named ``string`` and stay findings.
    """
    keyword = _TS_TYPE_VALUE.match(line, match.end())
    if keyword is None:
        return False
    if line.startswith(";", keyword.end()):
        return True
    depth = 0
    for i in range(match.start() - 1, -1, -1):
        if line[i] == "}":
            depth += 1
        elif line[i] == "{":
            if depth == 0:
                return bool(_TYPE_OPENER.search(line[:i]))
            depth -= 1
    return False


def _match_starting_in(pattern: re.Pattern, line: str, kept: str) -> re.Match | None:
    """First match of *pattern* on *line* whose first character survived masking in *kept*."""
    for match in pattern.finditer(line):
        start = match.start()
        if start < len(kept) and not kept[start].isspace():
            return match
    return None


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
                # Written in code, and not declared a non-security use.
                match = _match_starting_in(pattern, line, masked_line(lineno, line))
                if match and _NOT_FOR_SECURITY.search(
                    _call_text(mask()[1], lineno, match.start())
                ):
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


class SecurityScanner:
    """Scan a single file for security signals and persist to the database."""

    def __init__(self, session: AsyncSession, repo_id: str) -> None:
        self._session = session
        self._repo_id = repo_id

    async def scan_file(
        self,
        file_path: str,
        source: str,
        symbols: list[Any],
    ) -> list[dict]:
        """Async wrapper over :func:`scan_source`."""
        return scan_source(file_path, source, symbols)

    def _uses_sqlite(self) -> bool:
        """True when the bound session talks to SQLite (local/dev backend)."""
        try:
            name = self._session.bind.dialect.name  # type: ignore[attr-defined]
        except AttributeError:
            name = ""
        return name == "sqlite"

    async def persist(
        self,
        file_path: str,
        findings: list[dict],
        *,
        commit_sha: str | None = None,
        commit_at: datetime | None = None,
    ) -> int:
        """Insert security findings into the security_findings table.

        Re-runs never duplicate rows: the unique provenance constraint
        (``uq_security_finding_provenance``) makes a conflicting INSERT a no-op.
        We pick the conflict clause per dialect — Postgres supports
        ``ON CONFLICT ON CONSTRAINT ... DO NOTHING``; SQLite uses
        ``INSERT OR IGNORE`` (``ON CONFLICT ON CONSTRAINT`` is unsupported).

        ``commit_sha`` / ``commit_at`` carry the git-history provenance; omit
        them (working-tree scans) and the dedup key stores ``""`` for
        ``commit_sha`` (not NULL) so the unique constraint keys identically
        across runs.

        A per-row failure is skipped (``continue``) rather than aborting the
        whole batch, so one malformed finding cannot silently drop the rest.
        Returns the number of rows actually inserted, taken from the statement's
        ``rowcount`` (the constraint makes duplicate inserts report 0 affected
        rows on Postgres; SQLite reports the inserted count via ``rowcount`` too).
        """
        if not findings:
            return 0

        now = datetime.now(UTC)
        sha_key = commit_sha or ""
        uses_sqlite = self._uses_sqlite()
        if uses_sqlite:
            insert_prefix = "INSERT OR IGNORE INTO security_findings "
            conflict_suffix = ""
        else:
            insert_prefix = "INSERT INTO security_findings "
            conflict_suffix = " ON CONFLICT ON CONSTRAINT uq_security_finding_provenance DO NOTHING"

        inserted = 0
        for finding in findings:
            try:
                result = await self._session.execute(
                    text(
                        insert_prefix
                        + "(repository_id, file_path, kind, severity, snippet, line_number, "
                        "commit_sha, commit_at, detected_at) "
                        "VALUES (:repo_id, :file_path, :kind, :severity, :snippet, :line, "
                        ":commit_sha, :commit_at, :detected_at)" + conflict_suffix
                    ),
                    {
                        "repo_id": self._repo_id,
                        "file_path": file_path,
                        "kind": finding["kind"],
                        "severity": finding["severity"],
                        "snippet": finding.get("snippet", ""),
                        "line": finding.get("line", 0),
                        "commit_sha": sha_key,
                        "commit_at": commit_at,
                        "detected_at": now,
                    },
                )
                inserted += max(result.rowcount or 0, 0)
            except Exception:
                logger.warning(
                    "security_finding_persist_failed file_path=%s kind=%s",
                    file_path,
                    finding.get("kind"),
                    exc_info=True,
                )
                continue
        return inserted

    async def replace_findings(
        self,
        findings_by_file: dict[str, list[dict]],
        scanned_paths: list[str],
    ) -> None:
        """Replace the findings rows for every scanned file in one pass.

        Deleting all *scanned* paths (not just those with findings) keeps the
        table idempotent: re-indexing never accumulates duplicate rows, and a
        file whose issues were fixed loses its stale rows. Only working-tree
        rows (``commit_sha`` empty) are replaced — history findings from
        ``scan --history`` are left intact. Uses raw SQL to stay independent of
        any ORM session state; silently skips if the table doesn't exist yet
        (pre-migration).

        Re-running must never lose findings: two findings in one batch can
        share a provenance key (e.g. two keyword symbols on the same line), and
        a plain bulk INSERT would abort the whole batch at the first collision
        — after the DELETE above already removed the file's prior rows. Rows
        are therefore deduplicated in Python and inserted with the same
        conflict-tolerant clauses as ``persist``, so a duplicate key is a no-op
        rather than an abort.
        """
        chunk_size = 400  # SQLite parameter-limit headroom, same as the CRUD layer

        try:
            for i in range(0, len(scanned_paths), chunk_size):
                chunk = scanned_paths[i : i + chunk_size]
                placeholders = ", ".join(f":p{j}" for j in range(len(chunk)))
                params: dict[str, object] = {"repo_id": self._repo_id}
                params.update({f"p{j}": p for j, p in enumerate(chunk)})
                await self._session.execute(
                    text(
                        "DELETE FROM security_findings "
                        "WHERE repository_id = :repo_id "
                        f"AND file_path IN ({placeholders}) "
                        "AND COALESCE(commit_sha, '') = ''"
                    ),
                    params,
                )

            now = datetime.now(UTC)
            rows = [
                {
                    "repo_id": self._repo_id,
                    "file_path": file_path,
                    "kind": finding["kind"],
                    "severity": finding["severity"],
                    "snippet": finding.get("snippet", ""),
                    "line": finding.get("line", 0),
                    "commit_sha": "",
                    "commit_at": None,
                    "detected_at": now,
                }
                for file_path, findings in findings_by_file.items()
                for finding in findings
            ]
            # Two findings can collide on the unique provenance key
            # (repository_id, file_path, kind, line_number, commit_sha) — e.g.
            # two keyword symbols on the same line. Keeping only the first per
            # key makes the batch insertable and lossless (the duplicates are
            # redundant signals, not distinct rows).
            seen: set[tuple[str, str, int]] = set()
            deduped: list[dict] = []
            for row in rows:
                key = (row["file_path"], row["kind"], row["line"])
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(row)

            if deduped:
                uses_sqlite = self._uses_sqlite()
                insert_prefix = (
                    "INSERT OR IGNORE INTO security_findings "
                    if uses_sqlite
                    else "INSERT INTO security_findings "
                )
                conflict_suffix = (
                    ""
                    if uses_sqlite
                    else " ON CONFLICT ON CONSTRAINT uq_security_finding_provenance DO NOTHING"
                )
                await self._session.execute(
                    text(
                        insert_prefix
                        + "(repository_id, file_path, kind, severity, snippet, line_number, "
                        "commit_sha, commit_at, detected_at) "
                        "VALUES (:repo_id, :file_path, :kind, :severity, :snippet, :line, "
                        ":commit_sha, :commit_at, :detected_at)" + conflict_suffix
                    ),
                    deduped,
                )
            from repowise.core.persistence.crud.repository import stamp_analysis_ran

            await stamp_analysis_ran(self._session, self._repo_id, "security")
        except Exception as exc:
            # Pre-migration, the table does not exist yet — silently skip (the
            # historical contract for indexing against a not-yet-migrated DB).
            # Any other failure is a real one and must not be swallowed: a
            # silently dropped batch is how findings disappear.
            if _is_missing_table_error(exc):
                return
            logger.exception(
                "security_findings_replace_failed paths=%d rows=%d",
                len(scanned_paths),
                len(rows) if "rows" in locals() else 0,
            )
