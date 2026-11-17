"""Whether a model name reads as a flagship tier, for the picker's cost note."""

from __future__ import annotations

import re

# Checked before the flagship tokens: a cheap-tier marker wins even when the
# family name is a flagship one, so `gpt-5.4-nano` is not mistaken for `gpt-5`.
_BUDGET_MODEL_TOKENS = (
    "nano",
    # OpenAI's 5.6 budget tier carries no cheap-tier word in its name, and
    # `gpt-5` matches it as flagship. Without this, accepting the default
    # printed a note advising the user to switch to a cheaper model than the
    # one they were already on.
    "luna",
    "mini",
    "lite",
    "haiku",
    "small",
    "tiny",
    "flash",
    r"\d+b",  # 8b, 3b, 270m-style parameter-count tags
)

_FLAGSHIP_MODEL_TOKENS = (
    "opus",
    "gpt-4o",
    "gpt-5",
    "gpt-6",
    "pro",
    "sonnet",
    "ultra",
    "o1",
    "o3",
    "o4",
)


def _matches_token(model: str, tokens: tuple[str, ...]) -> bool:
    """True if any token appears in ``model`` as a whole dash/dot-delimited word.

    Substring matching is wrong here: ``gemini`` contains ``mini`` and
    ``gpt-5.4-nano`` contains ``gpt-5``.
    """
    return any(re.search(rf"(?<![a-z0-9]){tok}(?![a-z])", model) for tok in tokens)


def is_flagship_model(model: str) -> bool:
    """Heuristic: True if the model name suggests a flagship-tier model."""
    if not model:
        return False
    m = model.lower().rsplit("/", 1)[-1]
    if _matches_token(m, _BUDGET_MODEL_TOKENS):
        return False
    return _matches_token(m, _FLAGSHIP_MODEL_TOKENS)
