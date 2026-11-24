"""Unit tests for get_answer's retrieval-noise demotion (_answer_pipeline).

get_answer historically applied no decision/test demotion of its own, so
decision records and test file pages could win RRF and occupy top-5 slots that
feed synthesis. ``demote_noise_hits`` stable-partitions that noise below real
pages before the cap. These are pure-function tests (no fixture, no DB).
"""

from __future__ import annotations

from repowise.server.mcp_server._answer_pipeline import demote_noise_hits


def _hit(page_type, target_path, title="t"):
    return {"page_type": page_type, "target_path": target_path, "title": title}


def test_decision_demoted_on_non_why_question():
    hits = [
        _hit("decision_record", ""),
        _hit("file_page", "src/pipeline/incremental.py"),
    ]
    out = demote_noise_hits(hits, "how does incremental update work", is_why=False)
    assert out[0]["target_path"] == "src/pipeline/incremental.py"
    assert out[1]["page_type"] == "decision_record"  # demoted, not dropped


def test_decision_kept_on_why_question():
    hits = [
        _hit("decision_record", ""),
        _hit("file_page", "src/pipeline/incremental.py"),
    ]
    out = demote_noise_hits(hits, "why does update only regenerate affected pages", is_why=True)
    assert out[0]["page_type"] == "decision_record"  # ranked naturally on a why question


def test_test_page_demoted_on_impl_question():
    hits = [
        _hit("file_page", "tests/unit/test_incremental.py"),
        _hit("file_page", "src/pipeline/incremental.py"),
    ]
    out = demote_noise_hits(hits, "how does incremental update work", is_why=False)
    assert out[0]["target_path"] == "src/pipeline/incremental.py"
    assert out[1]["target_path"] == "tests/unit/test_incremental.py"  # demoted, not dropped


def test_test_page_kept_on_test_question():
    hits = [
        _hit("file_page", "tests/unit/test_incremental.py"),
        _hit("file_page", "src/pipeline/incremental.py"),
    ]
    out = demote_noise_hits(hits, "how is incremental update tested", is_why=False)
    assert out[0]["target_path"] == "tests/unit/test_incremental.py"  # test-focused: no demotion


def test_fixtures_and_test_lookalikes_keep_their_rank():
    """Only tests demote, and only real ones (#1103).

    The question is often about the fixtures themselves, and the substring
    tokens this ran on also demoted production files spelling "test_".
    """
    # Leading segments on purpose: the token list this replaced anchored on
    # "/tests/", so a root-level `tests/` path would pass it for the wrong
    # reason and the assertion would prove nothing.
    hits = [
        _hit("file_page", "packages/core/tests/conftest.py"),
        _hit("file_page", "packages/core/tests/factories/user.py"),
        _hit("file_page", "src/analysis/missing_test_signal.py"),
        _hit("file_page", "src/latest/api.py"),
    ]
    out = demote_noise_hits(list(hits), "where do the shared fixtures live", is_why=False)
    assert [h["target_path"] for h in out] == [h["target_path"] for h in hits]


def test_layouts_the_token_list_missed_now_demote():
    hits = [
        _hit("file_page", "myapp/tests.py"),
        _hit("file_page", "Foo.Tests/Bar.cs"),
        _hit("file_page", "src/pipeline/incremental.py"),
    ]
    out = demote_noise_hits(hits, "how does incremental update work", is_why=False)
    assert out[0]["target_path"] == "src/pipeline/incremental.py"


def test_stable_order_preserved_among_real_hits():
    hits = [
        _hit("file_page", "src/a.py"),
        _hit("decision_record", ""),
        _hit("file_page", "src/b.py"),
        _hit("module_page", "src/pkg"),
    ]
    out = demote_noise_hits(hits, "how does a work", is_why=False)
    # Real hits keep their relative order; the decision falls to the tail.
    assert [h.get("target_path") for h in out] == ["src/a.py", "src/b.py", "src/pkg", ""]


def test_a_test_file_defining_a_named_symbol_keeps_its_rank():
    anchored = {**_hit("file_page", "tests/model_forms/tests.py"), "_symbol_anchored": True}
    hits = [anchored, _hit("file_page", "src/forms.py")]
    out = demote_noise_hits(hits, "how does IncompleteFormWithFields set url", is_why=False)
    assert out[0]["target_path"] == "tests/model_forms/tests.py"


def test_empty_hits_noop():
    assert demote_noise_hits([], "anything", is_why=False) == []


def test_test_support_demoted_by_code_origin():
    """A mock header is test code: it ranks below the file it stands in for."""
    hits = [
        _hit("file_page", "test/mocks/router/router_filter_interface.h"),
        _hit("file_page", "source/common/router/router.cc"),
    ]
    out = demote_noise_hits(hits, "how does the router filter pick a route", is_why=False)
    assert out[0]["target_path"] == "source/common/router/router.cc"
    out = demote_noise_hits(hits, "where is the router filter mock", is_why=False)
    assert out[0]["target_path"] == "test/mocks/router/router_filter_interface.h"


def test_generated_and_vendored_files_demoted_unless_asked_for():
    hits = [
        _hit("file_page", "pkg/apis/v1/zz_generated.deepcopy.go"),
        _hit("file_page", "vendor/github.com/x/y/y.go"),
        _hit("file_page", "pkg/apis/v1/types.go"),
    ]
    # Generated code is known by its banner, which the stored origin saw.
    origins = {"pkg/apis/v1/zz_generated.deepcopy.go": "generated"}
    q = "what types define a resource"
    out = demote_noise_hits(list(hits), q, is_why=False, origins=origins)
    assert out[0]["target_path"] == "pkg/apis/v1/types.go"
    q = "where is the generated deepcopy code"
    out = demote_noise_hits(list(hits), q, is_why=False, origins=origins)
    assert out[0]["target_path"] == "pkg/apis/v1/zz_generated.deepcopy.go"


def test_stored_origin_decides_over_the_path():
    hits = [_hit("file_page", "src/fake_clock.py"), _hit("file_page", "src/clock.py")]
    origins = {"src/fake_clock.py": "test", "src/clock.py": "production"}
    out = demote_noise_hits(hits, "how does the clock tick", is_why=False, origins=origins)
    assert out[0]["target_path"] == "src/clock.py"


# --- Off-language demotion (stored graph_nodes.language) -------------------

_HCM_PROTO = "api/envoy/extensions/filters/network/http_connection_manager/v3/hcm.proto"
_LANGS = {
    _HCM_PROTO: "proto",
    "source/common/http/conn_manager_impl.cc": "cpp",
    "envoy/http/codec.h": "cpp",
    "source/common/http/codec_helper.c": "c",
    "tools/check_format.sh": "shell",
    "docs/root/intro.md": "markdown",
}


def _paths(hits):
    return [h["target_path"] for h in hits]


def test_off_language_file_sinks_below_question_language():
    hits = [
        _hit("file_page", _HCM_PROTO),
        _hit("file_page", "tools/check_format.sh"),
        _hit("file_page", "source/common/http/conn_manager_impl.cc"),
        _hit("file_page", "docs/root/intro.md"),
        _hit("file_page", "envoy/http/codec.h"),
    ]
    out = demote_noise_hits(
        hits, "How does the connection manager parse HTTP frames?", is_why=False, languages=_LANGS
    )
    assert _paths(out) == [
        "source/common/http/conn_manager_impl.cc",
        "envoy/http/codec.h",
        _HCM_PROTO,
        "tools/check_format.sh",
        "docs/root/intro.md",
    ]


def test_language_family_is_one_language():
    """A C source beside C++ headers is the C++ question's own language, even
    when a named C++ symbol makes other code languages sink."""
    langs = _LANGS | {"mobile/Engine.kt": "kotlin"}
    hits = [
        _hit("file_page", "mobile/Engine.kt"),
        _hit("file_page", "source/common/http/codec_helper.c"),
        {**_hit("file_page", "envoy/http/codec.h"), "_symbol_anchored": True},
    ]
    out = demote_noise_hits(hits, "how is ServerConnection built", is_why=False, languages=langs)
    assert _paths(out) == [
        "source/common/http/codec_helper.c",
        "envoy/http/codec.h",
        "mobile/Engine.kt",
    ]


def test_anchored_row_never_sinks():
    """A file defining a named symbol keeps its rank whatever its language;
    an unanchored file in the same language still sinks."""
    langs = {"a.go": "go", "gen.sh": "shell", "tools/other.sh": "shell"}
    hits = [
        _hit("file_page", "tools/other.sh"),
        {**_hit("file_page", "gen.sh"), "_symbol_anchored": True},
        {**_hit("file_page", "a.go"), "_symbol_anchored": True},
    ]
    out = demote_noise_hits(hits, "what do run_gen and Run do", is_why=False, languages=langs)
    assert _paths(out) == ["gen.sh", "a.go", "tools/other.sh"]


def test_non_code_named_symbol_falls_back_to_the_ranking_vote():
    """A named proto message sets no language; the ranked code files do."""
    hits = [
        _hit("file_page", "docs/root/intro.md"),
        {**_hit("api_contract", _HCM_PROTO), "_symbol_anchored": True},
        _hit("file_page", "source/common/http/conn_manager_impl.cc"),
        _hit("file_page", "envoy/http/codec.h"),
    ]
    out = demote_noise_hits(
        hits, "how is HttpConnectionManager read", is_why=False, languages=_LANGS
    )
    assert _paths(out) == [
        _HCM_PROTO,
        "source/common/http/conn_manager_impl.cc",
        "envoy/http/codec.h",
        "docs/root/intro.md",
    ]


def test_asked_languages_reads_tags_extensions_paths_and_kind_words():
    from repowise.server.mcp_server._origin_rank import asked_languages

    assert "proto" in asked_languages("which proto message carries it")
    assert "typescript" in asked_languages("the .tsx component that renders it")
    assert "typescript" in asked_languages("what does src/app/view.js export")
    assert "shell" in asked_languages("which script installs it")
    assert "markdown" in asked_languages("where is the readme section")
    assert "sql" in asked_languages("which migration adds the column")
    assert "go" in asked_languages("which Go package serves it")
    assert "cpp" in asked_languages("which C++ class parses it")
    assert "csharp" in asked_languages("the C# handler for it")
    # A bare extension, and a tag that is an everyday word, are words first.
    assert asked_languages("which page renders it") == frozenset()
    assert asked_languages("where do requests go after the router") == frozenset()


def test_question_naming_the_file_type_keeps_its_rank():
    hits = [
        _hit("file_page", _HCM_PROTO),
        _hit("file_page", "source/common/http/conn_manager_impl.cc"),
    ]
    for q in (
        "Which proto defines the connection manager config?",
        "Where is the schema for the connection manager?",
        "What does hcm.proto declare?",
    ):
        out = demote_noise_hits(list(hits), q, is_why=False, languages=_LANGS)
        assert _paths(out) == _paths(hits), q


def test_named_symbol_language_decides_over_the_vote():
    """A question naming a Go symbol is about Go even when TypeScript leads."""
    langs = {"public/app/loki/datasource.ts": "typescript", "pkg/tsdb/loki/loki.go": "go"}
    hits = [
        _hit("file_page", "public/app/loki/datasource.ts"),
        _hit("file_page", "public/app/loki/datasource.ts"),
        {**_hit("file_page", "pkg/tsdb/loki/loki.go"), "_symbol_anchored": True},
    ]
    out = demote_noise_hits(hits, "How does QueryData run?", is_why=False, languages=langs)
    assert out[0]["target_path"] == "pkg/tsdb/loki/loki.go"


def test_no_stored_languages_changes_nothing():
    hits = [_hit("file_page", _HCM_PROTO), _hit("file_page", "envoy/http/codec.h")]
    out = demote_noise_hits(list(hits), "how are codecs built", is_why=False)
    assert _paths(out) == _paths(hits)


def test_non_code_only_pool_keeps_its_order():
    """With no code file to vote, nothing is off-language."""
    hits = [_hit("file_page", _HCM_PROTO), _hit("file_page", "docs/root/intro.md")]
    out = demote_noise_hits(list(hits), "how is it configured", is_why=False, languages=_LANGS)
    assert _paths(out) == _paths(hits)


def test_one_leading_file_does_not_outvote_the_rest():
    """Each distinct file votes once: a script at rank 1 over one TypeScript
    file and four Go files leaves the question about Go, so the script sinks."""
    langs = {"scripts/gen.sh": "shell", "ui/src/api.ts": "typescript"} | {
        f"internal/server/{n}.go": "go" for n in "abcd"
    }
    hits = [_hit("file_page", "scripts/gen.sh"), _hit("file_page", "ui/src/api.ts")] + [
        _hit("file_page", f"internal/server/{n}.go") for n in "abcd"
    ]
    out = demote_noise_hits(hits, "how is a flag evaluated", is_why=False, languages=langs)
    assert _paths(out)[0] == "ui/src/api.ts"
    assert _paths(out)[-1] == "scripts/gen.sh"


def test_voted_language_leaves_other_code_in_place():
    """Without a named symbol the vote is weak evidence: a Python file among
    TypeScript ones keeps its rank, since a backend answer can rank among UI files."""
    langs = {"core/fix_attribution.py": "python"} | {f"ui/{n}.tsx": "typescript" for n in "abc"}
    hits = [_hit("file_page", p) for p in langs]
    out = demote_noise_hits(
        list(hits), "how is fix history rolled up", is_why=False, languages=langs
    )
    assert _paths(out) == _paths(hits)


def test_mixed_ranking_without_majority_demotes_nothing():
    langs = {"a.go": "go", "b.ts": "typescript", "c.py": "python", "d.go": "go", "e.py": "python"}
    hits = [_hit("file_page", p) for p in langs]
    out = demote_noise_hits(list(hits), "how does it work", is_why=False, languages=langs)
    assert _paths(out) == _paths(hits)


def test_contract_and_symbol_rows_use_their_file_language():
    langs = {_HCM_PROTO: "proto", "envoy/http/codec.h": "cpp"}
    hits = [
        _hit("api_contract", _HCM_PROTO),
        _hit("symbol_spotlight", "envoy/http/codec.h::ServerConnection"),
    ]
    out = demote_noise_hits(hits, "how is a request decoded", is_why=False, languages=langs)
    assert out[0]["page_type"] == "symbol_spotlight"
