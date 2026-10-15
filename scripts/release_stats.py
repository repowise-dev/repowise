"""Compute a release's "By the numbers" figures from git (and GitHub, if reachable).

Usage::

    python scripts/release_stats.py                       # latest tag..HEAD
    python scripts/release_stats.py --from v0.56.0 --to origin/main --version unreleased
    python scripts/release_stats.py --version 0.57.0 --write-json
    python scripts/release_stats.py --pr                  # add the thank-you block

Prints the ``### By the numbers`` block for the CHANGELOG release section.
``--write-json`` stores the same figures under ``--version`` in the bundled
``release_stats.json`` the dashboard and ``repowise whats-new`` read. ``--pr``
also prints the returning and first-time contributor handles, for the release
PR body only: names never go into the changelog or the bundled data.

Contributor handles and the first-time split need the ``gh`` CLI. Without it
(or offline) the git counts are still printed and the first-time count is
omitted.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATS_JSON = ROOT / "packages/core/src/repowise/core/upgrade/_data/release_stats.json"
GITHUB_REPO = "repowise-dev/repowise"
MAINTAINER_LOGINS = frozenset({"raghavchamadiya", "swati510"})
MAINTAINER_NAMES = frozenset({"raghav", "swati", "raghav chamadiya", "swati ahuja"})

_PR_RE = re.compile(r"\(#(\d+)\)\s*$")
_TYPE_RE = re.compile(r"^([a-z]+)[(!:]")
_ISSUE_RE = re.compile(r"\b(?:fix(?:es|ed)?|close[sd]?|resolve[sd]?)\s+#(\d+)", re.IGNORECASE)
_TEST_RE = re.compile(r"(^|/)tests?/|(^|/)test_[^/]*$|\.test\.[a-z]+$|_test\.[a-z]+$")
_SHORTSTAT_RE = re.compile(
    r"(\d+) files? changed(?:, (\d+) insertions?\(\+\))?(?:, (\d+) deletions?\(-\))?"
)
_COMMIT_SEP, _FIELD_SEP = "\x1e", "\x1f"

#: Commit types the numbers line names, in the order it names them.
TYPE_KEYS = {"fix": "fixes", "feat": "features", "perf": "perf", "refactor": "refactors", "docs": "docs"}


# --- pure parsing (unit-tested) -------------------------------------------


def parse_log(text: str) -> list[dict[str, str]]:
    """``git log`` output in the ``_log_format`` shape to commit dicts."""
    commits = []
    for chunk in text.split(_COMMIT_SEP):
        parts = chunk.strip("\n").split(_FIELD_SEP)
        if len(parts) < 5:
            continue
        sha, name, email, subject, body = parts[:5]
        commits.append({"sha": sha, "name": name, "email": email, "subject": subject, "body": body})
    return commits


def parse_shortstat(text: str) -> tuple[int, int, int]:
    """``git diff --shortstat`` to ``(files, added, removed)``."""
    m = _SHORTSTAT_RE.search(text)
    if not m:
        return 0, 0, 0
    return int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0)


def count_types(subjects: list[str]) -> dict[str, int]:
    counts = dict.fromkeys(TYPE_KEYS.values(), 0)
    for subject in subjects:
        m = _TYPE_RE.match(subject)
        if m and m.group(1) in TYPE_KEYS:
            counts[TYPE_KEYS[m.group(1)]] += 1
    return counts


def count_prs(subjects: list[str]) -> int:
    return len({m.group(1) for s in subjects if (m := _PR_RE.search(s))})


def count_issues(bodies: list[str]) -> int:
    return len({n for body in bodies for n in _ISSUE_RE.findall(body)})


def is_test_path(path: str) -> bool:
    return bool(_TEST_RE.search(path))


def is_maintainer(login: str | None, name: str) -> bool:
    if login:
        return login.lower() in MAINTAINER_LOGINS
    return name.strip().lower() in MAINTAINER_NAMES


def is_bot(login: str | None, name: str) -> bool:
    return (login or name).lower().endswith("[bot]")


def split_first_time(
    handles: list[str], prior_commits: dict[str, int | None]
) -> tuple[list[str], list[str]]:
    """Split *handles* into (returning, first-time) by their commits before the
    previous tag. A handle whose count is unknown (lookup failed) is left out
    of both, so no one is welcomed twice or by mistake."""
    returning, first = [], []
    for handle in sorted(set(handles), key=str.lower):
        count = prior_commits.get(handle)
        if count is None:
            continue
        (returning if count > 0 else first).append(handle)
    return returning, first


def _kinds_line(t: dict) -> str:
    """``**165 fixes, 89 features**, 28 refactors and 6 documentation changes``,
    leaving out kinds with no commits (the JSON keeps the zeros)."""

    def _join(parts: list[str]) -> str:
        if len(parts) < 2:
            return "".join(parts)
        return ", ".join(parts[:-1]) + " and " + parts[-1]

    lead = [
        f"{t[k]:,} {name}"
        for k, name in (("fixes", "fixes"), ("features", "features"), ("perf", "performance changes"))
        if t.get(k)
    ]
    rest = [
        f"{t[k]:,} {name}"
        for k, name in (("refactors", "refactors"), ("docs", "documentation changes"))
        if t.get(k)
    ]
    if lead and rest:
        return f"**{', '.join(lead)}**, {_join(rest)}"
    if lead:
        return f"**{_join(lead)}**"
    return _join(rest)


def render_markdown(stats: dict) -> str:
    """The ``### By the numbers`` block for the CHANGELOG."""
    t = stats["types"]
    days = stats["days"]
    since = stats["since"]
    first = stats.get("first_time_contributors")
    people = f"**{stats['contributors']:,} contributors**"
    if first:
        people += f", **{first:,} of them first-time contributors**"
    lines = [
        "### By the numbers",
        "",
        f"In the {days:,} day{'s' if days != 1 else ''} since {since}:",
        "",
        f"- **{stats['prs']:,} pull requests** merged, from {people}",
        f"- **{stats['files_changed']:,} files** changed, "
        f"**+{stats['lines_added']:,} / -{stats['lines_removed']:,}** lines",
        *([f"- {kinds}"] if (kinds := _kinds_line(t)) else []),
        f"- **{stats['test_files_touched']:,} test files** touched, "
        f"**{stats['test_files_new']:,}** of them new",
        f"- **{stats['issues_closed']:,} issues** closed",
        "",
        "Thank you to everyone who built this release with us.",
    ]
    return "\n".join(lines)


def render_thanks(returning: list[str], first: list[str]) -> str:
    """The contributor thank-you for the release PR body (never bundled)."""

    def _join(handles: list[str]) -> str:
        tagged = [f"@{h}" for h in handles]
        return tagged[0] if len(tagged) == 1 else ", ".join(tagged[:-1]) + " and " + tagged[-1]

    out = []
    if returning:
        out.append(
            f"To our returning contributors: {_join(returning)}, thank you for coming back "
            "and raising the bar again."
        )
    if first:
        n = len(first)
        who = "person whose first contribution" if n == 1 else "people whose first contribution"
        out.append(
            f"And a special welcome to the **{n} {who} landed in this release**: "
            f"{_join(first)}. Glad to have you here."
        )
    return "\n\n".join(out)


# --- git and GitHub -------------------------------------------------------


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout


def _gh(path: str, jq: str) -> str | None:
    try:
        out = subprocess.run(
            ["gh", "api", path, "--jq", jq],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _commit_date(ref: str) -> datetime:
    return datetime.fromisoformat(_git("log", "-1", "--format=%cI", ref).strip())


def _resolve_handles(commits: list[dict[str, str]]) -> dict[str, str | None]:
    """Email to GitHub login, one lookup per distinct author email."""
    first_sha: dict[str, str] = {}
    for c in commits:
        first_sha.setdefault(c["email"], c["sha"])
    with ThreadPoolExecutor(max_workers=8) as pool:
        logins = pool.map(
            lambda sha: _gh(f"repos/{GITHUB_REPO}/commits/{sha}", ".author.login"),
            first_sha.values(),
        )
        return {
            email: (login if login and login != "null" else None)
            for email, login in zip(first_sha, logins, strict=True)
        }


def _prior_commit_counts(handles: list[str], until: str) -> dict[str, int | None]:
    def _one(handle: str) -> int | None:
        out = _gh(f"repos/{GITHUB_REPO}/commits?author={handle}&until={until}&per_page=1", "length")
        return int(out) if out and out.isdigit() else None

    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(zip(handles, pool.map(_one, handles), strict=True))


def _test_files(rng: str, *extra: str) -> int:
    return sum(1 for p in _git("diff", "--name-only", *extra, rng).splitlines() if is_test_path(p))


def _git_stats(prev: str, ref: str, commits: list[dict[str, str]]) -> dict:
    """Every figure git alone can give for ``prev..ref``."""
    rng = f"{prev}..{ref}"
    files, added, removed = parse_shortstat(_git("diff", "--shortstat", rng))
    subjects = [c["subject"] for c in commits]
    return {
        "since": prev.lstrip("v"),
        "days": max(0, (_commit_date(ref).date() - _commit_date(prev).date()).days),
        "prs": count_prs(subjects),
        "contributors": len({c["name"] for c in commits if not is_bot(None, c["name"])}),
        "first_time_contributors": None,
        "files_changed": files,
        "lines_added": added,
        "lines_removed": removed,
        "types": count_types(subjects),
        "test_files_touched": _test_files(rng),
        "test_files_new": _test_files(rng, "--diff-filter=A"),
        "issues_closed": count_issues([c["body"] for c in commits]),
    }


def _community_split(commits: list[dict[str, str]], until: str) -> tuple[list[str], list[str], bool]:
    """(returning, first-time, known) community handles, from GitHub."""
    by_email = _resolve_handles(commits)
    community = sorted(
        {
            login
            for c in commits
            if (login := by_email.get(c["email"]))
            and not is_maintainer(login, c["name"])
            and not is_bot(login, c["name"])
        }
    )
    if not community:
        return [], [], False
    prior = _prior_commit_counts(community, until)
    returning, first = split_first_time(community, prior)
    return returning, first, any(v is not None for v in prior.values())


def compute(prev: str, ref: str, *, github: bool) -> tuple[dict, list[str], list[str]]:
    """Figures for ``prev..ref`` plus the (returning, first-time) handle lists."""
    fmt = _FIELD_SEP.join(["%H", "%an", "%ae", "%s", "%B"]) + _COMMIT_SEP
    commits = parse_log(_git("log", f"--format={fmt}", f"{prev}..{ref}"))
    stats = _git_stats(prev, ref, commits)
    if not (github and shutil.which("gh")):
        return stats, [], []
    returning, first, known = _community_split(commits, _commit_date(prev).isoformat())
    if known:
        stats["first_time_contributors"] = len(first)
    return stats, returning, first


def write_json(version: str, stats: dict, path: Path = STATS_JSON) -> None:
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    if version != "unreleased":
        # Releasing turns the Unreleased figures into this version's.
        data.pop("unreleased", None)
    data[version] = stats
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _latest_tag(ref: str) -> str:
    return _git("describe", "--tags", "--abbrev=0", "--match", "v[0-9]*", ref).strip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--from", dest="prev", help="previous release tag (default: latest v* tag)")
    ap.add_argument("--to", dest="ref", default="HEAD", help="end of the range (default: HEAD)")
    ap.add_argument("--version", help="key for --write-json, e.g. 0.57.0 or unreleased")
    ap.add_argument("--write-json", action="store_true", help="store the figures in release_stats.json")
    ap.add_argument("--pr", action="store_true", help="also print the contributor thank-you")
    ap.add_argument("--no-github", action="store_true", help="skip the gh lookups")
    args = ap.parse_args(argv)
    if args.write_json and not args.version:
        ap.error("--write-json needs --version")

    prev = args.prev or _latest_tag(args.ref)
    stats, returning, first = compute(prev, args.ref, github=not args.no_github)
    print(render_markdown(stats))
    if args.pr:
        print("\n<!-- release PR body only -->\n")
        print(render_thanks(returning, first) or "(contributor handles unavailable: gh not reachable)")
    if args.write_json:
        write_json(args.version, stats)
        print(f"\nwrote {args.version} to {STATS_JSON.relative_to(ROOT)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
