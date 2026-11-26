"""Setup prompts: the whole pass (FINISH_SETUP), its workspace form, and one step.

FINISH_SETUP hands an agent the ``repowise next`` loop, as CONNECT_EVIDENCE
hands it the ``evidence setup`` loop: the commands come from that output, so
the text names none. ``repowise next --prompt setup`` prints it, the web's
Setup hero copies it, and ``packages/ui`` mirrors :data:`FINISH_SETUP` in
``health/ai-prompts/setup-prompt.ts``, held equal by a parity test.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .evidence import render_connect_evidence
from .preamble import ROLE, WORKSPACE_ROLE
from .sections import repo_suffix

FINISH_SETUP = (
    "Finish setting up Repowise for this repository. From the repository root, run "
    "`repowise next --format json`. Work only through the actions whose tier is "
    "`improve_signal`, in the order printed. Each one has a `command`, or a `next_call` "
    "with the directory (`cwd`) it runs in. Run it exactly as printed, without changing "
    "its flags. If a step fails, read the error, fix the cause (a missing tool or "
    "dependency) and run it again once. After each action, run `repowise next --format "
    "json` again and continue with what it prints. Stop when no `improve_signal` action "
    "is left, or when it repeats one you already ran.\n"
    "Do not spend money, and do not turn on anything that sends code to a model, without "
    "asking. If an action needs a model or would spend, ask me one question, once, "
    "listing the options it prints (for example a signed-in Claude Code or a local "
    "Ollama), and skip it if I say no. Ask me nothing else.\n"
    "When you are done, reply in at most six lines: what you turned on, what you skipped "
    "and why, anything I need to restart, and the one action from `repowise next` worth "
    "doing first.\n"
    "If I decline before you start, run `repowise next --dismiss setup` so Repowise stops "
    "offering this."
)

#: Ahead of :data:`FINISH_SETUP` in a workspace: the same loop, member by member.
WORKSPACE_LEAD = (
    "Setup is not finished in these repositories of this workspace: {names}. Go into "
    "each one's directory in turn and finish its setup as below. Ask your one question once "
    "for the whole workspace, and send one reply at the end."
)

#: The rules whose one step is the evidence loop.
EVIDENCE_RULES = frozenset({"evidence_found", "runtime_missing", "runtime_stale"})
#: Every rule whose row prompt is a setup step, not a code change: the live
#: setup rules (``analysis.actions.rules.setup``, a test holds the two equal)
#: and the evidence rules.
SETUP_STEP_RULES = EVIDENCE_RULES | {
    "sync_hook_missing",
    "coverage_found",
    "decisions_unchecked",
    "docs_model_ready",
    "settings_suggested",
    "always_load_suggested",
    "distill_suggested",
}


def render_finish_setup(flavor: str = "generic") -> str:
    """The pass for one repository: who the agent is, then the ``repowise next`` loop."""
    return f"{ROLE[flavor]}\n\n{FINISH_SETUP}"


def render_finish_setup_workspace(paths: Sequence[str], flavor: str = "generic") -> str:
    """The pass across a workspace's indexed members, named by their directories."""
    names = ", ".join(f"`{p}`" for p in paths)
    lead = WORKSPACE_LEAD.format(names=names)
    return f"{WORKSPACE_ROLE[flavor]}\n\n{lead}\n\n{FINISH_SETUP}"


def render_setup_step(
    action: Mapping[str, Any], flavor: str = "generic", repo_name: str | None = None
) -> str:
    """One setup row as a prompt: what Repowise found, the command, how to check.

    Takes the action's wire dict. The evidence rules hand over the evidence
    loop itself (:data:`~.evidence.CONNECT_EVIDENCE`).
    """
    if action["rule"] in EVIDENCE_RULES:
        return render_connect_evidence(flavor)
    found = "; ".join(f"{w['label']}: {w['value']}" for w in action.get("why") or [])
    lines = [
        f"Repowise suggests this setup step{repo_suffix(repo_name)}: {action['title']}. "
        f"{action['impact']}" + (f" ({found}.)" if found else ""),
        f"From the repository root, run `{action['command']}`. If it prints a `next_call`, "
        "follow it as printed. If the step needs a model or would spend money, ask me "
        "first and skip it if I say no.",
        f"Done when: {action['done_when']} Then run `repowise next --format json` and "
        "tell me whether the step has left the list.",
    ]
    return "\n\n".join([ROLE[flavor], *lines])


__all__ = [
    "EVIDENCE_RULES",
    "FINISH_SETUP",
    "SETUP_STEP_RULES",
    "render_finish_setup",
    "render_finish_setup_workspace",
    "render_setup_step",
]
