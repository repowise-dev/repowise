"""``candidates``: the files a search_codebase call actually reached.

Same defect as get_answer's A10, on the other tool. A caller asking for ten
results gets ten *pages*, and a page is not always a file: module pages are
named by a structural group key that reads like a directory, SCC pages by
``scc-<hash>``, onboarding pages by a slot. Serving those in a field a consumer
opens is finding A15, and it is what made half the slots the caller paid for
unopenable.

``results`` keeps its page semantics, because ranking a module page is
correct. ``candidates`` is the navigation half: distinct, openable, drawn from
the full ranked pool rather than the visible window so junk in the window does
not cost the caller a file.
"""

from __future__ import annotations

from repowise.server.mcp_server._page_paths import (
    file_candidates,
    file_path_of,
    hit_file_path,
)


def _page(page_type, target_path):
    return {"page_type": page_type, "target_path": target_path}


class TestFilePathOf:
    def test_a_file_page_names_its_file(self):
        assert file_path_of("file_page", "pkg/cmd/release/list.go") == "pkg/cmd/release/list.go"

    def test_a_symbol_page_resolves_to_the_file_it_is_cut_from(self):
        assert file_path_of("symbol_spotlight", "api/client.go::HTTP") == "api/client.go"

    def test_api_contract_and_infra_pages_are_file_backed(self):
        assert file_path_of("api_contract", "api/queries.go") == "api/queries.go"
        assert file_path_of("infra_page", "Dockerfile") == "Dockerfile"

    def test_a_module_page_names_no_file_even_though_its_key_looks_like_one(self):
        """The trap this module exists for: ``pkg/cmd/release`` is a group key.

        It is path-shaped, so every heuristic that asks "does this look like a
        path" says yes, and an agent told to read it gets a directory.
        """
        assert file_path_of("module_page", "pkg/cmd/release") is None

    def test_scc_overview_and_onboarding_pages_name_no_file(self):
        assert file_path_of("scc_page", "scc-8f21ab") is None
        assert file_path_of("repo_overview", "cli") is None
        assert file_path_of("onboarding", "onboarding/how_it_works") is None
        assert file_path_of("layer_page", "presentation") is None

    def test_an_empty_target_path_resolves_to_nothing(self):
        assert file_path_of("file_page", "") is None
        assert file_path_of("file_page", None) is None

    def test_an_unknown_page_type_is_refused_rather_than_guessed(self):
        """Default deny. A page type nobody has classified is not a path."""
        assert file_path_of("some_future_page", "looks/like/a/path.go") is None


class TestHitFilePath:
    def test_a_symbol_index_hit_names_its_file_directly(self):
        assert hit_file_path({"file": "api/client.go", "symbol_id": "s1"}) == "api/client.go"

    def test_page_type_wins_over_a_looser_key(self):
        """A page hit that resolves to no file must not fall through.

        Otherwise the one field that is allowed to be a page id leaks back out
        through the field that is not.
        """
        hit = {"page_type": "module_page", "target_path": "pkg/cmd", "file": "pkg/cmd"}
        assert hit_file_path(hit) is None


class TestFileCandidates:
    def test_pages_that_name_no_file_do_not_consume_a_slot(self):
        hits = [
            _page("onboarding", "onboarding/how_it_works"),
            _page("file_page", "a.go"),
            _page("module_page", "pkg/cmd"),
            _page("file_page", "b.go"),
        ]
        assert file_candidates(hits, limit=10) == [{"path": "a.go"}, {"path": "b.go"}]

    def test_symbol_pages_in_one_file_collapse_to_one_entry(self):
        hits = [
            _page("symbol_spotlight", "api/client.go::HTTP"),
            _page("symbol_spotlight", "api/client.go::Do"),
            _page("file_page", "api/client.go"),
            _page("file_page", "api/queries.go"),
        ]
        assert file_candidates(hits, limit=10) == [
            {"path": "api/client.go"},
            {"path": "api/queries.go"},
        ]

    def test_rank_order_is_preserved(self):
        hits = [_page("file_page", f"f{i}.go") for i in range(5)]
        assert [e["path"] for e in file_candidates(hits, limit=10)] == [
            "f0.go",
            "f1.go",
            "f2.go",
            "f3.go",
            "f4.go",
        ]

    def test_the_pool_backfills_what_the_window_wasted(self):
        """The whole point of drawing from the pool and not the cut window.

        Four of the first five hits name no file. Asked for three candidates,
        the caller gets three files, reached from below where the visible
        window ends.
        """
        hits = [
            _page("module_page", "pkg/cmd"),
            _page("file_page", "a.go"),
            _page("scc_page", "scc-11"),
            _page("repo_overview", "cli"),
            _page("onboarding", "onboarding/how_it_works"),
            _page("file_page", "b.go"),
            _page("file_page", "c.go"),
        ]
        assert file_candidates(hits, limit=3) == [
            {"path": "a.go"},
            {"path": "b.go"},
            {"path": "c.go"},
        ]

    def test_capped_at_the_limit_asked_for(self):
        hits = [_page("file_page", f"f{i}.go") for i in range(40)]
        assert len(file_candidates(hits, limit=10)) == 10

    def test_no_files_reached_yields_no_block(self):
        hits = [_page("repo_overview", "cli"), _page("onboarding", "onboarding/how_it_works")]
        assert file_candidates(hits, limit=10) == []

    def test_empty_pool_yields_no_block(self):
        assert file_candidates([], limit=10) == []
