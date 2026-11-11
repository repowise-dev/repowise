"""The MCP server: which tools it offers and how its answers behave."""

from __future__ import annotations

from repowise.core.settings.areas._common import factory
from repowise.core.settings.model import FALSY, Setting

AREA = "mcp"
_s = factory(AREA)
_e = factory(AREA, scope="env")

#: ``get_answer`` switches, each ``(name, default, what it does)``. One that is
#: on by default turns off with 0/false/no/off; one that is off turns on with
#: 1/true/yes/on.
_ANSWER_SWITCHES: tuple[tuple[str, bool, str], ...] = (
    ("AGREEMENT_CONFIDENCE", True, "Raise confidence when text and semantic search agree."),
    ("ALWAYS_SYNTHESIZE", True, "Always write an answer, lowering confidence when unsure."),
    (
        "CLAIM_SUPPORT_GATE",
        True,
        "Hold back high confidence unless the key symbol's source was shown.",
    ),
    ("EARN_HIGH_GROUNDING", True, "Let the named symbol's source earn a high-confidence answer."),
    ("EARN_HIGH_ON_WEAK_RETRIEVAL", False, "Let a weakly found answer earn high confidence."),
    (
        "SYMBOL_AGREEMENT",
        True,
        "Without embeddings, let text and symbol search agree to raise confidence.",
    ),
    ("UNION_MECHANISM_DEFER", True, "Keep the exact-symbol path off how-does-it-work questions."),
    ("DISABLE_CACHE", False, "Skip the saved-answer cache."),
    ("LEAN_HIGH", False, "Drop re-read evidence from high-confidence answers."),
)

SETTINGS: tuple[Setting, ...] = (
    _s(
        "mcp.tools",
        "list",
        None,
        "Which MCP tools are offered: all, lean, +name or -name to adjust the "
        "default, or plain names as the exact list. Unset offers the default set.",
        parser="repowise.core.registry.tool_selection:normalize_override",
        writer="repowise.server.mcp_server._tool_selection:set_tool_override",
    ),
    _e(
        "REPOWISE_ANSWER_TIMEOUT_S",
        "float",
        None,
        "Seconds get_answer may spend writing one answer. Unset uses the provider's own.",
        minimum=0,
        exclusive_minimum=True,
        maximum=600,
        out_of_range="clamp",
    ),
    _e(
        "REPOWISE_EMBED_TIMEOUT_S",
        "float",
        8.0,
        "Seconds a get_answer question may wait to be embedded.",
        minimum=0,
        exclusive_minimum=True,
        maximum=120,
        out_of_range="clamp",
    ),
    _e(
        "REPOWISE_VECTOR_SEARCH_TIMEOUT_S",
        "float",
        30.0,
        "Seconds one semantic search may take.",
        minimum=0,
        exclusive_minimum=True,
        maximum=120,
        out_of_range="clamp",
    ),
    _e(
        "REPOWISE_WORKSPACE_CONCURRENCY",
        # Read as a number and cut to a whole one, as its reader does.
        "float",
        4.0,
        "How many repos one workspace query asks at once.",
        minimum=0,
        exclusive_minimum=True,
        maximum=64,
        out_of_range="clamp",
    ),
    _e(
        "REPOWISE_WORKSPACE_REPO_TIMEOUT_S",
        "float",
        60.0,
        "Seconds each repo gets in a workspace query.",
        minimum=0,
        exclusive_minimum=True,
        maximum=300,
        out_of_range="clamp",
    ),
    _e(
        "REPOWISE_MCP_NO_WATCHDOG",
        "bool",
        False,
        "Keep the MCP server running when the app that started it exits.",
        env_true=frozenset({"1", "true", "yes"}),
    ),
    *(
        _e(
            f"REPOWISE_ANSWER_{name}",
            "bool",
            default,
            description,
            env_false=FALSY if default else None,
            internal=True,
        )
        for name, default, description in _ANSWER_SWITCHES
    ),
    _e(
        "REPOWISE_SYNTHESIS_MAX_TOKENS",
        "int",
        4096,
        "Most tokens in one get_answer write-up.",
        minimum=0,
        exclusive_minimum=True,
        internal=True,
    ),
    _e(
        "REPOWISE_SEMANTIC_GRACE_S",
        "float",
        1.0,
        "Seconds search waits for semantic results after the others finish.",
        minimum=0,
        exclusive_minimum=True,
        maximum=120,
        out_of_range="clamp",
        internal=True,
    ),
    _e(
        "REPOWISE_FTS_DF_CEILING",
        "float",
        0.20,
        "Drop search terms found in more than this share of pages.",
        internal=True,
    ),
    _e("REPOWISE_MCP_DEBUG_META", "bool", False, "Add timings to every MCP reply.", internal=True),
    _e("REPOWISE_MCP_PRETTY_JSON", "bool", False, "Indent MCP replies.", internal=True),
    _e(
        "REPOWISE_MCP_INDEX_SCOPE",
        "str",
        None,
        "Set to full for the older, larger index-scope block in replies.",
        internal=True,
    ),
)
