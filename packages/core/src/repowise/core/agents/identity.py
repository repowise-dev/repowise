"""One record per agent, and the only place an announced name becomes a slug.

The same agent used to be spelled four ways in four namespaces — ``claude-code``
as a ``--target`` id, ``claude-code`` as a hook adapter, ``claude_code`` as a
transcript adapter, and again in whatever the savings ledger happened to
enumerate. Each spelling was hand-maintained beside the others, which is the
defect even while they agree: agreement by hand is invisible when it breaks.

So one :class:`AgentIdentity` holds all of them and every namespace derives its
spelling from it.

Three shapes here are load-bearing:

**The canonical slug is the underscore form.** ``claude_code`` is already what
:mod:`repowise.core.sessions.adapters` uses and what the shipped
``pricing_agent`` wire field carries, it is a valid identifier, and it is a
clean column value. The hyphenated ``--target=`` id is *derived* from it rather
than declared, following :func:`~repowise.cli.agent_targets.types.derive_tier`:
a field a descriptor can set independently is a field that can disagree with the
thing it describes. The ceiling is an agent whose published target id is not the
hyphenated slug; the upgrade path is an explicit override field on this record,
added the day a second spelling is real rather than in anticipation of it.

**Aliases are mostly derived too.** What a host announces in MCP ``clientInfo``
is almost always its slug or its display name, so :attr:`AgentIdentity.aliases`
computes both and :attr:`announced_as` carries only the names neither spelling
reaches. A seventh agent therefore resolves correctly without anyone remembering
to write an alias down.

**Nothing downstream enumerates agents.** :func:`is_agent_slug` is the bounded
syntactic rule that storage applies — writer and reader alike — so an agent is
valid the day its descriptor lands and an event written by an agent since
retired still reads back. Membership in this registry decides only two things:
what an *announced* name resolves to, and what an agent is called on screen.
``unknown`` is what an unrecognised announcement becomes; it is never what a
stored slug is coerced to.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field

#: The attribution used when evidence is absent or unrecognised. Reserved: no
#: identity may claim it, because "we could not tell" and "this agent" have to
#: stay distinguishable.
UNKNOWN_AGENT = "unknown"

#: Version of the announced-name mapping, recorded on events that used it so a
#: later change to the alias rules is legible in stored data.
CLIENT_IDENTITY_MAPPING_VERSION = "mcp_client_info_v1"

#: Storage rule for an agent slug. Syntactic on purpose — see the module
#: docstring. Bounded so a column value cannot be a payload.
_SLUG_RE = re.compile(r"[a-z0-9_]{1,32}\Z")

#: Announced client names are compared with punctuation and case removed, so
#: ``Claude-Code``, ``claude code`` and ``ClaudeCode`` are one name. Bounded
#: before it reaches a lookup or a metadata field.
_ALIAS_LIMIT = 64


def is_agent_slug(value: object) -> bool:
    """Whether *value* is a well-formed agent slug.

    The whole of the validation an agent id gets in storage. Deliberately not a
    membership test: see the module docstring.
    """
    return isinstance(value, str) and _SLUG_RE.fullmatch(value) is not None


def normalize_client_name(value: object | None) -> str:
    """Reduce a self-declared client name to its comparable form.

    Empty for anything that carries no letters or digits, which the resolver
    treats as "not announced" rather than as a name it failed to recognise.
    """
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())[:_ALIAS_LIMIT]


@dataclass(frozen=True, slots=True)
class AgentIdentity:
    """Who one agent is, in every namespace that needs to name it."""

    #: Canonical, underscore form. The value stored in the savings ledger and
    #: carried on the wire.
    slug: str
    #: What a human sees. The source an agent's label should come from; the
    #: report payload carries it so no consumer needs a label map of its own.
    #: (A few pre-existing UI maps still spell labels by hand and are folded in
    #: when their surfaces are rebuilt, not before.)
    display_name: str
    #: Names a host announces in MCP ``clientInfo`` that neither the slug nor
    #: the display name normalizes to. Usually empty.
    announced_as: frozenset[str] = field(default_factory=frozenset)
    #: Entry in ``repowise.cli.agent_adapters`` / ``core.sessions.adapters``.
    #: ``None`` means this agent has no such surface, which is what keeps it out
    #: of the Full tier. Declared rather than derived: the presence of these is
    #: an independent fact about what is implemented, which is exactly why
    #: ``derive_tier`` reads them.
    hook_adapter: str | None = None
    session_adapter: str | None = None

    def __post_init__(self) -> None:
        if not is_agent_slug(self.slug):
            raise ValueError(f"agent slug must match {_SLUG_RE.pattern}: {self.slug!r}")
        if self.slug == UNKNOWN_AGENT:
            raise ValueError(f"{UNKNOWN_AGENT!r} is reserved for unrecognised attribution")
        if not self.display_name:
            raise ValueError(f"{self.slug} needs a display name")
        if isinstance(self.announced_as, str):
            # A bare string is iterable, so this would otherwise register one
            # alias per character and answer to "e".
            raise ValueError(f"{self.slug} announced_as must be a set of names, not a string")
        for announced in self.announced_as:
            if announced != normalize_client_name(announced) or not announced:
                raise ValueError(f"{self.slug} alias {announced!r} is not in normalized form")
        if UNKNOWN_AGENT in self.aliases:
            raise ValueError(f"{self.slug} may not answer to {UNKNOWN_AGENT!r}")

    @property
    def cli_target_id(self) -> str:
        """The spelling ``--target=`` publishes, and the registry keys on."""
        return self.slug.replace("_", "-")

    @property
    def aliases(self) -> frozenset[str]:
        """Every announced name that resolves to this agent."""
        return frozenset(
            {normalize_client_name(self.slug), normalize_client_name(self.display_name)}
            | set(self.announced_as)
        )


CLAUDE_CODE = AgentIdentity(
    slug="claude_code",
    display_name="Claude Code",
    #: The host announces the product, not the CLI, in some versions.
    announced_as=frozenset({"claude"}),
    hook_adapter="claude-code",
    session_adapter="claude_code",
)
CODEX = AgentIdentity(
    slug="codex",
    display_name="Codex CLI",
    hook_adapter="codex",
    session_adapter="codex",
)
VSCODE = AgentIdentity(slug="vscode", display_name="VS Code")
CURSOR = AgentIdentity(slug="cursor", display_name="Cursor")
OPENCODE = AgentIdentity(slug="opencode", display_name="OpenCode")
HERMES = AgentIdentity(slug="hermes", display_name="Hermes")

#: Registered identities, by slug. Order is not load-bearing here — the order
#: agents are *presented* in belongs to the target registry, which is where a
#: reader already looks for it.
_REGISTERED: dict[str, AgentIdentity] = {}

#: Announced name to slug, rebuilt on every registration and rebound as a whole.
#:
#: A prebuilt dict rather than a memoized lookup, because the registry is
#: mutable and the readers are concurrent. A ``functools.lru_cache`` computes
#: outside its own lock, so a thread that missed before a registration can store
#: its stale ``unknown`` after the invalidation and answer wrongly forever; and
#: a reader iterating the registry while another thread registers raises
#: ``dictionary changed size during iteration`` inside a request. Rebinding one
#: immutable snapshot has neither failure mode, and a dict lookup is cheaper
#: than a cache hit anyway.
_ALIAS_INDEX: dict[str, str] = {}

#: Held only by the two mutators, which run at import and in tests.
_REGISTRY_LOCK = threading.Lock()


def _rebuild_alias_index() -> None:
    """Rebuild and atomically rebind the index. Caller holds the lock."""
    global _ALIAS_INDEX
    _ALIAS_INDEX = {
        alias: identity.slug for identity in _REGISTERED.values() for alias in identity.aliases
    }


def register_identity(identity: AgentIdentity) -> AgentIdentity:
    """Register *identity*, replacing any prior record for the same slug.

    The seam a third-party agent package registers through, and the one tests
    use to prove a seventh agent needs no edit anywhere downstream. Returns the
    identity so it can decorate a declaration.
    """
    with _REGISTRY_LOCK:
        conflicts = {
            alias: existing.slug
            for existing in _REGISTERED.values()
            if existing.slug != identity.slug
            for alias in identity.aliases & existing.aliases
        }
        if conflicts:
            raise ValueError(f"{identity.slug} claims aliases already owned: {sorted(conflicts)}")
        _REGISTERED[identity.slug] = identity
        _rebuild_alias_index()
    return identity


def unregister_identity(slug: str) -> None:
    """Drop a registered identity. For tests that register a fake agent."""
    with _REGISTRY_LOCK:
        _REGISTERED.pop(slug, None)
        _rebuild_alias_index()


def all_identities() -> tuple[AgentIdentity, ...]:
    """Every registered identity, in registration order."""
    return tuple(_REGISTERED.values())


def get_identity(slug: str) -> AgentIdentity | None:
    """The identity for *slug*, or ``None`` when no agent claims it."""
    return _REGISTERED.get(slug)


def slug_for_hook_adapter(name: str | None) -> str:
    """The agent behind a hook adapter's name, or :data:`UNKNOWN_AGENT`.

    The hook path knows exactly which agent it is serving — it was handed that
    agent's adapter — so attribution there is evidence rather than inference.
    This is the lookup that turns the adapter's own spelling back into the
    canonical one, instead of a caller re-deriving it.
    """
    if not name:
        return UNKNOWN_AGENT
    for identity in _REGISTERED.values():
        if identity.hook_adapter == name:
            return identity.slug
    return UNKNOWN_AGENT


def identity_for_target_id(target_id: str) -> AgentIdentity | None:
    """The identity behind a ``--target=`` id."""
    return get_identity(target_id.replace("-", "_"))


def display_name_for(slug: str) -> str:
    """What to call *slug* on screen.

    An unregistered slug — a retired agent whose events are still in the ledger
    — is returned verbatim. Guessing a prettier label for an agent we no longer
    describe would be inventing evidence.
    """
    identity = _REGISTERED.get(slug)
    return identity.display_name if identity else slug


def resolve_client_identity(client_name: str | None) -> str:
    """Resolve an announced MCP ``clientInfo`` name to a slug.

    The only place anything maps to :data:`UNKNOWN_AGENT`. Absent, empty and
    unrecognised names all resolve there — never to whichever agent happens to
    be the CLI's auto-detection fallback, which would attribute one host's
    traffic to another.

    Two operations, no allocation beyond the normalized name, so a caller on the
    MCP hot path pays almost nothing. It should still resolve once per session
    rather than once per event, because the announced name cannot change within
    one session.
    """
    return _ALIAS_INDEX.get(normalize_client_name(client_name), UNKNOWN_AGENT)


#: The agents shipped with repowise, registered through the same seam a
#: third-party package would use, so the alias-disjointness check covers them too.
for _shipped in (CLAUDE_CODE, CODEX, VSCODE, CURSOR, OPENCODE, HERMES):
    register_identity(_shipped)
del _shipped
