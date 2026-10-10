"""Author identity: noreply folding and automation accounts, across forges.

An address says which forge minted it, whatever host the repo lives on now: a
GitLab mirror of a GitHub project still carries GitHub noreply commits. So an
identity is read against every registered forge's rules, and
``Forge.normalize_identity`` gives the same answer on every forge.

Precision first: a person read as a bot, or two people folded into one key, is
worse than a missed fold. Only a forge's provable noreply form folds, and a bot
name matches as an explicit marker or a full service name, never a substring.
"""

from __future__ import annotations

import re
from functools import lru_cache

from .base import ForgeKind
from .registry import all_forges

# Automation that commits to any host. Either an explicit bot marker or a
# service name matched in full, so "Netlify Johnson" stays a person.
_BOT_NAME_RE = re.compile(
    r"(\[bot\]"
    r"|^bot$"
    r"|[-_ ]bot$"
    r"|^(dependabot|renovate(bot)?|greenkeeper|snyk([-_ ]bot)?|imgbot|"
    r"semantic[-_ ]release|allcontributors|codecov|mergify|"
    r"pre[-_ ]commit[-_ ]ci|netlify|vercel)$)",
    re.IGNORECASE,
)
_BOT_EMAIL_RE = re.compile(r"\[bot\]@", re.IGNORECASE)


# Git history repeats a few hundred addresses over every commit and file, so
# the per-address answers are cached; ``register`` clears them.
@lru_cache(maxsize=8192)
def noreply_login(email: str | None) -> tuple[ForgeKind, str] | None:
    """``(forge, login)`` when *email* is a forge's noreply address for a login."""
    lowered = (email or "").strip().lower()
    for forge in all_forges():
        m = forge.noreply_re.match(lowered) if forge.noreply_re else None
        if m:
            return forge.kind, m.group("login")
    return None


@lru_cache(maxsize=8192)
def canonical_email(email: str | None) -> str:
    """*email* lowercased, with a forge's noreply variants of one login folded."""
    lowered = (email or "").strip().lower()
    for forge in all_forges():
        m = forge.noreply_re.match(lowered) if forge.noreply_re else None
        if m:
            return m.expand(forge.noreply_fold) if forge.noreply_fold else lowered
    return lowered


def is_bot(name: str | None, email: str | None) -> bool:
    """Whether the author is automation, by name or address, on any forge."""
    checks = [(_BOT_NAME_RE, name), (_BOT_EMAIL_RE, email)]
    for forge in all_forges():
        checks += [(forge.bot_name_re, name), (forge.bot_email_re, email)]
    return any(pattern and value and pattern.search(value) for pattern, value in checks)


def clear_caches() -> None:
    noreply_login.cache_clear()
    canonical_email.cache_clear()


def normalize_identity(email: str | None, name: str | None) -> tuple[str, bool]:
    """``(canonical_email, is_bot)`` for one author."""
    return canonical_email(email), is_bot(name, email)
