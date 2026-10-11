"""Does this function check anything -- the one predicate both lanes ask.

Split out of ``lexicon.py`` because it is not vocabulary: it is language
agnostic, it reads a walker output rather than a name, and editing it moves
findings, which a dialect row can never do.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .lexicon import NARROW_PREFIXES

if TYPE_CHECKING:
    from ..complexity.models import FunctionComplexity


def checks_something(fn: FunctionComplexity) -> bool:
    """Whether a walked function carries an oracle of any kind.

    Four things fail a test: a state assertion, a mock verification, a
    ``raise`` / ``throw`` the author wrote by hand, and an assertion call in a
    position ``_assertion_tier`` never classifies.

    The third is in no assertion vocabulary, because a throw is a statement and
    the vocabularies match callee names, so a helper built on ``if (!ok) throw
    new Error(...)`` reads as checking nothing unless this is asked.

    The fourth is a floor under ``assertion_count``, not a second opinion on
    it. ``_assertion_tier`` classifies statements, so ``const e = expect(x)``
    is a declaration it declines and ``return expect(x).toBe(1)`` is a return
    it declines, while ``called_names`` records the call either way. Asking the
    name is coarser and can only ever say "checked something", never how much;
    counting those positions properly means admitting more statement kinds into
    ``_assertion_tier``, which moves every calibrated marker reading that
    count. Reading names also bounds the nested-body descent behind it: it
    finds a call, so an ``expect(...)`` inside an inline helper is seen and a
    Python ``assert`` there, having no callee, is not. Unobserved in anything
    labelled, so it is a stated limit rather than a thing to build for.

    Read only by ``assertion_free_test`` and the oracle resolution behind it,
    both as a boolean. Calibrated markers count ``assertion_count`` alone and
    are untouched by the last two terms.
    """
    if fn.assertion_count or fn.verification_count or fn.raise_count:
        return True
    return any(name.lstrip("_").startswith(NARROW_PREFIXES) for name in fn.called_names)
