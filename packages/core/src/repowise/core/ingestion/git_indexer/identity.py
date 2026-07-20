"""Author-identity canonicalization shared across the ingestion + serving paths.

GitHub stamps commits made through its web UI, squash-merges and PR merges with
a synthetic *noreply* address instead of the author's real email:
``NNN+login@users.noreply.github.com`` (numeric-id prefixed) or the older
``login@users.noreply.github.com`` form. The numeric id and the exact shape
vary between commits, so the same person fans out into several contributor
buckets whenever identity is keyed on the raw email.

:func:`canonicalize_author_email` folds every noreply variant for a login onto
one stable key so those buckets collapse. It is deliberately the *simple*
version: it only unifies the noreply forms of a single login. The GitHub
*system* author ``noreply@github.com`` (stamped on some merge commits) is left
untouched on purpose — it stays its own bucket and is never merged into a human
contributor. Bridging a person's noreply identity to their *real* email when the
two share only a display name is the thorough version, out of scope here.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

_NOREPLY_DOMAIN = "@users.noreply.github.com"

# ``NNN+login@users.noreply.github.com`` or the older ``login@...`` form.
# The numeric id and the prefix are optional; the login is everything before
# the ``@`` (minus the ``NNN+`` id). Case-insensitive to match git's casing.
_GH_NOREPLY_RE = re.compile(
    r"^(?:\d+\+)?(?P<login>[^@\s+]+)@users\.noreply\.github\.com$",
    re.IGNORECASE,
)


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


def _is_github_noreply(canonical_email: str | None) -> bool:
    """True for a ``login@users.noreply.github.com`` identity (not the system
    ``noreply@github.com`` author, which lives on a different domain)."""
    return bool(canonical_email) and canonical_email.endswith(_NOREPLY_DOMAIN)


def build_identity_resolver(
    pairs: Iterable[tuple[str | None, str | None]],
) -> Callable[[str | None, str | None], str]:
    """Return ``resolve(name, email) -> identity key`` for a whole repo's authors.

    Canonicalizes each email (folding GitHub noreply *variants* of one login),
    then does the same-display-name merge the simple dedup calls for: a person
    who shows up both with a real email and with a ``NNN+login@users.noreply``
    email under the **same display name** collapses to one identity. Only the
    noreply side is redirected, and only when that name maps to exactly **one**
    real email (unambiguous) — so two distinct real emails under one name, or a
    name<->login bridge across *different* names, are left split on purpose.

    Keys match ``owner_key``'s shape: a lower-cased email, or ``name:<name>``
    when no email is known.
    """
    real_by_name: dict[str, set[str]] = {}
    for name, email in pairs:
        canon = canonicalize_author_email(email)
        if canon and not _is_github_noreply(canon) and name and name.strip():
            real_by_name.setdefault(name.strip().lower(), set()).add(canon)
    # Fold only when a display name resolves to a single real email.
    fold = {n: next(iter(s)) for n, s in real_by_name.items() if len(s) == 1}

    def resolve(name: str | None, email: str | None) -> str:
        canon = canonicalize_author_email(email)
        if canon:
            if _is_github_noreply(canon) and name and name.strip():
                real = fold.get(name.strip().lower())
                if real:
                    return real
            return canon.strip().lower()
        if name and name.strip():
            return f"name:{name.strip()}"
        return ""

    return resolve
