"""Pattern registry and kind sets for the security scanner."""

from __future__ import annotations

import re

# Bump whenever a change alters what the scanner reports: a new/removed
# pattern, a changed severity, a precision fix to an existing rule. ``update``
# compares this against the value stamped in state.json and, on a mismatch,
# rescans every tracked working-tree file instead of only the changed ones,
# the same way ``HEALTH_ANALYZER_VERSION`` forces a full health re-score
# (#3072). Without this, a scanner fix only ever reaches a file a user
# happens to edit.
SECURITY_SCANNER_VERSION = 5


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
