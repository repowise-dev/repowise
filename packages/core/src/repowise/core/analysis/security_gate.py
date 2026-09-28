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
"""

from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from repowise.core.ci import baseline as ci_baseline
from repowise.core.ci import github, sarif
from repowise.core.ci.markdown import ROW_LIMIT, cell, more_line, plural
from repowise.core.support_paths import DOC_EXTENSIONS

from .change_risk.features import GIT_TIMEOUT_SECONDS, _git, revspec_head, split_revspec
from .changed_lines import parse_unified_diff
from .security_scan import SECRET_KINDS, scan_source

#: Severities from least to most severe; ``--fail-on`` names the lowest that fails.
SEVERITIES = ("low", "med", "high")
_RANK = {s: i for i, s in enumerate(SEVERITIES)}

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
# Scan
# ---------------------------------------------------------------------------


def fingerprint_of(file_path: str, kind: str, snippet: str) -> str:
    """The line-independent key a baseline holds, over the *masked* snippet.

    Consequence accepted: two identical matched lines in one file share a
    fingerprint, and so do two secrets whose visible prefix and line agree.
    """
    parts = (_FINGERPRINT_NAMESPACE, file_path, kind, snippet)
    return hashlib.sha256(_FIELD_SEP.join(parts).encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class ChangeScan:
    """The findings a change adds and how much of it was read."""

    findings: list[dict]
    files_scanned: int
    commits_scanned: int


def _text(blob: bytes) -> str | None:
    """*blob* as text, or ``None`` when it is binary."""
    if b"\0" in blob[:_BINARY_SNIFF]:
        return None
    return blob.decode("utf-8", errors="replace")


def _read_blobs(root: str, specs: Sequence[str]) -> list[str | None]:
    """The text of each ``rev:path`` in *specs*, in order, over one ``cat-file``.

    ``None`` for an object that is missing, not a blob (a submodule) or binary.
    Raises when git itself fails.
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
    for _ in specs:
        nl = out.index(b"\n", pos)
        header = out[pos:nl].split()
        pos = nl + 1
        # "<sha> <type> <size>", else "<spec> missing" (the spec may hold spaces).
        if len(header) != 3 or not header[2].isdigit():
            texts.append(None)
            continue
        size = int(header[2])
        body = out[pos : pos + size]
        pos += size + 1
        texts.append(_text(body) if header[1] == b"blob" else None)
    return texts


def _scoped(path: str, source: str, lines: set[int], *, secrets_only: bool) -> list[dict]:
    """Findings in *source* that span a line in *lines*, as gate rows.

    A document keeps only secret kinds: prose naming ``pickle.loads`` is not a
    call, but a key pasted into a README is still a leak.
    """
    secrets_only = secrets_only or PurePosixPath(path).suffix.lower() in DOC_EXTENSIONS
    rows = []
    for f in scan_source(path, source):
        if secrets_only and f["kind"] not in SECRET_KINDS:
            continue
        if lines.isdisjoint(range(f["line"], f.get("end_line", f["line"]) + 1)):
            continue
        rows.append(
            {
                "file_path": path,
                "line_number": f["line"],
                "kind": f["kind"],
                "severity": f["severity"],
                "snippet": f["snippet"],
                "fingerprint": fingerprint_of(path, f["kind"], f["snippet"]),
                "commit": None,
            }
        )
    return rows


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


def _history_secrets(root: str, revspec: str) -> tuple[list[dict], int]:
    """Secret findings on the lines each commit in the change added, oldest first."""
    # Merges are skipped: a merge from the target branch brings its lines, not
    # the change's. A secret written in a conflict resolution and kept is still
    # caught by the head pass.
    args = ["log", "--no-merges", "--reverse", "-p", "--unified=0", "--no-color"]
    args += ["--no-ext-diff", "--format=%x00%H", *_log_range(root, revspec), "--"]
    raw = _git(args, root)
    chunks = raw.split("\0")[1:]
    cut = _shallow_boundary(root)
    rows: list[dict] = []
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
            for row in _scoped(path, text, added[path], secrets_only=True):
                row["commit"] = sha
                rows.append(row)
    return rows, len(chunks)


class ShallowHistoryError(ValueError):
    """The change reaches past a shallow clone's cut; its history cannot be read."""


def _shallow_boundary(root: str) -> frozenset[str]:
    """Commits whose parents a shallow clone left out (empty in a full clone)."""
    if _git(["rev-parse", "--is-shallow-repository"], root).strip() != "true":
        return frozenset()
    shallow = Path(root, _git(["rev-parse", "--git-path", "shallow"], root).strip())
    return frozenset(shallow.read_text(encoding="utf-8").split()) if shallow.exists() else frozenset()


def scan_change(root: str, changed: Mapping[str, set[int]], revspec: str) -> ChangeScan:
    """Every finding *revspec* adds; *changed* is its new-side lines per file.

    Raises :class:`subprocess.CalledProcessError` when git fails and
    :class:`ShallowHistoryError` when a commit of the change lost its parents.
    """
    paths = sorted(changed)
    head = revspec_head(revspec)
    findings: list[dict] = []
    scanned = 0
    texts = _read_blobs(root, [f"{head}:{p}" for p in paths])
    for path, text in zip(paths, texts, strict=True):
        if text is None:
            continue
        scanned += 1
        findings += _scoped(path, text, changed[path], secrets_only=False)
    history, commits = _history_secrets(root, revspec)
    # A secret still on a changed line at the head is reported there, once; a
    # secret committed twice inside the change is reported at its first commit.
    seen = {f["fingerprint"] for f in findings}
    for row in history:
        if row["fingerprint"] not in seen:
            seen.add(row["fingerprint"])
            findings.append(row)
    return ChangeScan(findings, files_scanned=scanned, commits_scanned=commits)


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

    def to_dict(self) -> dict:
        """A stable JSON shape: the verdict, its counts, the failing findings."""
        return {
            "passed": self.passed,
            "fail_on": self.fail_on,
            "failing_count": len(self.failing),
            "baselined_count": len(self.baselined),
            "below_threshold_count": self.below_threshold,
            "failing": [dict(f) for f in self.failing],
        }


def evaluate(
    findings: Iterable[Mapping[str, Any]],
    *,
    fail_on: str = "high",
    baseline: frozenset[str] | None = None,
) -> GateResult:
    """Split *findings* into failing, baselined and below-threshold."""
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


def _where(finding: Mapping[str, Any]) -> str:
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
                f"| {cell(_where(f))} | {cell(f['kind'])} | {cell(f['severity'])} "
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


def _sarif_rules() -> list[dict]:
    return [
        sarif.rule(
            kind,
            "".join(part.capitalize() for part in kind.split("_")),
            text,
            f"{text} {DETECTION_BASIS}",
            DETECTION_BASIS,
        )
        for kind, text in _RULE_TEXT.items()
    ]


def render_sarif(
    findings: Sequence[Mapping[str, Any]],
    *,
    tool_version: str,
    fail_on: str = "high",
) -> dict:
    """One SARIF 2.1.0 run; ``partialFingerprints`` survive line shifts.

    A finding at or above *fail_on* is an ``error``, so the level matches the gate.
    """
    results = []
    for f in _order(findings):
        properties: dict[str, Any] = {"severity": f["severity"], "snippet": f["snippet"]}
        if f["commit"]:
            properties["commit"] = f["commit"]
        results.append(
            sarif.result(
                str(f["kind"]),
                "error" if _RANK[f["severity"]] >= _RANK[fail_on] else "warning",
                _message(f),
                str(f["file_path"]),
                int(f["line_number"]),
                SARIF_FINGERPRINT_KEY,
                f["fingerprint"],
                properties,
            )
        )
    return sarif.run(SARIF_TOOL_NAME, tool_version, _sarif_rules(), results)
