"""First questions to ask a newly connected agent, built from index facts.

Each names Repowise, so the agent reaches for its tools, and names the repo
or one of its files, so the first answer is about the user's own code. No
model call: the facts are ones the index already holds. ``packages/ui``
mirrors :data:`FIRST_PROMPTS` in ``onboarding/first-prompts.ts``, held equal
by a parity test.
"""

from __future__ import annotations

#: Templates by name. ``{repo}`` is the repository, ``{path}`` a file in it,
#: ``{title}`` a recorded decision's title, ``{repos}`` a workspace's repos.
FIRST_PROMPTS: dict[str, str] = {
    "tour": (
        "Use Repowise to give me a tour of {repo}: what it does, its main modules, "
        "and where I should start reading."
    ),
    "hotspot": (
        "Before I change {path}, use Repowise to tell me what depends on it, "
        "who knows it best, and what tends to break with it."
    ),
    "worst": (
        "Use Repowise to explain why {path} is the least healthy file in {repo}, "
        "and how to improve it safely."
    ),
    "risky": "Use Repowise to find the riskiest files to change in {repo}, and tell me why.",
    "decision": (
        'Use Repowise to explain the decision "{title}" in {repo}: why it was made '
        "and which code follows it."
    ),
    "unused": (
        "Use Repowise to find code in {repo} that nothing calls any more and is safe to delete."
    ),
    "workspace": (
        "Use Repowise to show me how {repos} depend on each other: the contracts "
        "between them and which changes ripple."
    ),
}

#: A workspace prompt names its repos up to this many; past it, it counts them.
_NAMED_REPOS = 3


def first_prompts(
    repo: str,
    *,
    hotspot: str | None = None,
    worst: str | None = None,
    decision: str | None = None,
) -> list[str]:
    """Three prompts for *repo*: a tour, a risk question and a why question.

    *hotspot* is the busiest file, *worst* the least healthy one and
    *decision* a recorded decision's title; each is optional, and a missing
    fact falls back to a question any indexed repo can answer.
    """
    risk = (
        FIRST_PROMPTS["hotspot"].format(path=hotspot)
        if hotspot
        else FIRST_PROMPTS["worst"].format(path=worst, repo=repo)
        if worst
        else FIRST_PROMPTS["risky"].format(repo=repo)
    )
    why = (
        FIRST_PROMPTS["decision"].format(title=decision, repo=repo)
        if decision
        else FIRST_PROMPTS["unused"].format(repo=repo)
    )
    return [FIRST_PROMPTS["tour"].format(repo=repo), risk, why]


def first_prompt(repo: str, *, hotspot: str | None = None) -> str:
    """The one question a finish screen leads with.

    The busiest file when one was measured, since the answer is about code the
    user is about to touch; else the tour of *repo*.
    """
    if hotspot:
        return FIRST_PROMPTS["hotspot"].format(path=hotspot)
    return FIRST_PROMPTS["tour"].format(repo=repo)


def workspace_prompt(repos: list[str]) -> str:
    """The cross-repo question for a workspace of *repos*; one repo gets its tour."""
    if len(repos) == 1:
        return FIRST_PROMPTS["tour"].format(repo=repos[0])
    if len(repos) > _NAMED_REPOS:
        names = f"the {len(repos)} repos in this workspace"
    else:
        names = f"{', '.join(repos[:-1])} and {repos[-1]}"
    return FIRST_PROMPTS["workspace"].format(repos=names)


__all__ = ["FIRST_PROMPTS", "first_prompt", "first_prompts", "workspace_prompt"]
