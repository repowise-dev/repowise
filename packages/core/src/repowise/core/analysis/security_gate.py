"""The CI gate over security signals: what a change adds, judged against a threshold.

Reads git (every call raises on failure, so a git error can never read as a
clean change) and runs :func:`~.security_scan.scan_source`; no index, no
database. Two passes over the change:

* **At its head.** Every changed file is scanned as the head commit has it, and
  a finding counts when a line it spans is on the new side of the diff.
* **Secrets inside it.** Each commit in the range is scanned the same way,
  secret kinds only, so a key added in one commit and deleted in the next is
  still caught: it stays in the history, and in every clone, after HEAD
  drops it. File contents are held in memory for the scan and never written.

Only the masked snippet (:func:`~.security_scan._snippet`) leaves this module,
and the fingerprint is taken over it too, so neither a CI log nor the committed
baseline holds a raw value.

Two repository-owned inputs shape a scan. ``security.patterns`` in
``.repowise/config.yaml`` adds secret kinds (``custom:<name>``, see
:func:`custom_patterns`), treated like the built-in ones. A
``repowise-security-ignore`` marker on a finding's own line silences it (all
kinds, or only the ``: kind, kind`` it lists); a silenced finding never fails
the gate or enters a baseline, but every output still counts and lists it.
"""

from __future__ import annotations

import hashlib
import re
import string
import subprocess
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from repowise.core.ci import baseline as ci_baseline
from repowise.core.ci import github, gitlab, sarif
from repowise.core.ci.markdown import ROW_LIMIT, cell, details, more_line, plural
from repowise.core.support_paths import DOC_EXTENSIONS

from .change_risk.features import GIT_TIMEOUT_SECONDS, _git, revspec_head, split_revspec
from .changed_lines import DIFF_ARGS, parse_unified_diff
from .security_scan import SECRET_KINDS, masked_snippet, scan_source, source_lines

#: Severities from least to most severe; ``--fail-on`` names the lowest that fails.
SEVERITIES = ("low", "med", "high")
_RANK = {s: i for i, s in enumerate(SEVERITIES)}

#: Secret kinds matched by the value's own vendor format rather than a variable
#: name; see :func:`fingerprint_of`. A remote URL password can be as weak as
#: any other, so it stays out.
_KEY_SHAPE_KINDS = SECRET_KINDS - {
    "hardcoded_password",
    "hardcoded_secret",
    "git_url_credentials",
}


class ShallowHistoryError(ValueError):
    """The change reaches past a shallow clone's cut; its history cannot be read."""


class MissingObjectError(ValueError):
    """git has no object for a path a diff listed."""

DETECTION_BASIS = (
    "Pattern matches from a fixed regex registry: a floor, not a scanner. A clean "
    "result means no pattern matched a changed line, not that the change is safe."
)

BASELINE_BASIS = (
    "Security findings accepted as known. A listed finding does not fail the gate; "
    "it is keyed on file, kind and the masked matched line, not line number."
)

SARIF_TOOL_NAME = "repowise-security"
SARIF_FINGERPRINT_KEY = "repowiseSecurity/v1"

_FINGERPRINT_NAMESPACE = "repowise.security.fingerprint.v1"
_FIELD_SEP = "\x1f"
#: Bytes read to decide a blob is binary, as git itself does.
_BINARY_SNIFF = 8000

#: SARIF rule text per kind the gate can report. ``security_sensitive_symbol``
#: is absent on purpose: it needs parsed symbols and never gates.
_RULE_TEXT: dict[str, str] = {
    "eval_call": "A call to eval.",
    "exec_call": "A call to exec (outside Python, in a file that names child_process).",
    "pickle_loads": "pickle.loads, which runs code from the data it loads.",
    "subprocess_shell_true": "A subprocess call with shell=True.",
    "os_system": "os.system, which runs its argument through a shell.",
    "hardcoded_password": "A quoted literal assigned to a password-named variable.",
    "hardcoded_secret": "A quoted literal assigned to a key-, secret- or token-named variable.",
    "aws_access_key": "An AWS access key ID.",
    "github_token": "A GitHub token.",
    "slack_token": "A Slack token.",
    "google_api_key": "A Google API key.",
    "stripe_key": "A Stripe secret or restricted key.",
    "gitlab_token": "A GitLab token (personal, deploy, runner, trigger, CI job or OAuth).",
    "azure_devops_pat": "An Azure DevOps personal access token.",
    "git_url_credentials": "A password or token embedded in a git remote URL.",
    "private_key_pem": "A PEM private key with its body.",
    "fstring_sql": "An f-string holding SQL and an interpolation.",
    "concat_sql": "SQL built by string concatenation.",
    "tls_verify_false": "TLS certificate verification turned off (verify=False).",
    "weak_hash": "The words md5 or sha1.",
    "unsafe_inner_html": "__html set from a non-literal value.",
    "template_literal_sql": "A template literal holding SQL and an interpolation.",
    "public_env_secret": "A secret-shaped name behind a NEXT_PUBLIC_ or VITE_ prefix.",
    "new_function_call": "new Function(...), which compiles a string as code.",
    "reject_unauthorized_false": "TLS verification turned off (rejectUnauthorized: false).",
}


# ---------------------------------------------------------------------------
# Custom patterns and the inline marker
# ---------------------------------------------------------------------------

MAX_CUSTOM_PATTERNS = 50
MAX_CUSTOM_REGEX_LENGTH = 500
#: Lines longer than this are not matched against custom patterns (counted as
#: skipped). Ceiling: Python's ``re`` has no timeout, so only line length is
#: bounded and a pathological regex that passes validation can still be slow.
MAX_CUSTOM_LINE_LENGTH = 4096

_CUSTOM_NAME = re.compile(r"[a-z0-9_-]{1,40}")
_CUSTOM_KEYS = ("name", "regex", "severity")
_CUSTOM_PREFIX = "custom:"
#: Every printable ASCII character, so a zero-width pattern (``\b``, a
#: lookaround such as ``(?=z)``) matches somewhere in it.
_ZERO_WIDTH_PROBE = string.printable
_QUANTIFIERS = ("*", "+", "{")

_IGNORE_TOKEN = "repowise-security-ignore"
_IGNORE_RE = re.compile(
    rf"{_IGNORE_TOKEN}(?![\w-])"
    r"(?:[ \t]*:[ \t]*([\w:-]+(?:[ \t]*,[ \t]*[\w:-]+)*))?"
    # A colon no kind list follows is a malformed scope, which silences nothing.
    r"(?P<stray>[ \t]*:)?"
)


@dataclass(frozen=True)
class CustomPattern:
    """One ``security.patterns`` entry: a secret shape the repository defines."""

    name: str
    regex: re.Pattern[str]
    severity: str

    @property
    def kind(self) -> str:
        return f"{_CUSTOM_PREFIX}{self.name}"


def custom_patterns(
    config: Mapping[str, Any],
) -> tuple[tuple[CustomPattern, ...], tuple[str, ...]]:
    """``security.patterns`` from a loaded repo config: the patterns, and every error found."""
    raw, errors = _pattern_entries(config.get("security"))
    patterns: list[CustomPattern] = []
    for i, entry in enumerate(raw):
        parsed = _custom_pattern(entry, {p.name for p in patterns})
        if isinstance(parsed, CustomPattern):
            patterns.append(parsed)
        else:
            errors.extend(f"{_entry_label(i, entry)}: {problem}" for problem in parsed)
    return tuple(patterns), tuple(errors)


def _pattern_entries(block: object) -> tuple[list, list[str]]:
    """The ``security.patterns`` entries to parse, and the problems with the block around them."""
    if block is None:
        return [], []
    if not isinstance(block, dict):
        return [], ["security must be a mapping."]
    unknown = sorted(str(k) for k in block if k != "patterns")
    errors = [f"security: unknown key {', '.join(unknown)}; expected patterns."] if unknown else []
    raw = block.get("patterns")
    if raw is None:
        return [], errors
    if not isinstance(raw, list):
        return [], [*errors, "security.patterns must be a list of {name, regex, severity}."]
    if len(raw) > MAX_CUSTOM_PATTERNS:
        errors.append(
            f"security.patterns holds {len(raw)} entries; the limit is {MAX_CUSTOM_PATTERNS}."
        )
    return raw, errors


def _entry_label(i: int, entry: object) -> str:
    name = entry.get("name") if isinstance(entry, dict) else None
    return f"security.patterns[{i}]" + (f" ({name!r})" if isinstance(name, str) else "")


def _custom_pattern(entry: object, taken: set[str]) -> CustomPattern | list[str]:
    """One entry, or every problem with it."""
    if not isinstance(entry, dict):
        return ["must be a mapping with name, regex and severity."]
    name, severity = entry.get("name"), entry.get("severity", "high")
    regex, regex_problems = _compiled_regex(entry.get("regex"))
    problems = [
        *_unknown_key_problems(entry),
        *_name_problems(name, taken),
        *_severity_problems(severity),
        *regex_problems,
    ]
    if problems:
        return problems
    assert isinstance(name, str) and regex is not None
    return CustomPattern(name, regex, str(severity))


def _unknown_key_problems(entry: dict) -> list[str]:
    unknown = sorted(str(k) for k in entry if k not in _CUSTOM_KEYS)
    if not unknown:
        return []
    return [f"unknown key {', '.join(unknown)}; expected {', '.join(_CUSTOM_KEYS)}."]


def _name_problems(name: object, taken: set[str]) -> list[str]:
    if not isinstance(name, str) or not _CUSTOM_NAME.fullmatch(name):
        return ["name must be 1 to 40 of a-z, 0-9, _ and -."]
    return ["duplicate name; each pattern needs its own."] if name in taken else []


def _severity_problems(severity: object) -> list[str]:
    if severity in SEVERITIES:
        return []
    return [f"severity must be one of {', '.join(SEVERITIES)}, got {severity!r}."]


def _compiled_regex(raw: object) -> tuple[re.Pattern[str] | None, list[str]]:
    """*raw* compiled, or ``None`` and why it cannot be used."""
    if not isinstance(raw, str) or not raw:
        return None, ["regex must be a non-empty string."]
    if len(raw) > MAX_CUSTOM_REGEX_LENGTH:
        return None, [f"regex is longer than {MAX_CUSTOM_REGEX_LENGTH} characters."]
    try:
        regex = re.compile(raw)
    except re.error as exc:
        return None, [f"regex does not compile: {exc}."]
    if _matches_zero_width(regex):
        return None, ["regex can match zero characters, so it would match every line."]
    if _nested_quantifier(raw):
        return None, [
            "regex repeats a group that itself repeats or alternates (like (a+)+ or "
            "(a|aa)+), which can take exponential time; use a character class."
        ]
    return regex, []


def _matches_zero_width(regex: re.Pattern[str]) -> bool:
    if regex.search("") is not None:
        return True
    return any(m.start() == m.end() for m in regex.finditer(_ZERO_WIDTH_PROBE))


def _nested_quantifier(regex: str) -> bool:
    """Best effort: a group quantified by ``*``, ``+`` or ``{`` whose body repeats or alternates.

    Reads the pattern's structure (:func:`_structure`) rather than a parse
    tree, so it catches the classic backtracking shapes only.
    """
    stack = [False]  # per open group: its body holds a quantifier or ``|``
    for char, following in _structure(regex):
        if char == "(":
            stack.append(False)
        elif char == ")" and len(stack) > 1:
            risky = stack.pop()
            quantified = following in _QUANTIFIERS
            if risky and quantified:
                return True
            stack[-1] = stack[-1] or risky or quantified
        elif char in _QUANTIFIERS or char == "|":
            stack[-1] = True
    return False


def _structure(regex: str) -> Iterator[tuple[str, str]]:
    """Each character of *regex* outside escapes and character classes, with the one after it."""
    i = 0
    while i < len(regex):
        char = regex[i]
        if char == "\\":
            i += 2
        elif char == "[":
            i = _class_end(regex, i)
        else:
            yield char, regex[i + 1 : i + 2]
            i += 1


def _class_end(regex: str, start: int) -> int:
    """The index just past the character class opening at *start*."""
    i = start + 1
    if regex[i : i + 1] == "^":
        i += 1
    if regex[i : i + 1] == "]":
        i += 1
    while i < len(regex) and regex[i] != "]":
        i += 2 if regex[i] == "\\" else 1
    return i + 1


def _silenced(line: str, kind: str) -> bool:
    """Whether a marker on *line* silences *kind*: the bare token all kinds, ``: a, b`` those."""
    if _IGNORE_TOKEN not in line:
        return False
    match = _IGNORE_RE.search(line)
    if match is None or match.group("stray"):
        return False
    if not match.group(1):
        return True
    return kind in {k.strip() for k in match.group(1).split(",")}


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


def fingerprint_of(file_path: str, kind: str, snippet: str, material: str = "") -> str:
    """The line-independent key a baseline holds, over the *masked* snippet.

    *material* is the matched text itself, passed only for the vendor key
    shapes (:data:`_KEY_SHAPE_KINDS`): those mask to the vendor's fixed prefix
    (``AKIA****``), so without it a different key on an identical line would
    inherit an accepted key's fingerprint. They are high-entropy by format, so a
    one-way hash of them cannot be walked back. The keyword kinds keep the
    masked-only key, because a short password could be guessed from its hash.
    Custom kinds do too: a repository's own pattern can be as weak as a password.

    Consequence accepted: two identical matched lines in one file share a
    fingerprint, and so do two keyword or custom secrets whose visible prefix and
    line agree.
    """
    parts = (_FINGERPRINT_NAMESPACE, file_path, kind, snippet, material)
    return hashlib.sha256(_FIELD_SEP.join(parts).encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class ChangeScan:
    """The findings a change adds and how much of it was read."""

    findings: list[dict]
    files_scanned: int
    commits_scanned: int
    #: Silenced by a ``repowise-security-ignore`` marker on their line.
    suppressed: list[dict] = field(default_factory=list)
    #: Distinct changed lines over :data:`MAX_CUSTOM_LINE_LENGTH`, not matched
    #: against custom patterns.
    long_lines_skipped: int = 0


def _text(blob: bytes) -> str | None:
    """*blob* as text, or ``None`` when it is binary."""
    if b"\0" in blob[:_BINARY_SNIFF]:
        return None
    return blob.decode("utf-8", errors="replace")


def _read_blobs(root: str, specs: Sequence[str]) -> list[str | None]:
    """The text of each ``rev:path`` in *specs*, in order, over one ``cat-file``.

    ``None`` for an object that is not a blob (a submodule) or is binary.
    Raises when git fails, and :class:`MissingObjectError` for an object git
    does not have: every spec names a file a diff just listed, so a miss means
    the path was misread, and skipping it would pass a file nobody scanned.
    """
    if not specs:
        return []
    proc = subprocess.run(
        ["git", "cat-file", "--batch"],
        cwd=root,
        input=("\n".join(specs) + "\n").encode("utf-8"),
        capture_output=True,
        timeout=GIT_TIMEOUT_SECONDS,
        check=True,
    )
    out, pos, texts = proc.stdout, 0, []
    for spec in specs:
        nl = out.index(b"\n", pos)
        header = out[pos:nl].split()
        pos = nl + 1
        # "<sha> <type> <size>", "<sha> submodule" for a gitlink, else
        # "<spec> missing" (the spec may hold spaces).
        if len(header) == 2 and header[1] == b"submodule":
            texts.append(None)
            continue
        if len(header) != 3 or not header[2].isdigit():
            raise MissingObjectError(f"git has no object for {spec!r}")
        size = int(header[2])
        body = out[pos : pos + size]
        pos += size + 1
        texts.append(_text(body) if header[1] == b"blob" else None)
    return texts


def _custom_spans(
    line: str, patterns: Sequence[CustomPattern]
) -> list[tuple[CustomPattern, list[tuple[int, int]]]]:
    """Each pattern that matches *line*, with the span of every match."""
    hits = []
    for pattern in patterns:
        spans = [m.span() for m in pattern.regex.finditer(line) if m.end() > m.start()]
        if spans:
            hits.append((pattern, spans))
    return hits


#: Set on a row a marker silenced; :func:`_split` removes it.
_SUPPRESSED = "_suppressed"
#: What JSON lists for a silenced finding: where and what, never the matched text.
_SUPPRESSED_FIELDS = ("file_path", "line_number", "kind", "severity", "fingerprint", "commit")


def _scoped(
    path: str,
    source: str,
    lines: set[int],
    *,
    secrets_only: bool,
    patterns: Sequence[CustomPattern] = (),
) -> tuple[list[dict], set[tuple[str, str]]]:
    """Findings in *source* that span a line in *lines*, as gate rows, and the long lines skipped.

    A document keeps only secret kinds: prose naming ``pickle.loads`` is not a
    call, but a key pasted into a README is still a leak. Custom patterns are
    secret kinds, matched on the lines in *lines* only; a custom match is also
    masked in a built-in finding's snippet on its line, while that finding keeps
    the fingerprint it has without patterns.
    """
    secrets_only = secrets_only or PurePosixPath(path).suffix.lower() in DOC_EXTENSIONS
    text = _Text(path, source_lines(source), patterns)
    found = [
        entry
        for f in scan_source(path, source)
        if (entry := _builtin_entry(text, f, lines, secrets_only)) is not None
    ]
    custom, long_lines = _custom_entries(text, lines)
    return [_row(text, entry) for entry in [*found, *custom]], long_lines


#: ``(line, kind, severity, snippet shown, fingerprint)``
_Entry = tuple[int, str, str, str, str]


@dataclass
class _Text:
    """One file's lines, with the custom-pattern matches on each looked up once."""

    path: str
    lines: list[str]
    patterns: Sequence[CustomPattern]
    _spans: dict[int, list[tuple[int, int]]] = field(default_factory=dict)
    _hits: dict[int, list[CustomPattern]] = field(default_factory=dict)

    def line(self, lineno: int) -> str:
        return self.lines[lineno - 1] if lineno <= len(self.lines) else ""

    def matched(self, lineno: int) -> list[CustomPattern]:
        """The patterns matching *lineno*; none on a line too long to match."""
        if lineno not in self._hits:
            line = self.line(lineno)
            hits = _custom_spans(line, self.patterns) if len(line) <= MAX_CUSTOM_LINE_LENGTH else []
            self._hits[lineno] = [p for p, _ in hits]
            self._spans[lineno] = [span for _, spans in hits for span in spans]
        return self._hits[lineno]

    def masked(self, lineno: int) -> str:
        """*lineno* as a snippet with every custom match masked."""
        self.matched(lineno)
        return masked_snippet(self.line(lineno), self._spans[lineno])


def _builtin_entry(
    text: _Text, f: Mapping[str, Any], lines: set[int], secrets_only: bool
) -> _Entry | None:
    """A registry finding as an entry, or ``None`` when it is out of scope."""
    if secrets_only and f["kind"] not in SECRET_KINDS:
        return None
    span = range(f["line"], f.get("end_line", f["line"]) + 1)
    if lines.isdisjoint(span):
        return None
    material = ""
    if f["kind"] in _KEY_SHAPE_KINDS:
        material = "\n".join(text.lines[span.start - 1 : span.stop - 1])
    snippet = f["snippet"]
    fingerprint = fingerprint_of(text.path, f["kind"], snippet, material)
    # Only a snippet cut from the line itself; a whole-line PEM mask stays.
    if text.matched(f["line"]) and snippet == masked_snippet(text.line(f["line"])):
        snippet = text.masked(f["line"])
    return f["line"], f["kind"], f["severity"], snippet, fingerprint


def _custom_entries(text: _Text, lines: set[int]) -> tuple[list[_Entry], set[tuple[str, str]]]:
    """Custom-pattern entries on *lines*, and the lines too long to match."""
    entries: list[_Entry] = []
    long_lines: set[tuple[str, str]] = set()
    if not text.patterns:
        return entries, long_lines
    for lineno in sorted(n for n in lines if n <= len(text.lines)):
        if len(text.line(lineno)) > MAX_CUSTOM_LINE_LENGTH:
            long_lines.add((text.path, text.line(lineno)))
            continue
        snippet = text.masked(lineno)
        for pattern in text.matched(lineno):
            fingerprint = fingerprint_of(text.path, pattern.kind, snippet)
            entries.append((lineno, pattern.kind, pattern.severity, snippet, fingerprint))
    return entries, long_lines


def _row(text: _Text, entry: _Entry) -> dict:
    """*entry* as a gate row, flagged when a marker on its line silences it."""
    lineno, kind, severity, snippet, fingerprint = entry
    row = {
        "file_path": text.path,
        "line_number": lineno,
        "kind": kind,
        "severity": severity,
        "snippet": snippet,
        "fingerprint": fingerprint,
        "commit": None,
    }
    if _silenced(text.line(lineno), kind):
        row[_SUPPRESSED] = True
    return row


def _split(rows: Iterable[dict]) -> tuple[list[dict], list[dict]]:
    """``(findings, suppressed)`` from :func:`_scoped` rows."""
    findings: list[dict] = []
    suppressed: list[dict] = []
    for row in rows:
        (suppressed if row.pop(_SUPPRESSED, False) else findings).append(row)
    return findings, suppressed


def _log_range(root: str, revspec: str) -> list[str]:
    """The commits *revspec* covers, as ``git log`` arguments.

    A range lists what its head has and its base does not, which for
    ``base...head`` is the branch's own commits. One revision covers itself,
    and for a merge the branch it brought in, matching the first-parent diff
    :func:`~.changed_lines.changed_lines` takes of it.
    """
    if (parts := split_revspec(revspec)) is not None:
        base, _, head = parts
        return [f"{base}..{head}"]
    if _git(["rev-parse", "--verify", "--quiet", f"{revspec}^"], root, check=False).strip():
        return [f"{revspec}^..{revspec}"]
    return ["--max-count=1", revspec]  # a root commit


def _history_secrets(
    root: str, revspec: str, patterns: Sequence[CustomPattern]
) -> tuple[list[dict], int, set[tuple[str, str]]]:
    """Secret rows on the lines each commit in the change added, oldest first.

    Returns the :func:`_scoped` rows, the commits read and the long lines skipped.
    """
    # Merges are skipped: a merge from the target branch brings its lines, not
    # the change's. Ceiling: a secret written in a conflict resolution is caught
    # only if the head keeps it; ``--diff-merges=remerge`` (git 2.36+) could show
    # what a resolution itself introduced.
    log_range = _log_range(root, revspec)
    cut = _shallow_boundary(root, log_range)
    args = ["log", "--no-merges", "--reverse", "-p", "--unified=0"]
    args += [*DIFF_ARGS, "--format=%x00%H", *log_range, "--"]
    raw = _git(args, root)
    chunks = raw.split("\0")[1:]
    rows: list[dict] = []
    skipped: set[tuple[str, str]] = set()
    for chunk in chunks:
        sha, _, diff = chunk.partition("\n")
        sha = sha.strip()
        if sha in cut:
            raise ShallowHistoryError(
                f"Commit {sha[:7]} in this change has its parents cut off by a shallow "
                "clone, so every line it holds would read as added. Fetch the full "
                "history (fetch-depth: 0)."
            )
        added = {p: d.new_lines for p, d in parse_unified_diff(diff).items() if d.new_lines}
        # One read per commit bounds memory to the files a single commit touched.
        paths = sorted(added)
        texts = _read_blobs(root, [f"{sha}:{p}" for p in paths])
        for path, text in zip(paths, texts, strict=True):
            if text is None:
                continue
            scoped, long_lines = _scoped(
                path, text, added[path], secrets_only=True, patterns=patterns
            )
            skipped |= long_lines
            for row in scoped:
                row["commit"] = sha
                rows.append(row)
    return rows, len(chunks), skipped


def _shallow_boundary(root: str, log_range: Sequence[str]) -> frozenset[str]:
    """Commits whose parents a shallow clone left out (empty in a full clone).

    Raises :class:`ShallowHistoryError` when the range's own ends have no
    merge-base: a cut on the base side hides that the head's older history is
    the base's too, and ``base..head`` would walk it back to the root.
    """
    if _git(["rev-parse", "--is-shallow-repository"], root).strip() != "true":
        return frozenset()
    if len(log_range) == 1 and ".." in log_range[0]:
        base, _, head = log_range[0].partition("..")
        if not _git(["merge-base", base, head], root, check=False).strip():
            raise ShallowHistoryError(
                f"{base} and {head} share no commit in this shallow clone, so the "
                "change's own commits cannot be told apart. Fetch the full history "
                "(fetch-depth: 0)."
            )
    shallow = Path(root, _git(["rev-parse", "--git-path", "shallow"], root).strip())
    if not shallow.exists():
        return frozenset()
    return frozenset(shallow.read_text(encoding="utf-8").split())


def scan_change(
    root: str,
    changed: Mapping[str, set[int]],
    revspec: str,
    *,
    patterns: Sequence[CustomPattern] = (),
    staged: bool = False,
) -> ChangeScan:
    """Every finding *revspec* adds; *changed* is its new-side lines per file.

    With *staged*, *changed* is the staged diff: files are read from the index
    and there is no history to scan, so *revspec* is only a label.

    Raises :class:`subprocess.CalledProcessError` when git fails and
    :class:`ShallowHistoryError` when a commit of the change lost its parents.
    """
    paths = sorted(changed)
    # ``:0:<path>`` is the index's copy; ``:<path>`` misreads a path like ``1:notes.txt``.
    rev = ":0" if staged else revspec_head(revspec)
    rows: list[dict] = []
    scanned = 0
    skipped: set[tuple[str, str]] = set()
    texts = _read_blobs(root, [f"{rev}:{p}" for p in paths])
    for path, text in zip(paths, texts, strict=True):
        if text is None:
            continue
        scanned += 1
        scoped, long_lines = _scoped(
            path, text, changed[path], secrets_only=False, patterns=patterns
        )
        rows += scoped
        skipped |= long_lines
    history: list[dict] = []
    commits = 0
    if not staged:
        history, commits, long_lines = _history_secrets(root, revspec, patterns)
        skipped |= long_lines
    findings, suppressed = _merge(rows, history)
    return ChangeScan(
        findings,
        files_scanned=scanned,
        commits_scanned=commits,
        suppressed=suppressed,
        long_lines_skipped=len(skipped),
    )


def _merge(head: list[dict], history: list[dict]) -> tuple[list[dict], list[dict]]:
    """``(findings, suppressed)``: the head's rows, then history rows no head row covers.

    A secret still on a changed line at the head is reported there, once; a
    secret committed twice inside the change is reported at its first commit.
    One fingerprint lands in one list, and an unsilenced row wins over a
    silenced one.
    """
    findings, suppressed = _split(head)
    failing = {f["fingerprint"] for f in findings}
    suppressed = [r for r in suppressed if r["fingerprint"] not in failing]
    silenced = {r["fingerprint"] for r in suppressed}
    later_findings, later_suppressed = _split(history)
    for row in later_findings:
        fp = row["fingerprint"]
        if fp in failing:
            continue
        if fp in silenced:
            suppressed = [r for r in suppressed if r["fingerprint"] != fp]
            silenced.discard(fp)
        failing.add(fp)
        findings.append(row)
    for row in later_suppressed:
        if row["fingerprint"] not in failing | silenced:
            silenced.add(row["fingerprint"])
            suppressed.append(row)
    return findings, suppressed


# ---------------------------------------------------------------------------
# Verdict and baseline
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateResult:
    """What the gate decided, and which findings it decided on."""

    passed: bool
    #: At or above ``fail_on`` and not baselined.
    failing: list[dict] = field(default_factory=list)
    #: At or above ``fail_on`` but accepted by the baseline.
    baselined: list[dict] = field(default_factory=list)
    below_threshold: int = 0
    fail_on: str = "high"
    #: Silenced inline; never failing, at any severity.
    suppressed: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        """A stable JSON shape: the verdict, its counts, the failing findings."""
        return {
            "passed": self.passed,
            "fail_on": self.fail_on,
            "failing_count": len(self.failing),
            "baselined_count": len(self.baselined),
            "below_threshold_count": self.below_threshold,
            "suppressed_count": len(self.suppressed),
            "failing": [dict(f) for f in self.failing],
            "suppressed": [{k: f[k] for k in _SUPPRESSED_FIELDS} for f in self.suppressed],
        }


def evaluate(
    findings: Iterable[Mapping[str, Any]],
    *,
    fail_on: str = "high",
    baseline: frozenset[str] | None = None,
    suppressed: Iterable[Mapping[str, Any]] = (),
) -> GateResult:
    """Split *findings* into failing, baselined and below-threshold; *suppressed* rides along."""
    accepted = baseline or frozenset()
    floor = _RANK[fail_on]
    failing: list[dict] = []
    baselined: list[dict] = []
    below = 0
    for finding in findings:
        if _RANK[finding["severity"]] < floor:
            below += 1
            continue
        (baselined if finding["fingerprint"] in accepted else failing).append(dict(finding))
    return GateResult(
        passed=not failing,
        failing=failing,
        baselined=baselined,
        below_threshold=below,
        fail_on=fail_on,
        suppressed=[dict(f) for f in suppressed],
    )


def write_baseline(
    path: Path | str,
    findings: Iterable[Mapping[str, Any]],
    *,
    keep: Iterable[Mapping[str, Any]] = (),
) -> int:
    """Write *findings* (plus the entries in *keep*) as the baseline at *path*.

    *keep* is the file's current entries: the gate sees one change at a time,
    so accepting this change's findings must not drop earlier ones.
    """
    entries = [
        *keep,
        *(
            {
                "fingerprint": f["fingerprint"],
                "file_path": f["file_path"],
                "kind": f["kind"],
                "snippet": f["snippet"],
            }
            for f in findings
        ),
    ]
    doc = ci_baseline.build_document(
        entries,
        basis=BASELINE_BASIS,
        sort_key=lambda e: (
            str(e.get("file_path", "")),
            str(e.get("kind", "")),
            str(e.get("snippet", "")),
            e["fingerprint"],
        ),
    )
    return ci_baseline.write_document(path, doc)


# ---------------------------------------------------------------------------
# Renderings
# ---------------------------------------------------------------------------


def _order(findings: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return sorted(
        findings,
        key=lambda f: (
            -_RANK[f["severity"]],
            f["commit"] is not None,
            f["file_path"],
            int(f["line_number"]),
            f["kind"],
        ),
    )


def location(finding: Mapping[str, Any]) -> str:
    """``path:line``, or ``path (commit abc1234)`` for a secret found in an earlier commit."""
    if finding["commit"]:
        return f"{finding['file_path']} (commit {finding['commit'][:7]})"
    return f"{finding['file_path']}:{int(finding['line_number'])}"


def _message(finding: Mapping[str, Any]) -> str:
    text = f"{finding['kind']} ({finding['severity']}): {finding['snippet']}"
    if finding["commit"]:
        text += (
            f" Committed in {finding['commit'][:7]} inside this change and not on a "
            "changed line at its head; it stays in the git history. Rotate it."
        )
    return text


def render_markdown(
    gate: GateResult,
    *,
    label: str,
    files_scanned: int,
    commits_scanned: int,
    long_lines_skipped: int = 0,
) -> str:
    """Step-summary / PR-comment markdown: verdict first, then a capped table."""
    if gate.passed:
        lines = [f"**No security findings at or above {gate.fail_on} in {cell(label)}.**"]
    else:
        n = len(gate.failing)
        lines = [
            f"**Security: {plural(n, 'finding')} {'fails' if n == 1 else 'fail'} the gate** "
            f"(severity {gate.fail_on} or above, in {cell(label)})."
        ]
    notes = [
        f"{plural(files_scanned, 'changed file')} and {plural(commits_scanned, 'commit')} scanned"
    ]
    if gate.baselined:
        notes.append(f"{len(gate.baselined)} accepted by the baseline")
    if gate.below_threshold:
        notes.append(f"{gate.below_threshold} below the threshold")
    if gate.suppressed:
        notes.append(f"{plural(len(gate.suppressed), 'finding')} suppressed inline")
    if long_lines_skipped:
        notes.append(
            f"{plural(long_lines_skipped, 'line')} over {MAX_CUSTOM_LINE_LENGTH} "
            "characters not matched against custom patterns"
        )
    lines += ["", "; ".join(notes) + "."]

    if gate.failing:
        lines += [
            "",
            "| Where | Kind | Severity | Matched (secrets masked) |",
            "| --- | --- | --- | --- |",
        ]
        ordered = _order(gate.failing)
        for f in ordered[:ROW_LIMIT]:
            lines.append(
                f"| {cell(location(f))} | {cell(f['kind'])} | {cell(f['severity'])} "
                f"| {cell(f['snippet'])} |"
            )
        if len(ordered) > ROW_LIMIT:
            lines += ["", more_line(len(ordered) - ROW_LIMIT, "findings")]
        if any(f["commit"] for f in gate.failing):
            lines += [
                "",
                "A row naming a commit is a secret committed inside this change and no "
                "longer on a changed line at its head. Deleting it does not remove it "
                "from the history: rotate it.",
            ]
    if gate.suppressed:
        items = [
            f"{cell(location(f))} {cell(f['kind'])} ({cell(f['severity'])})"
            for f in _order(gate.suppressed)
        ]
        lines += ["", *details("Suppressed inline (repowise-security-ignore)", items)]

    lines += ["", f"_{DETECTION_BASIS}_"]
    return "\n".join(lines) + "\n"


def github_annotations(findings: Sequence[Mapping[str, Any]], gate: GateResult) -> list[str]:
    """``::error`` for failing findings, ``::warning`` below the threshold, errors first.

    Baselined findings are accepted, so they are not annotated. A secret found
    only in an earlier commit is annotated on its file with no line, because
    the head's line numbers do not describe it.
    """
    # The fingerprint covers path and kind, so it fixes the severity: one
    # fingerprint is never both failing and below the threshold.
    failing = {f["fingerprint"] for f in gate.failing}
    accepted = {f["fingerprint"] for f in gate.baselined}
    errors: list[str] = []
    warnings: list[str] = []
    for f in _order(findings):
        if f["fingerprint"] in accepted:
            continue
        level = "error" if f["fingerprint"] in failing else "warning"
        line = github.annotation(
            level,
            _message(f),
            file=str(f["file_path"]),
            line=None if f["commit"] else int(f["line_number"]),
            title=f"Security: {f['kind']} ({f['severity']})",
        )
        (errors if level == "error" else warnings).append(line)
    return github.cap_annotations(errors + warnings, noun="security findings")


def _rule_id(kind: str) -> str:
    """The SARIF rule id / Code Quality check name: ``custom:<name>`` reads ``custom/<name>``."""
    return kind.replace(":", "/", 1)


def _sarif_rules(patterns: Sequence[CustomPattern] = ()) -> list[dict]:
    texts = {
        **_RULE_TEXT,
        **{
            p.kind: f"A match for the custom pattern {p.name} (security.patterns)."
            for p in patterns
        },
    }
    return [
        sarif.rule(
            _rule_id(kind),
            "".join(part.capitalize() for part in re.split(r"[_:-]", kind)),
            text,
            f"{text} {DETECTION_BASIS}",
            DETECTION_BASIS,
        )
        for kind, text in texts.items()
    ]


@dataclass(frozen=True)
class SarifExtras:
    """What a SARIF log carries beyond the findings themselves."""

    #: ``GateResult.suppressed``: listed with an ``inSource`` suppression.
    suppressed: Sequence[Mapping[str, Any]] = ()
    #: One rule each, ``custom/<name>``.
    patterns: Sequence[CustomPattern] = ()
    #: A run property, when not zero.
    long_lines_skipped: int = 0


_NO_EXTRAS = SarifExtras()


def render_sarif(
    findings: Sequence[Mapping[str, Any]],
    *,
    tool_version: str,
    fail_on: str = "high",
    accepted: frozenset[str] = frozenset(),
    extras: SarifExtras = _NO_EXTRAS,
) -> dict:
    """One SARIF 2.1.0 run; ``partialFingerprints`` survive line shifts.

    A finding at or above *fail_on* is an ``error``, so the level matches the
    gate, and one whose fingerprint is in *accepted* (the baseline) is marked
    suppressed. Findings silenced inline follow them (:class:`SarifExtras`). A
    secret found only in an earlier commit is located on its file with no line:
    its line number belongs to that commit, not the head.
    """
    results = [
        _sarif_result(f, fail_on, suppression="external" if f["fingerprint"] in accepted else None)
        for f in _order(findings)
    ]
    results += [
        _sarif_result(f, fail_on, suppression="inSource") for f in _order(extras.suppressed)
    ]
    skipped = extras.long_lines_skipped
    properties = {"longLinesSkipped": skipped} if skipped else None
    return sarif.run(
        SARIF_TOOL_NAME, tool_version, _sarif_rules(extras.patterns), results, properties=properties
    )


def _sarif_result(f: Mapping[str, Any], fail_on: str, *, suppression: str | None) -> dict:
    properties: dict[str, Any] = {"severity": f["severity"], "snippet": f["snippet"]}
    if f["commit"]:
        properties["commit"] = f["commit"]
    return sarif.result(
        _rule_id(str(f["kind"])),
        "error" if _RANK[f["severity"]] >= _RANK[fail_on] else "warning",
        _message(f),
        str(f["file_path"]),
        None if f["commit"] else int(f["line_number"]),
        SARIF_FINGERPRINT_KEY,
        f["fingerprint"],
        properties,
        suppressed=suppression is not None,
        suppression_kind=suppression or "external",
    )


def _gitlab_severity(severity: str, fail_on: str) -> str:
    if _RANK[severity] < _RANK[fail_on]:
        return "minor"
    return "critical" if severity == "high" else "major"


def render_gitlab(
    findings: Sequence[Mapping[str, Any]],
    *,
    fail_on: str = "high",
    accepted: frozenset[str] = frozenset(),
) -> list[dict]:
    """GitLab Code Quality issues whose severity follows the gate, as SARIF's level does.

    At or above *fail_on*: ``critical`` for high, ``major`` otherwise; below
    it, ``minor``. *accepted* (the baseline) is applied by
    :func:`~repowise.core.ci.gitlab.report`. A secret found only in an earlier
    commit sits on line 1 of its file, since its line belongs to that commit;
    when the change deleted that file, the path is absent at the head.
    """
    return gitlab.report(
        SARIF_TOOL_NAME,
        (
            gitlab.issue(
                _rule_id(str(f["kind"])),
                _gitlab_severity(f["severity"], fail_on),
                _message(f),
                str(f["file_path"]),
                None if f["commit"] else int(f["line_number"]),
                f["fingerprint"],
            )
            for f in _order(findings)
        ),
        accepted=accepted,
    )
