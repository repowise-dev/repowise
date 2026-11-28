"""LLM provider endpoints and per-provider limits.

``<provider>.base_url`` is one entry for every provider whose spec names a
base-URL variable. The names come from ``PROVIDER_SPECS`` when a concrete key
is asked for, not at import: that table sits behind the provider SDK imports.
"""

from __future__ import annotations

from repowise.core.settings.areas._common import factory
from repowise.core.settings.model import Setting

AREA = "providers"
_s = factory(AREA)
_e = factory(AREA, scope="env")


def base_url_providers() -> tuple[str, ...]:
    """Providers whose endpoint can be set: the ones whose spec names a base-URL variable."""
    from repowise.core.providers.llm.specs import PROVIDER_SPECS

    return tuple(name for name, spec in PROVIDER_SPECS.items() if spec.base_url_envs)


SETTINGS: tuple[Setting, ...] = (
    _s(
        "<provider>.base_url",
        "str",
        None,
        "Custom http(s) endpoint for a provider (openai.base_url and the like), "
        "such as a proxy or a local server.",
        members="repowise.core.settings.areas.providers:base_url_providers",
        on_invalid="error",
        # Where requests go, not what a page says, so update rebuilds nothing.
        reload="immediate",
        # Written as one key; trusting the endpoint stays a separate, deliberate step.
        parser="repowise.core.providers.llm.resolve:validate_base_url",
    ),
    *(
        _e(
            f"REPOWISE_{cli}_CONCURRENCY",
            "int",
            4,
            f"How many {label} calls run at once.",
            minimum=1,
            out_of_range="clamp",
        )
        for cli, label in (
            ("CLAUDE_CLI", "Claude Code CLI"),
            ("CODEX_CLI", "Codex CLI"),
            ("COPILOT_CLI", "Copilot CLI"),
            ("OPENCODE", "OpenCode CLI"),
        )
    ),
    _e(
        "REPOWISE_OLLAMA_NUM_CTX",
        "int",
        None,
        "Context window for Ollama. Unset uses the model's own.",
        minimum=0,
        exclusive_minimum=True,
    ),
    _e(
        "REPOWISE_CONFIG_DIR",
        "str",
        "~/.repowise",
        "Folder holding the saved global provider choice (provider_config.json).",
    ),
    _e(
        "REPOWISE_PRICING_WARM",
        "bool",
        True,
        "Refresh the model price list in the background; 0 turns it off.",
        env_false=frozenset({"0"}),
        internal=True,
    ),
)
