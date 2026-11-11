"""LLM provider endpoints and per-provider limits.

The providers with a configurable endpoint are the ones whose spec names a
base-URL variable; a parity test holds this list to ``PROVIDER_SPECS``, which
is too heavy to import here.
"""

from __future__ import annotations

from repowise.core.settings.areas._common import factory
from repowise.core.settings.model import Setting

AREA = "providers"
_s = factory(AREA)

#: ``name for name, spec in PROVIDER_SPECS.items() if spec.base_url_envs``.
BASE_URL_PROVIDERS: tuple[str, ...] = (
    "gemini",
    "anthropic",
    "openai",
    "deepseek",
    "kimi",
    "edenai",
    "ollama",
    "litellm",
)

SETTINGS: tuple[Setting, ...] = (
    *(
        _s(
            f"{name}.base_url",
            "str",
            None,
            f"Custom http(s) endpoint for {name}, such as a proxy or a local server.",
            on_invalid="error",
            parser="repowise.core.providers.llm.resolve:validate_base_url",
        )
        for name in BASE_URL_PROVIDERS
    ),
    *(
        _s(
            f"REPOWISE_{cli}_CONCURRENCY",
            "int",
            4,
            f"How many {label} calls run at once.",
            minimum=1,
        )
        for cli, label in (
            ("CLAUDE_CLI", "Claude Code CLI"),
            ("CODEX_CLI", "Codex CLI"),
            ("COPILOT_CLI", "Copilot CLI"),
            ("OPENCODE", "OpenCode CLI"),
        )
    ),
    _s(
        "REPOWISE_OLLAMA_NUM_CTX",
        "int",
        None,
        "Context window for Ollama. Unset uses the model's own.",
        minimum=0,
        exclusive_minimum=True,
    ),
    _s(
        "REPOWISE_CONFIG_DIR",
        "str",
        "~/.repowise",
        "Folder holding the saved global provider choice (provider_config.json).",
    ),
    _s(
        "REPOWISE_PRICING_WARM",
        "bool",
        True,
        "Refresh the model price list in the background; 0 turns it off.",
        env_false=frozenset({"0"}),
        internal=True,
    ),
)
