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
_FILE_SETTINGS_WRITER = "repowise.core.repo_config:write_file_setting"

#: ``repowise.core.providers.embedding.registry._BUILTIN_EMBEDDERS``.
EMBEDDERS: tuple[str, ...] = ("openai", "gemini", "ollama", "openrouter", "edenai", "mock")


SETTINGS: tuple[Setting, ...] = (
    _s(
        "provider",
        "str",
        None,
        "Which model service writes docs and answers (anthropic, openai, gemini, "
        "ollama, claude_cli and others). Unset picks one from the API keys present.",
        env=Env("REPOWISE_PROVIDER"),
        writer="repowise.core.providers.llm.resolve:write_provider_setting",
    ),
    _s(
        "model",
        "str",
        None,
        "The model id to use with the provider. Unset uses the provider's default.",
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
        minimum=0,
        exclusive_minimum=True,
        on_invalid="error",
    ),
    _s(
        "context_window",
        "int",
        None,
        "The model's context size in tokens. Unset asks the provider.",
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
        "mock",
        "Which embedding service powers semantic search. mock means none.",
        choices=EMBEDDERS,
        env=Env("REPOWISE_EMBEDDER", wins=True),
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
    _s(
        "REPOWISE_DOC_MODEL",
        "str",
        None,
        "A different model just for get_answer write-ups.",
    ),
    _s(
        "REPOWISE_EMBEDDING_DIMS",
        "int",
        None,
        "Force the embedding vector width.",
        minimum=0,
        exclusive_minimum=True,
        reload="reindex",
    ),
    _s(
        "REPOWISE_EMBEDDING_DECLARED_DIMS",
        "int",
        None,
        "Vector width an OpenAI-compatible server really returns, when it differs "
        "from the width requested.",
        minimum=0,
        exclusive_minimum=True,
        reload="reindex",
    ),
    _s(
        "REPOWISE_EMBEDDING_TIMEOUT",
        "float",
        None,
        "Seconds one embedding request may take. Unset uses the embedder's own.",
        minimum=0,
        exclusive_minimum=True,
    ),
    _s(
        "REPOWISE_EMBED_DOC_PREFIX",
        "str",
        "",
        'Text put before each document before embedding (e.g. "passage: ").',
        reload="reindex",
    ),
    _s(
        "REPOWISE_EMBED_QUERY_PREFIX",
        "str",
        "",
        'Text put before each search query before embedding (e.g. "query: ").',
        reload="reindex",
    ),
    _s(
        "REPOWISE_NO_COST_TRACKING",
        "bool",
        False,
        "Do not save model cost records to the database.",
    ),
)
