"""Author-identity canonicalization shared across the ingestion + serving paths.

GitHub stamps commits made through its web UI, squash-merges and PR merges with
a synthetic *noreply* address instead of the author's real email:
``NNN+login@users.noreply.github.com`` (numeric-id prefixed) or the older
``login@users.noreply.github.com`` form. The numeric id and the exact shape
vary between commits, so the same person fans out into several contributor
buckets whenever identity is keyed on the raw email.

:func:`canonicalize_author_email` folds every noreply variant for a login onto
one stable key so those buckets collapse. The GitHub *system* author
``noreply@github.com`` (stamped on some merge commits) is left untouched on
purpose — it stays its own bucket and is never merged into a human contributor.

:func:`build_identity_resolver` goes further across a whole repo's authors,
merging emails into people by union-find over a few evidence edges (see its
docstring). False merges are worse than splits, so every edge beyond an exact
email match is guarded and can be rolled back.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from functools import lru_cache
from typing import Literal

IdentityKind = Literal["human", "agent", "bot"]

_NOREPLY_DOMAIN = "@users.noreply.github.com"

# ``NNN+login@users.noreply.github.com`` or the older ``login@...`` form.
# The numeric id and the prefix are optional; the login is everything before
# the ``@`` (minus the ``NNN+`` id). Case-insensitive to match git's casing.
_GH_NOREPLY_RE = re.compile(
    r"^(?:\d+\+)?(?P<login>[^@\s+]+)@users\.noreply\.github\.com$",
    re.IGNORECASE,
)

# Automation, excluded from people counts. Either an explicit bot marker or a
# service name matched in full, so "Netlify Johnson" stays a person.
_BOT_NAME_RE = re.compile(
    r"(\[bot\]"
    r"|^bot$"
    r"|[-_ ]bot$"
    r"|^(dependabot|renovate(bot)?|greenkeeper|snyk([-_ ]bot)?|imgbot|"
    r"github[-_ ]?actions|semantic[-_ ]release|allcontributors|codecov|mergify|"
    r"pre[-_ ]commit[-_ ]ci|netlify|vercel)$)",
    re.IGNORECASE,
)
_BOT_EMAIL_RE = re.compile(
    r"(\[bot\]@|@bots\.noreply\.github\.com|^(actions@github\.com|"
    r"noreply@github\.com)$)",
    re.IGNORECASE,
)

# A noreply login this generic names a machine or a shared account, not a
# person, so it never bridges to a real email by display name. Human first
# names never belong here: a short or common login is handled by the length
# floor and the per-login email cap instead.
_SYSTEM_LOGINS = frozenset({"root", "admin", "user", "ubuntu", "runner", "git", "dev", "test"})
_MIN_BRIDGE_LOGIN_LEN = 4
# A login whose name matches more real emails than this is a common word, not
# one person's handle.
_MAX_EMAILS_PER_LOGIN = 3

# Host labels that only exist on a local network or a single machine
# (``user@Users-MacBook-Pro.local``, ``me@box.localdomain``).
_LOCAL_HOST_LABELS = frozenset({"local", "localdomain", "localhost", "lan", "home", "internal"})


def canonicalize_author_email(email: str | None) -> str | None:
    """Return a stable identity email, folding GitHub noreply variants.

    ``NNN+login@users.noreply.github.com`` and ``login@users.noreply.github.com``
    both collapse to ``login@users.noreply.github.com`` (lower-cased) so every
    noreply variant of one login shares a key. Any other address — including the
    ``noreply@github.com`` system author — is returned lower-cased and otherwise
    unchanged. ``None``/empty passes through unchanged so callers can keep their
    existing "no email → fall back to name" handling.
    """
    if not email:
        return email
    lowered = email.strip().lower()
    m = _GH_NOREPLY_RE.match(lowered)
    if m:
        return f"{m.group('login')}{_NOREPLY_DOMAIN}"
    return lowered


def author_identity_key(author_name: str | None, author_email: str | None) -> str:
    """Stable per-person key for counting commits.

    Folds noreply variants onto one login first, falling back to the display
    name when there is no usable email. Anything that tallies commits per author
    must key on this, or one person splits across buckets and every count that
    depends on it (author experience, "is this a new contributor") comes out low
    for exactly the people who commit through GitHub's web UI.
    """
    canonical = canonicalize_author_email(author_email) or author_email
    return (canonical or author_name or "").strip().lower()


@lru_cache(maxsize=4096)
def _identity(name: str | None, email: str | None) -> tuple[IdentityKind, str | None]:
    """``(kind, agent label)`` for one author observation."""
    # Imported here: the provenance module loads the whole git indexer package.
    from repowise.core.ingestion.git_indexer.agent_provenance import agent_from_identity

    agent = agent_from_identity(name, email)
    if agent:
        return "agent", agent
    if (name and _BOT_NAME_RE.search(name)) or (email and _BOT_EMAIL_RE.search(email)):
        return "bot", None
    return "human", None


def identity_kind(name: str | None, email: str | None) -> IdentityKind:
    """Who an author identity is: a coding agent, other automation, or a person.

    Identity only: an agent-*assisted* commit still has a human author.
    """
    return _identity(name, email)[0]


def is_bot(name: str | None, email: str | None) -> bool:
    """True when this author is automation (CI or a coding agent's own identity)."""
    return identity_kind(name, email) != "human"


def _is_machine_local(canonical_email: str) -> bool:
    """An address git made up from a hostname: ``(none)``, ``localhost``, a
    ``.local`` host, or a domain with no dot (so no public TLD)."""
    domain = canonical_email.rpartition("@")[2].strip("()")
    if not domain or domain == "none" or "." not in domain:
        return True
    return any(label in _LOCAL_HOST_LABELS for label in domain.split("."))


def _clean_name(name: str | None) -> str:
    return " ".join((name or "").split())


def _node(name: str | None, email: str | None) -> str:
    """An observation's node: its canonical email, else ``name:<name>``."""
    canon = canonicalize_author_email(email)
    if canon:
        return canon.strip().lower()
    if name and name.strip():
        return f"name:{name.strip()}"
    return ""


class _UnionFind:
    def __init__(self, items: Iterable[str]) -> None:
        self.parent = {x: x for x in items}

    def find(self, x: str) -> str:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        self.parent[self.find(a)] = self.find(b)

    def groups(self) -> list[list[str]]:
        out: dict[str, list[str]] = defaultdict(list)
        for x in self.parent:
            out[self.find(x)].append(x)
        return list(out.values())


class IdentityResolver:
    """``resolve(name, email) -> identity key`` over one repo's authors.

    Keys match ``owner_key``'s shape: a lower-cased email (the identity's most
    used real email), or ``name:<name>`` when no email is known. An author the
    resolver was not built from resolves to its own canonical email.
    """

    def __init__(
        self,
        key_of: dict[str, str],
        names: dict[str, Counter[str]],
        kinds: dict[str, IdentityKind],
        agents: dict[str, str],
    ):
        self._key_of = key_of
        self._names = names
        # Automation identities only; every other key is a person.
        self._kinds = kinds
        self._agents = agents

    def __call__(self, name: str | None, email: str | None) -> str:
        node = _node(name, email)
        return self._key_of.get(node, node)

    def display_name(self, key: str) -> str:
        """The identity's most frequent name, preferring names of 2+ words."""
        names = self._names.get(key)
        if not names:
            return key.removeprefix("name:")
        pool = Counter({n: c for n, c in names.items() if len(n.split()) >= 2}) or names
        # Ties break alphabetically so the name is stable across runs.
        return min(pool, key=lambda n: (-pool[n], n))

    def kind(self, key: str) -> IdentityKind:
        return self._kinds.get(key, "human")

    def agent_of(self, key: str) -> str | None:
        """The coding agent behind an agent identity, else ``None``."""
        return self._agents.get(key)

    def is_bot(self, key: str) -> bool:
        return key in self._kinds

    def people(self) -> set[str]:
        """Every human identity the resolver was built from."""
        return set(self._names) - set(self._kinds)


def build_identity_resolver(
    pairs: Iterable[tuple[str | None, str | None]],
    evidence: Iterable[tuple[str | None, str | None]] = (),
) -> IdentityResolver:
    """Merge a repo's author ``(name, email)`` observations into people.

    Pass one pair per observation (a commit, or a file's author entry): the
    counts pick each identity's key and display name. Nodes are canonical
    emails, which already folds the exact edges:

    - **E1** the same email under any name or casing is one node;
    - **E2** every noreply variant of one login is one node.

    Union-find then adds the guarded edges:

    - **E3** a noreply login joins the real email of the same display name,
      when that name has 2+ words and exactly one real email;
    - **E4** a noreply login ``L`` joins every real email whose display name is
      ``L`` (case-insensitive), when ``L`` has 4+ characters, is not a system
      account, and names at most 3 real emails;
    - **E5** a machine-local email (``*.local``, ``localhost``, ``(none)``, no
      public TLD) joins the single real email of the same 2+ word name.

    Guardrails, since a false merge is worse than a split: the same display
    name alone never joins two real emails; a node whose names point E3/E5 at
    two different real emails takes neither; bots take no E3-E5 edge; and an
    identity that would hold two different noreply logins has its E3-E5 edges
    rolled back. (Numeric ids are not compared on their own: E2 already folds
    one login's re-issued ids, and two logins are caught by login.)

    ``evidence`` pairs (``Co-authored-by`` trailers) never create a node or an
    edge: they only add their name to the display-name tally of an identity
    whose email the author pairs already know.
    """
    names: dict[str, Counter[str]] = defaultdict(Counter)
    kind_votes: dict[str, Counter[str]] = defaultdict(Counter)
    agent_votes: dict[str, Counter[str]] = defaultdict(Counter)
    identity_cache: dict[tuple[str | None, str | None], tuple[IdentityKind, str | None]] = {}
    logins: dict[str, str] = {}
    for name, email in pairs:
        node = _node(name, email)
        if not node:
            continue
        names[node][_clean_name(name) or node] += 1
        if (name, email) not in identity_cache:
            identity_cache[(name, email)] = _identity(name, email)
        obs_kind, agent = identity_cache[(name, email)]
        kind_votes[node][obs_kind] += 1
        if agent:
            agent_votes[node][agent] += 1
        m = _GH_NOREPLY_RE.match((email or "").strip().lower())
        if m:
            logins[node] = m.group("login")
    # A node is automation when most of its observations say so (a tie stays
    # human); an agent/bot tie goes to the more specific agent.
    auto_kinds: dict[str, IdentityKind] = {}
    for node, votes in kind_votes.items():
        if votes["agent"] + votes["bot"] > votes["human"]:
            auto_kinds[node] = "agent" if votes["agent"] >= votes["bot"] else "bot"
    bots = set(auto_kinds)

    def kind(node: str) -> str:
        if node.startswith("name:"):
            return "name"
        if node in logins:
            return "noreply"
        return "local" if _is_machine_local(node) else "real"

    kinds = {n: kind(n) for n in names}
    real_by_name: dict[str, set[str]] = defaultdict(set)
    for node, counter in names.items():
        if kinds[node] == "real" and node not in bots:
            for n in counter:
                real_by_name[n.lower()].add(node)

    soft: list[tuple[str, str]] = []
    for node, counter in names.items():
        if node in bots or kinds[node] not in ("noreply", "local"):
            continue
        # E3 (noreply) and E5 (machine-local): the one real email of that
        # name. A one-word name ("Kirill", "Samuel") is shared by too many
        # people to vouch for anyone; a handle-like one is E4's job.
        by_name: set[str] = set()
        for n in counter:
            if len(n.split()) < 2:
                continue
            reals = real_by_name.get(n.lower(), set())
            if len(reals) == 1:
                by_name |= reals
        # A shared alias seen under two people's names would bridge them both.
        if len(by_name) == 1:
            soft.append((node, next(iter(by_name))))
        # E4: the login is the display name of a few real emails.
        login = logins.get(node, "")
        reals = real_by_name.get(login, set())
        if (
            len(login) >= _MIN_BRIDGE_LOGIN_LEN
            and login not in _SYSTEM_LOGINS
            and len(reals) <= _MAX_EMAILS_PER_LOGIN
        ):
            soft.extend((node, r) for r in reals)

    def merge(edges: list[tuple[str, str]]) -> _UnionFind:
        uf = _UnionFind(names)
        for a, b in edges:
            uf.union(a, b)
        return uf

    uf = merge(soft)
    conflicted = set()
    for nodes in uf.groups():
        if len({logins[n] for n in nodes if n in logins}) > 1:
            conflicted.add(uf.find(nodes[0]))
    if conflicted:
        uf = merge([(a, b) for a, b in soft if uf.find(a) not in conflicted])

    rank = {"real": 0, "noreply": 1, "local": 2, "name": 3}
    key_of: dict[str, str] = {}
    merged: dict[str, Counter[str]] = {}
    for nodes in uf.groups():
        key = min(nodes, key=lambda n: (rank[kinds[n]], -names[n].total(), n))
        merged[key] = sum((names[n] for n in nodes), Counter())
        key_of.update(dict.fromkeys(nodes, key))

    for name, email in evidence:
        key = key_of.get(_node(None, email))
        if key and _clean_name(name):
            merged[key][_clean_name(name)] += 1

    # Bots never merge, so a bot's identity is its own node.
    agents = {
        n: min(agent_votes[n], key=lambda a: (-agent_votes[n][a], a))
        for n, k in auto_kinds.items()
        if k == "agent" and agent_votes[n]
    }
    return IdentityResolver(key_of, merged, auto_kinds, agents)
