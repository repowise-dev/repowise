"""The planner: what the model can and cannot break, and what the validator sees.

The design claim this file exists to hold is narrow and testable: **naming is
the only thing the model decides**. Coverage, membership and page identity come
from the deterministic partition, so a model that skips half the groups, echoes
back ids that were never in the input, or returns nothing at all still leaves a
complete and correctly-identified outline behind.
"""

from __future__ import annotations

import pytest

from repowise.core.generation.concept_tree.grouping import (
    GroupingParams,
    group_files,
)
from repowise.core.generation.concept_tree.naming import (
    build_payload,
    decode_response,
    deterministic_title,
)
from repowise.core.generation.concept_tree.planner import PlannerInputs, plan_outline
from repowise.core.generation.concept_tree.validation import validate_outline

TINY = GroupingParams(min_files=3, max_files=6)

FILES = [
    "src/ingest/reader.py",
    "src/ingest/parser.py",
    "src/ingest/walker.py",
    "src/ingest/cache.py",
    "src/render/html.py",
    "src/render/markdown.py",
    "src/render/theme.py",
    "src/render/layout.py",
]
TESTS = {"tests/test_reader.py"}


def _inputs() -> PlannerInputs:
    return PlannerInputs(
        repo_name="demo",
        production_files=list(FILES),
        layer_labels={"layer:core": "Core"},
        test_files=set(TESTS),
    )


class FakeProvider:
    """A provider whose reply is whatever the test needs it to be."""

    def __init__(self, content: str):
        self.content = content
        self.calls: list[str] = []

    async def generate(self, *, system_prompt, user_prompt, **kwargs):
        self.calls.append(user_prompt)

        class R:
            content = self.content

        return R()


class TestModelCannotBreakStructure:
    @pytest.mark.asyncio
    async def test_a_model_that_returns_nothing_still_covers_every_file(self):
        _outline, report = await plan_outline(
            _inputs(), provider=FakeProvider(""), params=TINY, repair=False
        )
        assert report.coverage == 1.0
        assert report.unclaimed_files == []
        assert report.ok

    @pytest.mark.asyncio
    async def test_a_model_that_invents_group_ids_does_not_add_pages(self):
        groups = group_files(FILES, params=TINY)
        _payload, index = build_payload(groups)
        data = {
            "sections": [
                {"title": "Made Up", "groups": ["g99", "gzz"]},
                {"title": "Real", "groups": list(index)},
            ],
            "names": {"g99": {"title": "Ghost Page", "scope": "x"}},
        }
        named, invented = decode_response(data, index)
        assert invented == ["g99", "gzz"]
        assert len(named) == len(index)
        assert "Ghost Page" not in [n.title for n in named]

    @pytest.mark.asyncio
    async def test_a_model_that_skips_groups_leaves_them_named_anyway(self):
        groups = group_files(FILES, params=TINY)
        _payload, index = build_payload(groups)
        first = next(iter(index))
        data = {"sections": [], "names": {first: {"title": "Real Title", "scope": "s"}}}
        named, _ = decode_response(data, index)
        assert len(named) == len(index)
        skipped = [n for n in named if n.fallback]
        assert skipped, "fixture must leave at least one group unnamed"
        assert all(n.title for n in named)

    def test_decode_survives_a_non_list_group_field(self):
        """A model asked for a list of ids returned a count instead.

        ``section.get("groups") or []`` passes a non-empty scalar straight
        through to the loop, which raised. Asserted against ``decode_response``
        directly, because the planner also wraps this call and a test that only
        went through the planner would pass on the safety net alone.
        """
        groups = group_files(FILES, params=TINY)
        _payload, index = build_payload(groups)
        named, invented = decode_response(
            {"sections": [{"title": "Core", "groups": 3}], "names": {}}, index
        )
        assert len(named) == len(index)
        assert invented == []

    def test_decode_ignores_group_ids_that_are_not_scalars(self):
        groups = group_files(FILES, params=TINY)
        _payload, index = build_payload(groups)
        named, _ = decode_response(
            {"sections": [{"title": "Core", "groups": [{"a": 1}, ["b"], None]}],
             "names": {}},
            index,
        )
        assert len(named) == len(index)

    @pytest.mark.asyncio
    async def test_a_non_list_group_field_does_not_crash_the_run(self):
        """The same malformed response, end to end through the planner."""
        _outline, report = await plan_outline(
            _inputs(),
            provider=FakeProvider('{"sections":[{"title":"Core","groups":3}],"names":{}}'),
            params=TINY,
            repair=False,
        )
        assert report.coverage == 1.0
        assert report.ok

    @pytest.mark.asyncio
    async def test_junk_types_in_the_response_are_ignored(self):
        content = (
            '{"sections": {"not": "a list"},'
            ' "names": {"g01": ["not", "a", "dict"], "g02": 7}}'
        )
        outline, report = await plan_outline(
            _inputs(), provider=FakeProvider(content), params=TINY, repair=False
        )
        assert report.coverage == 1.0
        assert report.ok
        assert all(p.title for p in outline.pages)

    @pytest.mark.asyncio
    async def test_an_enormous_title_is_bounded(self):
        groups = group_files(FILES, params=TINY)
        _payload, index = build_payload(groups)
        gid = next(iter(index))
        data = {"sections": [], "names": {gid: {"title": "Wildly " * 400, "scope": "s"}}}
        named, _ = decode_response(data, index)
        assert all(len(n.title) <= 120 for n in named)

    @pytest.mark.asyncio
    async def test_a_model_claiming_one_group_twice_places_it_once(self):
        groups = group_files(FILES, params=TINY)
        _payload, index = build_payload(groups)
        gid = next(iter(index))
        data = {
            "sections": [
                {"title": "First", "groups": [gid]},
                {"title": "Second", "groups": [gid]},
            ],
            "names": {},
        }
        named, _ = decode_response(data, index)
        assert sum(1 for n in named if n.group is index[gid]) == 1


class TestDeterministicPath:
    @pytest.mark.asyncio
    async def test_no_provider_still_produces_a_valid_outline(self):
        outline, report = await plan_outline(_inputs(), provider=None, params=TINY)
        assert outline.naming_mode == "deterministic"
        assert report.coverage == 1.0
        assert report.ok

    @pytest.mark.asyncio
    async def test_deterministic_mode_ignores_a_provider_that_is_present(self):
        provider = FakeProvider('{"sections":[],"names":{}}')
        outline, _ = await plan_outline(
            _inputs(), provider=provider, deterministic=True, params=TINY
        )
        assert provider.calls == []
        assert outline.naming_mode == "deterministic"

    @pytest.mark.asyncio
    async def test_the_keyless_tree_has_the_same_shape_as_the_named_one(self):
        """D5: adding a key changes the prose, never the partition."""
        keyless, _ = await plan_outline(_inputs(), provider=None, params=TINY)
        named, _ = await plan_outline(
            _inputs(), provider=FakeProvider(""), params=TINY, repair=False
        )
        assert sorted(p.structural_key for p in keyless.pages) == sorted(
            p.structural_key for p in named.pages
        )

    def test_deterministic_titles_are_not_bare_directory_names(self):
        """The name of this test is what it must actually assert.

        A word-count bound alone would pass on "src ingest", which is the
        exact failure the validator's bare-directory check exists to catch.
        """
        from repowise.core.generation.concept_tree.validation import (
            _is_bare_directory,
        )

        for group in group_files(FILES, params=TINY):
            title = deterministic_title(group)
            assert 2 <= len(title.split()) <= 7
            assert not _is_bare_directory(title, group.dirs), title


class TestValidator:
    def _outline(self):
        from repowise.core.generation.concept_tree.models import (
            ConceptOutline,
            ConceptPage,
            ConceptSection,
        )

        groups = group_files(FILES, params=TINY)
        outline = ConceptOutline()
        outline.sections.append(
            ConceptSection(
                title="All",
                pages=[
                    ConceptPage(title=f"Page {i}", scope="s", group=g)
                    for i, g in enumerate(groups)
                ],
            )
        )
        outline.number_sections()
        return outline

    def test_a_clean_outline_has_no_hard_failures(self):
        report = validate_outline(
            self._outline(),
            all_files=set(FILES),
            test_files=TESTS,
            max_files_per_page=TINY.max_files,
        )
        assert report.hard_failures == []

    def test_an_invented_member_is_a_hard_failure(self):
        outline = self._outline()
        outline.pages[0].group.members.append("src/ghost/nowhere.py")
        report = validate_outline(
            outline,
            all_files=set(FILES),
            test_files=TESTS,
            max_files_per_page=TINY.max_files,
        )
        assert report.invented_paths == ["src/ghost/nowhere.py"]
        assert not report.ok

    def test_an_unclaimed_file_is_a_hard_failure(self):
        report = validate_outline(
            self._outline(),
            all_files=set(FILES) | {"src/orphan.py"},
            test_files=TESTS,
            max_files_per_page=TINY.max_files,
        )
        assert report.unclaimed_files == ["src/orphan.py"]
        assert not report.ok

    def test_two_pages_on_one_target_path_is_a_hard_failure(self):
        outline = self._outline()
        outline.pages[1].group.target_path = outline.pages[0].group.target_path
        report = validate_outline(
            outline,
            all_files=set(FILES),
            test_files=TESTS,
            max_files_per_page=TINY.max_files,
        )
        assert report.duplicate_target_paths
        assert not report.ok

    def test_a_test_file_in_the_tree_is_a_hard_failure(self):
        outline = self._outline()
        outline.pages[0].group.members.append("tests/test_reader.py")
        report = validate_outline(
            outline,
            all_files=set(FILES) | TESTS,
            test_files=TESTS,
            max_files_per_page=TINY.max_files,
        )
        assert report.test_paths_included == ["tests/test_reader.py"]
        assert not report.ok

    def test_a_long_title_is_reported_but_does_not_block(self):
        outline = self._outline()
        outline.pages[0].title = "A Very Long Title That Runs On And On Forever Indeed"
        report = validate_outline(
            outline,
            all_files=set(FILES),
            test_files=TESTS,
            max_files_per_page=TINY.max_files,
        )
        assert report.bad_length_titles
        assert report.ok, "title length is taste, not correctness"


class TestPayload:
    def test_group_ids_carry_no_path_information(self):
        """A fabricated id must be recognisable as fabricated."""
        groups = group_files(FILES, params=TINY)
        _payload, index = build_payload(groups)
        for gid in index:
            assert gid.startswith("g") and gid[1:].isdigit()

    def test_filenames_are_sampled_across_every_directory(self):
        """A merged group must not be described by only one of its halves."""
        from repowise.core.generation.concept_tree.grouping import ConceptGroup

        group = ConceptGroup(
            members=[f"src/aaa/a{i}.py" for i in range(6)]
            + [f"src/zzz/z{i}.py" for i in range(6)],
            dirs=["src/aaa", "src/zzz"],
            target_path="src/aaa",
        )
        payload, _ = build_payload([group], max_filenames=4)
        names = payload["groups"][0]["names"]
        assert any(n.startswith("a") for n in names)
        assert any(n.startswith("z") for n in names), names

    def test_sibling_directories_are_shown_relative_to_what_they_share(self):
        from repowise.core.generation.concept_tree.grouping import ConceptGroup

        group = ConceptGroup(
            members=["src/git_indexer/a.py", "src/graph/b.py"],
            dirs=["src/git_indexer", "src/graph"],
            target_path="src/git_indexer",
        )
        payload, _ = build_payload([group])
        assert sorted(payload["groups"][0]["subdirs"]) == ["git_indexer", "graph"]
