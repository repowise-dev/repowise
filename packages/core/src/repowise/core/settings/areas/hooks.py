"""``hooks:`` in ``.repowise/config.yaml``: opt-in agent hook surfaces.

Read by ``hook_flag_enabled`` in the CLI, which this package cannot import, so
the flag names are declared here and a parity test holds them to every
``CONFIG_FLAG`` and ``hook_flag_enabled(..., "<flag>")`` in the CLI source.
``REPOWISE_HOOK_<FLAG>`` overrides a flag for one session.
"""

from __future__ import annotations

from repowise.core.settings.model import TRUTHY, Env, Setting

AREA = "hooks"
_WRITER = "repowise.cli._helpers.config:save_hook_surface_enabled"

#: Every flag ``hook_flag_enabled`` is asked about. All are off when absent.
_FLAGS: dict[str, str] = {
    "read_skeleton": "Serve a whole-file Read of a large indexed file as its outline.",
    "read_reread": "Serve an unchanged repeat Read as a short notice.",
    "search_digest": "Serve a search that floods many files as a digest.",
    "coverage_reingest": "Re-read coverage reports after an agent's full test run.",
    "measure": "On update, check locally whether agents acted on each hook firing.",
    "health_gate": "Block an agent's commit or push that fails repowise health check.",
}


def _flag(name: str, description: str) -> Setting:
    return Setting(
        key=f"hooks.{name}",
        type="bool",
        default=False,
        description=description,
        area=AREA,
        env=Env(f"REPOWISE_HOOK_{name.upper()}", wins=True, true_values=TRUTHY),
        writer=_WRITER,
    )


SETTINGS: tuple[Setting, ...] = (
    *(_flag(name, description) for name, description in _FLAGS.items()),
    Setting(
        key="hooks.read_skeleton_mode",
        type="str",
        default="smart",
        description="What a served outline keeps: smart, or plus to keep all code "
        "outside function bodies.",
        area=AREA,
        choices=("smart", "plus"),
        env=Env("REPOWISE_HOOK_READ_SKELETON_MODE", wins=True),
    ),
    Setting(
        key="REPOWISE_HOOK_UPDATED_OUTPUT",
        type="bool",
        default=None,
        description="Force whether the Claude Code hook may replace a tool's output; "
        "unset decides from the client version.",
        area=AREA,
        scope="env",
        env=Env("REPOWISE_HOOK_UPDATED_OUTPUT", true_values=TRUTHY),
        internal=True,
    ),
)
