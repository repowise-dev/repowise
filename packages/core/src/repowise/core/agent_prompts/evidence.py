"""The "Connect evidence" prompt: hand an agent the setup loop, not stack commands.

The agent runs ``repowise evidence setup --format json`` and follows its
``next_call``; the commands themselves come from that output, so this text
names none. ``packages/ui`` mirrors :data:`CONNECT_EVIDENCE` in
``health/ai-prompts/setup-prompt.ts``, held equal by a parity test.
"""

from __future__ import annotations

from .preamble import ROLE

CONNECT_EVIDENCE = (
    "Connect runtime evidence to Repowise for this repository. From the repository root, "
    "run `repowise evidence setup --format json`. It prints, for each build unit, how to "
    "collect coverage and, where it can, profiles, and a `next_call`: the steps to run "
    "next, each with the directory (`cwd`) it runs in. Run those steps in order, exactly as "
    "printed, without changing their flags. If a step fails, read the error, fix the cause "
    "(a missing tool or dependency) and run the step again. When the steps are done, run "
    "`repowise evidence setup --format json` again and follow the new `next_call`. Stop "
    "when `next_call` is null or repeats what you already did. A runtime target whose "
    "`command` ends in `<args>` (`next_call.optional` names them) is yours to finish: "
    "replace `<args>` with arguments that exercise real work, in the command and in its "
    "`ingest` line's workload (for a CLI `app`: `build --release`, and the workload "
    "`app build`), then run both. Then tell me what `repowise "
    "evidence status` reports, and the `reason` setup gave or why you stopped."
)


def render_connect_evidence(flavor: str = "generic") -> str:
    """The prompt for *flavor*: who the agent is, then the setup loop."""
    return f"{ROLE[flavor]}\n\n{CONNECT_EVIDENCE}"


__all__ = ["CONNECT_EVIDENCE", "render_connect_evidence"]
