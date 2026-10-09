"""One record per built-in LLM provider, and the tables everyone else derives.

Facts about a provider (which env var holds its key, whether it runs in the
repo's directory, whether its models cost money, how hard to rate-limit it)
used to be spelled out again in every module that needed one: the registry,
the rate limiter, two pricing tables, the server catalog, the init picker,
doctor's install warnings. Each copy agreed with the others only by hand, and
several had already drifted. A :class:`ProviderSpec` holds them once; the old
tables (``KEYLESS_PROVIDERS``, ``PROVIDER_CATALOG`` and so on) are now
computed from :data:`PROVIDER_SPECS`, so a new provider is one record here.

The agent CLIs (``claude_cli``, ``codex_cli``, ``opencode``) are the one
place this registry meets the agent registry in
:mod:`repowise.core.agents.identity`: the spec names the agent slug in
``agent``, the identity names the provider back in ``indexing_provider``, and
a test holds the two links to each other. Executable, install and login facts
belong to the agent and live on the identity. ``agent`` being set is also how
a consumer tells "needs a local agent CLI on this machine" from a provider
that only needs network access.

Agent CLIs get no rate limiter (``rate_limit=None``). Each call is a
subprocess under the provider's own concurrency semaphore, and the account's
real ceiling is enforced by the CLI itself, so a client-side token bucket only
double-throttles them.

Declaration order is load-bearing: it is the server catalog's order, and the
server falls back to the first catalog provider that is usable without a key.
"""

from __future__ import annotations

from dataclasses import dataclass

from repowise.core.agents.identity import get_identity
from repowise.core.rate_limiter import RateLimitConfig


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """What the rest of repowise needs to know about one LLM provider."""

    name: str
    #: ``"module:Class"``, imported only when the provider is requested.
    impl: str
    #: Human label for catalogs (the server's settings list).
    label: str
    default_model: str
    #: Models a picker offers before asking the provider for its live list.
    models: tuple[str, ...] = ()
    api_key_envs: tuple[str, ...] = ()
    base_url_envs: tuple[str, ...] = ()
    #: Runs with no API key: out-of-band login, a local model, or a proxy.
    keyless: bool = False
    #: Shells out with the repo as its working directory.
    needs_repo_cwd: bool = False
    #: Runs on this machine, so high concurrency can time out.
    local: bool = False
    #: Models named ``"<name>/..."`` cost nothing per token.
    zero_cost: bool = False
    #: Flood guard; ``None`` attaches no rate limiter.
    rate_limit: RateLimitConfig | None = None
    #: Package named when the provider's module fails to import.
    package: str = ""
    #: Position in credential auto-detection; ``None`` never auto-detects.
    autodetect_rank: int | None = None
    #: The agent slug whose CLI this provider drives.
    agent: str | None = None
    #: Offered in pickers and catalogs. ``False`` means flag-only.
    selectable: bool = True
    #: Short dim note beside the name in the init picker.
    note: str = ""
    #: Where to get a key (or the runtime) when the provider is not set up.
    signup_url: str = ""

    @property
    def module_path(self) -> str:
        return self.impl.partition(":")[0]

    @property
    def class_name(self) -> str:
        return self.impl.partition(":")[2]

    @property
    def required_envs(self) -> tuple[str, ...]:
        """The key, or for a keyless provider with an endpoint, the endpoint."""
        return self.api_key_envs or (self.base_url_envs if self.keyless else ())

    @property
    def setup_hint(self) -> str:
        """One line telling a user how to make this provider usable."""
        identity = get_identity(self.agent) if self.agent else None
        if identity is not None:
            return f"{identity.install_hint}, then: {identity.login_hint}"
        if not self.api_key_envs and self.signup_url:
            return self.signup_url
        return f"pip install {self.package}" if self.package else ""


_LLM = "repowise.core.providers.llm"

# Rate limits are FLOOD GUARDS, not throttles. The provider's own 429s are the
# authoritative signal, and every provider retries them patiently (retry-after
# aware, see providers/llm/base.py). Values sit near each provider's published
# upper tiers (2026); low-tier accounts degrade through 429 backoff instead of
# everyone being pre-emptively slowed. Ollama is local and effectively
# unlimited, capped only to avoid OOM.
_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        name="gemini",
        impl=f"{_LLM}.gemini:GeminiProvider",
        label="Google Gemini",
        default_model="gemini-3.5-flash-lite",
        models=("gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.1-pro-preview"),
        api_key_envs=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        base_url_envs=("GEMINI_BASE_URL",),
        rate_limit=RateLimitConfig(requests_per_minute=1_000, tokens_per_minute=4_000_000),
        package="google-genai",
        autodetect_rank=4,
        note="recommended",
        signup_url="https://aistudio.google.com/apikey",
    ),
    ProviderSpec(
        name="anthropic",
        impl=f"{_LLM}.anthropic:AnthropicProvider",
        label="Anthropic",
        default_model="claude-haiku-5-5",
        models=("claude-haiku-5-5", "claude-sonnet-4-6", "claude-opus-4-6"),
        api_key_envs=("ANTHROPIC_API_KEY",),
        base_url_envs=("ANTHROPIC_BASE_URL",),
        rate_limit=RateLimitConfig(requests_per_minute=2_000, tokens_per_minute=1_600_000),
        package="anthropic",
        autodetect_rank=0,
        signup_url="https://console.anthropic.com/settings/keys",
    ),
    ProviderSpec(
        name="openai",
        impl=f"{_LLM}.openai:OpenAIProvider",
        label="OpenAI",
        default_model="gpt-5.6-luna",
        models=("gpt-5.6-luna", "gpt-5.4-nano", "gpt-5.4-mini", "gpt-5.4"),
        api_key_envs=("OPENAI_API_KEY",),
        base_url_envs=("OPENAI_BASE_URL",),
        rate_limit=RateLimitConfig(requests_per_minute=5_000, tokens_per_minute=4_000_000),
        package="openai",
        autodetect_rank=1,
        signup_url="https://platform.openai.com/api-keys",
    ),
    ProviderSpec(
        name="openrouter",
        impl=f"{_LLM}.openrouter:OpenRouterProvider",
        label="OpenRouter",
        default_model="google/gemini-3.5-flash-lite",
        models=(
            "google/gemini-3.5-flash-lite",
            "openai/gpt-5.6-luna",
            "anthropic/claude-haiku-4-5",
            "anthropic/claude-sonnet-5",
        ),
        api_key_envs=("OPENROUTER_API_KEY",),
        rate_limit=RateLimitConfig(requests_per_minute=1_000, tokens_per_minute=2_000_000),
        package="openai",
        autodetect_rank=2,
        signup_url="https://openrouter.ai/keys",
    ),
    ProviderSpec(
        name="deepseek",
        impl=f"{_LLM}.deepseek:DeepSeekProvider",
        label="DeepSeek",
        default_model="deepseek-v4-flash",
        models=("deepseek-v4-flash", "deepseek-v4-pro"),
        api_key_envs=("DEEPSEEK_API_KEY",),
        base_url_envs=("DEEPSEEK_BASE_URL",),
        rate_limit=RateLimitConfig(requests_per_minute=1_000, tokens_per_minute=5_000_000),
        package="openai",
        autodetect_rank=5,
        signup_url="https://platform.deepseek.com/api_keys",
    ),
    ProviderSpec(
        name="kimi",
        impl=f"{_LLM}.kimi:KimiProvider",
        label="Kimi",
        default_model="kimi-for-coding",
        models=("kimi-for-coding", "kimi-for-coding-highspeed", "kimi-k2.5", "kimi-k2.6"),
        api_key_envs=("KIMI_API_KEY",),
        base_url_envs=("KIMI_BASE_URL",),
        rate_limit=RateLimitConfig(requests_per_minute=1_000, tokens_per_minute=5_000_000),
        package="openai",
        autodetect_rank=6,
        signup_url="https://www.kimi.com/code/console",
    ),
    ProviderSpec(
        name="edenai",
        impl=f"{_LLM}.edenai:EdenAIProvider",
        label="Eden AI",
        default_model="mistral/mistral-small-latest",
        models=(
            "mistral/mistral-small-latest",
            "openai/gpt-4o-mini",
            "anthropic/claude-haiku-4-5",
            "google/gemini-2.5-flash",
        ),
        api_key_envs=("EDENAI_API_KEY",),
        base_url_envs=("EDENAI_BASE_URL",),
        rate_limit=RateLimitConfig(requests_per_minute=1_000, tokens_per_minute=2_000_000),
        package="openai",
        # Last on purpose: an unrelated EDENAI_API_KEY in the environment must
        # not take over from a provider the user was already resolving to.
        autodetect_rank=7,
        signup_url="https://app.edenai.run/user/register",
    ),
    ProviderSpec(
        name="ollama",
        impl=f"{_LLM}.ollama:OllamaProvider",
        label="Ollama (Local)",
        default_model="qwen3.5:4b",
        models=("qwen3.5:4b", "qwen3.5:2b", "llama3.2", "qwen2.5-coder"),
        base_url_envs=("OLLAMA_BASE_URL",),
        keyless=True,
        local=True,
        rate_limit=RateLimitConfig(requests_per_minute=1_000, tokens_per_minute=10_000_000),
        package="openai",
        autodetect_rank=3,
        note="runs on your machine, no key",
        signup_url="https://ollama.com/download",
    ),
    ProviderSpec(
        name="litellm",
        impl=f"{_LLM}.litellm:LiteLLMProvider",
        label="LiteLLM",
        default_model="groq/llama-3.1-70b-versatile",
        models=("groq/llama-3.1-70b-versatile",),
        api_key_envs=("LITELLM_API_KEY",),
        base_url_envs=("LITELLM_BASE_URL", "LITELLM_API_BASE"),
        keyless=True,
        rate_limit=RateLimitConfig(requests_per_minute=1_000, tokens_per_minute=2_000_000),
        package="litellm",
        note="proxy in front of another provider",
        signup_url="https://docs.litellm.ai/docs/providers",
    ),
    ProviderSpec(
        name="claude_cli",
        impl=f"{_LLM}.claude_cli:ClaudeCliProvider",
        label="Claude Code (Local CLI)",
        default_model="claude_cli/claude-haiku-5-5",
        models=(
            "claude_cli/claude-haiku-5-5",
            "claude_cli/claude-sonnet-4-6",
            "claude_cli/claude-opus-4-6",
        ),
        keyless=True,
        # Not needs_repo_cwd: it runs in a scratch dir so the repo's CLAUDE.md
        # stays out of every prompt (see claude_cli.py).
        local=True,
        zero_cost=True,
        agent="claude_code",
        note="uses your Claude Code login",
    ),
    ProviderSpec(
        name="codex_cli",
        impl=f"{_LLM}.codex_cli:CodexCliProvider",
        label="Codex (Local CLI)",
        default_model="codex_cli/default",
        # The live list comes from the authenticated codex catalog at runtime.
        models=("codex_cli/default",),
        keyless=True,
        needs_repo_cwd=True,
        local=True,
        zero_cost=True,
        agent="codex",
        note="uses your Codex CLI login",
    ),
    ProviderSpec(
        name="opencode",
        impl=f"{_LLM}.opencode:OpenCodeProvider",
        label="OpenCode (Local CLI)",
        default_model="opencode/default",
        models=("opencode/default", "opencode/openai/gpt-5", "opencode/deepseek/deepseek-v4-pro"),
        keyless=True,
        needs_repo_cwd=True,
        local=True,
        zero_cost=True,
        agent="opencode",
        note="uses your opencode CLI setup",
    ),
    ProviderSpec(
        name="mock",
        impl=f"{_LLM}.mock:MockProvider",
        label="Mock",
        default_model="mock",
        keyless=True,
        selectable=False,
    ),
)

PROVIDER_SPECS: dict[str, ProviderSpec] = {spec.name: spec for spec in _SPECS}

#: Model-name prefixes that cost nothing per token.
ZERO_COST_MODEL_PREFIXES: tuple[str, ...] = tuple(
    f"{spec.name}/" for spec in _SPECS if spec.zero_cost
)
