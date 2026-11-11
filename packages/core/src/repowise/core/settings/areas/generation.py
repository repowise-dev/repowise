"""What writes the wiki and how: provider, model, embedder, style, language.

``reload`` comes from ``CONFIG_DEPENDENCY_KEYS``, the table ``update`` uses to
decide what a config change invalidates. Values behind a heavy import (the
embedder list, the generation defaults) are declared and held to their source
by a parity test.
"""

from __future__ import annotations

from repowise.core.generation.languages import SUPPORTED_LANGUAGES
from repowise.core.reasoning import REASONING_MODES
from repowise.core.settings.areas._common import factory
from repowise.core.settings.model import Env, Setting

AREA = "generation"
_s = factory(AREA)
_e = factory(AREA, scope="env")
_FILE_SETTINGS_WRITER = "repowise.core.repo_config:write_file_setting"

SETTINGS: tuple[Setting, ...] = (
    _s(
        "provider",
        "str",
        None,
        "Which model service writes docs and answers (anthropic, openai, gemini, "
        "ollama, claude_cli and others).",
        default_note="Unset: the saved global choice (provider_config.json), then "
        "REPOWISE_PROVIDER, then the first provider whose API key is present.",
        env=Env("REPOWISE_PROVIDER"),
        writer="repowise.core.providers.llm.resolve:write_provider_setting",
    ),
    _s(
        "model",
        "str",
        None,
        "The model id to use with the provider.",
        default_note="Unset: the saved global choice (provider_config.json), then "
        "REPOWISE_MODEL, then the provider's default model.",
        env=Env("REPOWISE_MODEL"),
    ),
    _s(
        "reasoning",
        "str",
        "auto",
        "How much the model thinks before writing.",
        choices=REASONING_MODES,
        env=Env("REPOWISE_REASONING", wins=True),
        on_invalid="error",
        parser="repowise.core.reasoning:normalize_reasoning",
    ),
    _s(
        "max_tokens",
        "int",
        16384,
        "Most output tokens for one generated page.",
        parser="repowise.core.settings.parsers:quoted_int",
        minimum=0,
        exclusive_minimum=True,
        on_invalid="error",
    ),
    _s(
        "context_window",
        "int",
        None,
        "Tokens (prompt plus answer) the model runs with; page material shrinks to fit. "
        "Unset: no limit for hosted models, 16384 for a local server (Ollama reports "
        "its own num_ctx).",
        minimum=0,
        exclusive_minimum=True,
        on_invalid="error",
    ),
    _s(
        "generation_context.token_budget",
        "int",
        8000,
        "Most tokens of pinned source files added to a page as evidence.",
        minimum=0,
        on_invalid="error",
    ),
    _s(
        "generation_context.files",
        "mapping",
        {},
        "Repo files to show the writer as evidence, by page.",
        on_invalid="error",
    ),
    _s(
        "wiki_style",
        "str",
        "comprehensive",
        "How the wiki reads: comprehensive, caveman, reference, tutorial, or a "
        "custom style under .repowise/styles/.",
        writer=_FILE_SETTINGS_WRITER,
    ),
    _s(
        "language",
        "str",
        "en",
        "Language the docs are written in.",
        choices=tuple(SUPPORTED_LANGUAGES),
    ),
    _s(
        "max_file_pages",
        "int",
        None,
        "Most per-file pages to write; 0 means every file. Unset lets the repo size decide.",
        minimum=0,
        parser="repowise.core.settings.parsers:int_like",
    ),
    _s(
        "folder_coverage",
        "list",
        [],
        'Least share of files per folder that get their own page, as "glob=0.5".',
        on_invalid="error",
    ),
    _s(
        "enable_onboarding",
        "bool",
        True,
        "Write the onboarding pages.",
    ),
    _s(
        "embedder",
        "str",
        None,
        "Which embedding service powers semantic search: openai, gemini, ollama, "
        "openrouter, edenai, a plugin's, or mock for none.",
        default_note="Unset: the first embedder whose API key is present, then the "
        "embedder in ~/.repowise/config.yaml, then mock.",
        env=Env("REPOWISE_EMBEDDER", wins=True),
        parser="repowise.core.settings.parsers:folded",
        reload="reindex",
    ),
    _s(
        "embedding_model",
        "str",
        None,
        "The embedding model id. Unset uses the embedder's default.",
        env=Env("REPOWISE_EMBEDDING_MODEL", wins=True),
        reload="reindex",
    ),
    _e(
        "REPOWISE_DOC_MODEL",
        "str",
        None,
        "A different model just for get_answer write-ups.",
    ),
    _e(
        "REPOWISE_EMBEDDING_DIMS",
        "int",
        None,
        "Force the embedding vector width.",
        minimum=0,
        exclusive_minimum=True,
        reload="reindex",
    ),
    _e(
        "REPOWISE_EMBEDDING_DECLARED_DIMS",
        "int",
        None,
        "Vector width an OpenAI-compatible server really returns, when it differs "
        "from the width requested.",
        minimum=0,
        exclusive_minimum=True,
        reload="reindex",
    ),
    _e(
        "REPOWISE_EMBEDDING_TIMEOUT",
        "float",
        None,
        "Seconds one embedding request may take. Unset uses the embedder's own.",
        minimum=0,
        exclusive_minimum=True,
    ),
    _e(
        "REPOWISE_EMBED_DOC_PREFIX",
        "str",
        "",
        'Text put before each document before embedding (e.g. "passage: ").',
        reload="reindex",
    ),
    _e(
        "REPOWISE_EMBED_QUERY_PREFIX",
        "str",
        "",
        'Text put before each search query before embedding (e.g. "query: ").',
        reload="reindex",
    ),
    _e(
        "REPOWISE_NO_COST_TRACKING",
        "bool",
        False,
        "Do not save model cost records to the database.",
    ),
)
