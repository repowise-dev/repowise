"""Rules about what surrounds the code: credentials, documents, decisions, people."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import timedelta

from ..context import RepoContext
from ..facts import RepoFacts
from ..model import Action, RuleOutcome, WhyFact, fingerprint
from ._text import code, plural

# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------

#: Only credential kinds. Pattern kinds (``os_system``, ``pickle_loads``, ...)
#: are code smells with a high false-positive rate on this scanner today and
#: stay on the Security tab until its precision work lands.
SECRET_KINDS = {
    "hardcoded_secret": "hard-coded secret",
    "hardcoded_password": "hard-coded password",
}
PUBLIC_ENV_KIND = "public_env_secret"

_DOC_SUFFIXES = (".md", ".mdx", ".rst", ".txt", ".adoc")
_COMMENT_PREFIXES = ("#", "//", "*", "/*", ">>>", "--", '"', "'")


#: Values nobody would ship: the elided or templated examples in docstrings
#: and READMEs (`api_key="sk-..."`, `"your-key-here"`). A masked real secret
#: reads `sk-a****`, which none of these match.
_PLACEHOLDER_MARKERS = ("...", "…", "<", "your", "xxx", "example", "placeholder", "dummy", "fake", "changeme")
_QUOTED = re.compile(r"""["']([^"']*)["']""")


def _looks_like_code(path: str, snippet: str) -> bool:
    lowered = path.lower()
    if lowered.endswith(_DOC_SUFFIXES) or lowered.startswith("docs/") or "/docs/" in lowered:
        return False
    # Comments, docstrings and quoted examples: the scanner matches text, and a
    # sentence about passwords is not a password.
    if snippet.lstrip().startswith(_COMMENT_PREFIXES):
        return False
    values = _QUOTED.findall(snippet)
    if any(marker in v.lower() for v in values for marker in _PLACEHOLDER_MARKERS):
        return False
    # A credential is long and not a plain word: `api_key="ollama"` is the
    # dummy value a local server's client requires, not a secret.
    return not values or any(_credential_shaped(v) for v in values)


def _credential_shaped(value: str) -> bool:
    return len(value) >= 8 and not value.isalpha()


def live_secret(facts: RepoFacts, ctx: RepoContext) -> RuleOutcome:
    rule = "live_secret"
    if "security" in facts.unavailable:
        return RuleOutcome(rule, "unavailable", facts.unavailable["security"])
    by_file: dict[str, list] = defaultdict(list)
    for s in facts.secrets:
        if (s.kind in SECRET_KINDS or s.kind == PUBLIC_ENV_KIND) and _looks_like_code(
            s.file_path, s.snippet
        ):
            by_file[s.file_path].append(s)
    actions = []
    for path, found in by_file.items():
        public = all(s.kind == PUBLIC_ENV_KIND for s in found)
        lines = sorted({s.line for s in found if s.line})
        where = (
            f"line {lines[0]}"
            if len(lines) == 1
            else plural(len(lines), "line")
            if lines
            else "Unknown line"
        )
        if public:
            title = f"Keep the secret out of a public environment variable in {code(path)}"
            impact = (
                "A variable a web build exposes to the browser is read as a secret "
                "here; anything it holds ships to every visitor."
            )
            done = "The secret is read only from a server-side variable."
            tier, severity = "plan", "high"
        else:
            kind = SECRET_KINDS[next(s.kind for s in found if s.kind in SECRET_KINDS)]
            title = f"Rotate the {kind} in {code(path)}"
            impact = "A credential sits in the working tree; anyone who can read the repository can use it."
            done = "Rotate the credential with its provider, then remove it from the file."
            tier, severity = "act_now", "critical"
        actions.append(
            Action(
                rule=rule,
                tier=tier,
                horizons=("week", "quarter"),
                severity=severity,
                title=title,
                impact=impact,
                why=(WhyFact("where", where), WhyFact("in", "Working tree")),
                target_kind="file",
                target_path=path,
                surface="security",
                effort="S",
                # Until the scanner's precision work lands, a secret match is
                # worth a look rather than a certainty.
                confidence="medium",
                done_when=done,
                evidence_total=len(found),
                fingerprint=fingerprint(len(found)),
            )
        )
    return RuleOutcome(rule, "evaluated", "", tuple(actions))


# ---------------------------------------------------------------------------
# Documentation
# ---------------------------------------------------------------------------

DRIFT_MIN_CONFIDENCE = 0.8


def _trusted_drift(d) -> bool:
    if d.confidence < DRIFT_MIN_CONFIDENCE:
        return False
    if d.kind == "path":
        # A document can name paths from another repository or an example that
        # never existed. Trust the claim when the path was part of this one.
        return d.target_known
    if d.kind == "anchor":
        # `/#install` is a site-root link on a web page, not an anchor in this
        # file tree.
        return not d.raw.startswith(("/", "http:", "https:"))
    return False


def broken_doc_refs(facts: RepoFacts, ctx: RepoContext) -> RuleOutcome:
    rule = "broken_doc_refs"
    if "doc_drift" in facts.unavailable:
        return RuleOutcome(rule, "unavailable", facts.unavailable["doc_drift"])
    by_doc: dict[str, list] = defaultdict(list)
    for d in facts.drift:
        if _trusted_drift(d):
            by_doc[d.document].append(d)
    actions = []
    for doc, found in by_doc.items():
        targets = sorted({d.target for d in found})
        actions.append(
            Action(
                rule=rule,
                tier="plan",
                horizons=("quarter",),
                severity="low",
                title=f"Fix {plural(len(targets), 'broken reference')} in {code(doc)}",
                impact=(
                    "The document points readers and agents at "
                    f"{'a path' if len(targets) == 1 else 'paths'} this repository "
                    "no longer has."
                ),
                why=(
                    WhyFact("broken references", str(len(targets))),
                    WhyFact("first", targets[0]),
                ),
                target_kind="document",
                target_path=doc,
                surface="doc_drift",
                effort="S",
                confidence="high",
                done_when="The references resolve on the next update.",
                weight=float(len(targets)),
                evidence_total=len(found),
                fingerprint=fingerprint(len(targets)),
            )
        )
    return RuleOutcome(rule, "evaluated", "", tuple(actions))


# ---------------------------------------------------------------------------
# Dead code
# ---------------------------------------------------------------------------

DEAD_MIN_SYMBOLS = 3
DEAD_MIN_LINES = 30

#: Code nothing imports on purpose: tutorials, samples, and scripts run by
#: hand. FastAPI's `docs_src/` alone is 137 "unreachable" files.
_STANDALONE_DIRS = ("docs/", "docs_src/", "examples/", "example/", "samples/", "scripts/")


def _standalone(path: str) -> bool:
    lowered = path.lower()
    return lowered.startswith(_STANDALONE_DIRS) or any(
        f"/{d}" in lowered for d in _STANDALONE_DIRS
    )


def dead_code_batch(facts: RepoFacts, ctx: RepoContext) -> RuleOutcome:
    rule = "dead_code_batch"
    if "dead_code" in facts.unavailable:
        return RuleOutcome(rule, "unavailable", facts.unavailable["dead_code"])
    found = [d for d in facts.dead if not _standalone(d.file_path)]
    lines = sum(d.lines for d in found)
    if len(found) < DEAD_MIN_SYMBOLS and lines < DEAD_MIN_LINES:
        return RuleOutcome(
            rule,
            "evaluated",
            f"{plural(len(found), 'safe-to-delete symbol')}; below the batch floor.",
        )
    files = {d.file_path for d in found}
    action = Action(
        rule=rule,
        tier="plan",
        horizons=("quarter",),
        severity="low",
        title=(
            f"Delete {len(found):,} unused "
            f"{'symbol or file' if len(found) == 1 else 'symbols and files'} ({lines:,} lines)"
        ),
        impact=(
            f"Nothing in the graph reaches them, across {plural(len(files), 'file')}; "
            "every reader and agent pays to skip them."
        ),
        why=(
            WhyFact("symbols", str(len(found))),
            WhyFact("lines", f"{lines:,}"),
            WhyFact("safe to delete", "Yes", "inferred"),
        ),
        target_kind="repo",
        target_path="",
        surface="dead_code",
        effort="S" if lines < 300 else "M",
        confidence="high",
        done_when="The dead-code list is empty after the next update.",
        weight=float(lines),
        evidence_ids=tuple(d.finding_id for d in found[:50]),
        evidence_total=len(found),
        fingerprint=fingerprint(len(found) // 5),
    )
    return RuleOutcome(rule, "evaluated", "", (action,))


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


def stale_decision(facts: RepoFacts, ctx: RepoContext) -> RuleOutcome:
    rule = "stale_decision"
    if "decisions" in facts.unavailable:
        return RuleOutcome(rule, "unavailable", facts.unavailable["decisions"])
    if facts.accepted_decisions == 0:
        return RuleOutcome(rule, "not_applicable", "No accepted decisions to drift.")
    actions = [
        Action(
            rule=rule,
            tier="plan",
            horizons=("quarter",),
            severity="medium",
            title=f"Update the decision “{d.title}”: the code it governs has moved on",
            impact="Agents and reviewers still follow it, so they are steered by a rule the code no longer keeps.",
            why=(WhyFact("status", "Accepted, drifting"),),
            target_kind="decision",
            target_path=d.id,
            surface="decisions",
            effort="S",
            confidence="medium",
            done_when="The decision is re-accepted, amended or superseded.",
            weight=float(len(facts.stale_decisions) - i),
            fingerprint=fingerprint(d.id),
        )
        for i, d in enumerate(facts.stale_decisions)
    ]
    return RuleOutcome(rule, "evaluated", "", tuple(actions))


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------

#: An owner quiet this long is treated as gone for planning purposes.
OWNER_QUIET = timedelta(days=60)


def knowledge_loss(facts: RepoFacts, ctx: RepoContext) -> RuleOutcome:
    rule = "knowledge_loss"
    if "files" in facts.unavailable:
        return RuleOutcome(rule, "unavailable", facts.unavailable["files"])
    if not ctx.is_team:
        return RuleOutcome(
            rule,
            "not_applicable",
            f"{plural(ctx.active_authors_90d, 'active author')} in 90 days; "
            "single ownership is the repository's shape, not a risk in it.",
        )
    if ctx.anchor is None:
        return RuleOutcome(rule, "not_applicable", "No commit history in the index.")
    actions = []
    for f in facts.files.values():
        if f.is_test or f.owner_key is None or (f.owner_pct or 0) < 0.8:
            continue
        if f.commits_90d < ctx.busy_threshold or (f.bus_factor or 1) > 1:
            continue
        last = facts.author_last_commit.get(f.owner_key)
        if last is None:
            continue
        quiet = ctx.anchor.replace(tzinfo=None) - last.replace(tzinfo=None)
        if quiet < OWNER_QUIET:
            continue
        actions.append(
            Action(
                rule=rule,
                tier="plan",
                horizons=("quarter",),
                severity="medium",
                title=f"Share what {f.owner_name or 'its main author'} knew about {code(f.path)}",
                impact=(
                    f"{round((f.owner_pct or 0) * 100)}% of its history is theirs, they have not "
                    f"committed in {quiet.days} days, and it still changes "
                    f"{f.commits_90d} times a quarter."
                ),
                why=(
                    WhyFact("main author's share", f"{round((f.owner_pct or 0) * 100)}%"),
                    WhyFact("days since their last commit", str(quiet.days)),
                    WhyFact("commits in 90 days", str(f.commits_90d)),
                ),
                target_kind="file",
                target_path=f.path,
                surface="file",
                effort="M",
                confidence="medium",
                done_when="A second person has reviewed or changed it.",
                weight=float(f.commits_90d),
                fingerprint=fingerprint(f.owner_key),
            )
        )
    return RuleOutcome(rule, "evaluated", "", tuple(actions))
