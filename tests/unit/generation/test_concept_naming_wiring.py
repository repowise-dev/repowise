"""The namer is wired into the real pipeline, and cannot break it.

The naming machinery existed and was tested before any of this ran in
production: the selector derived every shipped title from a path and the
planner was reachable only from its own unit tests. So these tests assert
reachability through ``run_pipeline`` rather than by calling the planner,
because calling the planner is exactly what passed while nothing shipped.

The rest assert the guarantees naming is allowed to make. It may change what
a page is called. It may not change which files a page covers, what its id
is, or whether the run finishes.
"""

from __future__ import annotations

import json

from repowise.core.generation.concept_tree.grouping import ConceptGroup
from repowise.core.generation.concept_tree.planner import PlannerInputs, name_groups
from repowise.core.pipeline import run_pipeline
from repowise.core.pipeline.modes import OrchestratorMode
from repowise.core.providers.llm.base import GeneratedResponse
from repowise.core.providers.llm.mock import MockProvider

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class NamingProvider(MockProvider):
    """A provider that answers the naming call and mocks everything else.

    Keyed off the naming system prompt rather than call order: naming happens
    before any page is written, but pinning the assertion to "call zero" would
    make an unrelated reordering look like a naming failure.
    """

    def __init__(self, naming_payload: object, **kwargs) -> None:
        super().__init__(**kwargs)
        self.naming_payload = naming_payload
        self.naming_calls = 0

    async def generate(self, system_prompt: str = "", user_prompt: str = "", **kwargs):
        if "information architect" in system_prompt:
            self.naming_calls += 1
            self._calls.append({"system_prompt": system_prompt, **kwargs})
            body = (
                self.naming_payload
                if isinstance(self.naming_payload, str)
                else json.dumps(self.naming_payload)
            )
            return GeneratedResponse(content=body, input_tokens=10, output_tokens=10)
        return await super().generate(system_prompt, user_prompt, **kwargs)


#: Enough distinct subtrees, and enough files in each, that the grouper's size
#: ladder produces several groups. A single-group fixture cannot test naming,
#: ordering or title collisions, and it passed as though it could.
_PACKAGES = ("ingest", "render", "storage", "transport", "analysis", "reporting")


def _write_repo(repo_path):
    """A repository with several clearly different subsystems and a root file."""
    repo_path.mkdir(parents=True, exist_ok=True)
    for pkg in _PACKAGES:
        (repo_path / "src" / pkg).mkdir(parents=True, exist_ok=True)
        for i in range(12):
            (repo_path / "src" / pkg / f"mod{i}.py").write_text(
                f"def {pkg}_{i}() -> int:\n    return {i}\n", encoding="utf-8"
            )
    (repo_path / "main.py").write_text("def main() -> None:\n    pass\n", encoding="utf-8")
    return repo_path


async def _run_deterministic(repo_path):
    """The keyless run. A ``MockProvider`` is a provider, so it is not this."""
    return await run_pipeline(
        repo_path,
        generate_docs=True,
        llm_client=None,
        concurrency=1,
        mode=OrchestratorMode.DETERMINISTIC,
    )


def _naming_payload_for(titles: dict[str, str]) -> dict:
    return {
        "sections": [{"title": "Core Engine", "groups": list(titles)}],
        "names": {
            gid: {"title": title, "scope": f"Covers the {title} work and nothing else."}
            for gid, title in titles.items()
        },
    }


async def _run(repo_path, provider):
    return await run_pipeline(
        repo_path,
        generate_docs=True,
        llm_client=provider,
        concurrency=1,
        # No ``test_run``: it caps the pipeline to the top ten files by
        # PageRank, which collapses this fixture to a single group, and a
        # one-group run cannot exercise naming, ordering or collisions.
    )


def _module_pages(result):
    return [p for p in result.generated_pages if p.page_type == "module_page"]


# ---------------------------------------------------------------------------
# Reachability
# ---------------------------------------------------------------------------


async def test_the_namer_runs_on_the_real_generation_path(tmp_path):
    """A model title reaches a shipped page. This is the whole change."""
    repo = _write_repo(tmp_path / "repo")
    provider = NamingProvider(
        _naming_payload_for({f"g{i:02d}": f"Title {i}" for i in range(1, 21)})
    )

    result = await _run(repo, provider)

    assert provider.naming_calls == 1, "naming must be exactly one call, not one per page"
    pages = _module_pages(result)
    assert pages, "fixture produced no concept pages, so it cannot test naming"
    assert any(
        p.title.startswith("Title ") for p in pages
    ), f"no model title reached a page: {[p.title for p in pages]}"


async def test_the_keyless_path_never_calls_a_namer(tmp_path):
    """Keyless is a real tree with plainer names, and it costs nothing.

    Asserted through the stamp rather than through a spy: the keyless run has
    no provider to record calls on, which is the point of it.
    """
    repo = _write_repo(tmp_path / "repo")

    result = await _run_deterministic(repo)
    pages = _module_pages(result)

    assert pages
    assert all(p.title.strip() for p in pages)
    assert all(
        "concept_order" not in p.metadata for p in pages
    ), "a keyless run stamped a reading order, so something named it"


async def test_a_run_that_writes_no_concept_page_does_not_name_one(tmp_path):
    """An incremental update must not buy an outline it throws away.

    ``file_pages_only`` returns before the concept level, so a naming call on
    that path is a whole-repository request whose result nothing reads. On a
    post-commit update hook that is a bill per commit for nothing.
    """
    from repowise.core.generation import GenerationConfig

    repo = _write_repo(tmp_path / "repo")
    provider = NamingProvider(_naming_payload_for({"g01": "Anything At All"}))

    await run_pipeline(
        repo,
        generate_docs=True,
        llm_client=provider,
        concurrency=1,
        generation_config=GenerationConfig(max_concurrency=1, file_pages_only=True),
    )

    assert provider.naming_calls == 0, "an incremental run paid for a naming call"


# ---------------------------------------------------------------------------
# Identity does not follow the title (D2)
# ---------------------------------------------------------------------------


async def test_renaming_a_page_does_not_mint_a_new_page(tmp_path):
    """Two runs, different titles, identical ids and identical membership.

    The load-bearing one. If identity followed the title, every regeneration
    would strand the old row and a re-index would double the wiki.
    """
    repo = _write_repo(tmp_path / "repo")

    first = await _run(
        repo, NamingProvider(_naming_payload_for({f"g{i:02d}": f"Alpha {i}" for i in range(1, 21)}))
    )
    second = await _run(
        repo, NamingProvider(_naming_payload_for({f"g{i:02d}": f"Omega {i}" for i in range(1, 21)}))
    )

    a = {p.page_id: p for p in _module_pages(first)}
    b = {p.page_id: p for p in _module_pages(second)}

    assert a and b
    assert set(a) == set(b), "a rename moved a page id"
    assert {p.structural_key for p in a.values()} == {p.structural_key for p in b.values()}
    for pid, page in a.items():
        assert page.metadata["file_paths"] == b[pid].metadata["file_paths"]
    # The fixture is only meaningful if the titles actually differed.
    assert {p.title for p in a.values()} != {p.title for p in b.values()}


async def test_the_partition_is_identical_with_and_without_a_namer(tmp_path):
    """D5: adding a key changes the prose, never the shape."""
    repo = _write_repo(tmp_path / "repo")

    named = await _run(
        repo, NamingProvider(_naming_payload_for({f"g{i:02d}": f"Named {i}" for i in range(1, 21)}))
    )
    plain = await _run_deterministic(repo)

    n = {p.page_id: sorted(p.metadata["file_paths"]) for p in _module_pages(named)}
    p = {p.page_id: sorted(p.metadata["file_paths"]) for p in _module_pages(plain)}

    assert n, "fixture produced no concept pages"
    assert n == p


# ---------------------------------------------------------------------------
# A bad naming call costs titles, never the run
# ---------------------------------------------------------------------------


async def test_a_provider_that_raises_leaves_a_complete_wiki(tmp_path):
    class Exploding(NamingProvider):
        async def generate(self, system_prompt: str = "", user_prompt: str = "", **kwargs):
            if "information architect" in system_prompt:
                raise RuntimeError("naming is down")
            return await MockProvider.generate(self, system_prompt, user_prompt, **kwargs)

    repo = _write_repo(tmp_path / "repo")
    result = await _run(repo, Exploding(None))

    pages = _module_pages(result)
    assert pages
    assert all(p.title for p in pages)


async def test_a_malformed_response_leaves_a_complete_wiki(tmp_path):
    repo = _write_repo(tmp_path / "repo")
    result = await _run(repo, NamingProvider("not json at all {{{"))

    pages = _module_pages(result)
    assert pages
    assert all(p.title for p in pages)


async def test_invented_group_ids_are_discarded(tmp_path):
    """A response naming only groups that do not exist changes nothing."""
    repo = _write_repo(tmp_path / "repo")
    payload = _naming_payload_for({"g99": "Invented", "zzz": "Also Invented"})
    result = await _run(repo, NamingProvider(payload))

    pages = _module_pages(result)
    assert pages
    assert not any(p.title in ("Invented", "Also Invented") for p in pages)


async def test_a_skipped_group_keeps_a_deterministic_title(tmp_path):
    """Naming one of eight groups leaves the other seven named, not blank."""
    repo = _write_repo(tmp_path / "repo")
    result = await _run(repo, NamingProvider(_naming_payload_for({"g01": "Only This One"})))

    pages = _module_pages(result)
    assert len(pages) > 1, "fixture must produce more than one group"
    assert all(p.title.strip() for p in pages)
    assert len({p.title for p in pages}) == len(pages), "titles collided"


# ---------------------------------------------------------------------------
# The reasoning setting is honoured
# ---------------------------------------------------------------------------


async def test_the_naming_call_honours_the_run_reasoning_setting(tmp_path):
    repo = _write_repo(tmp_path / "repo")
    (repo / ".repowise").mkdir(exist_ok=True)
    (repo / ".repowise" / "config.yaml").write_text("reasoning: off\n", encoding="utf-8")

    provider = NamingProvider(_naming_payload_for({"g01": "Anything At All"}))
    await _run(repo, provider)

    naming = [
        c for c in provider.calls if "information architect" in (c.get("system_prompt") or "")
    ]
    assert naming, "the naming call did not happen"
    assert all(c.get("reasoning") == "off" for c in naming)


# ---------------------------------------------------------------------------
# Section and order
# ---------------------------------------------------------------------------


async def test_the_section_and_order_reach_the_page(tmp_path):
    repo = _write_repo(tmp_path / "repo")
    payload = {
        "sections": [
            {"title": "Second Section", "groups": ["g02"]},
            {"title": "First Section", "groups": ["g01"]},
        ],
        "names": {
            "g01": {"title": "Ingest Pipeline", "scope": "Covers ingest and not rendering."},
            "g02": {"title": "Render Pipeline", "scope": "Covers rendering and not ingest."},
        },
    }
    result = await _run(repo, NamingProvider(payload))

    stamped = [p for p in _module_pages(result) if p.metadata.get("concept_section")]
    assert stamped, "no page carried the namer's section"
    orders = [p.metadata["concept_order"] for p in stamped]
    assert len(set(orders)) == len(orders), "two pages share a position in the reading order"


# ---------------------------------------------------------------------------
# name_groups itself
# ---------------------------------------------------------------------------


def _groups() -> list[ConceptGroup]:
    return [
        ConceptGroup(
            members=[f"src/a/f{i}.py" for i in range(3)], dirs=["src/a"], target_path="src/a"
        ),
        ConceptGroup(
            members=[f"src/b/f{i}.py" for i in range(3)], dirs=["src/b"], target_path="src/b"
        ),
    ]


async def test_name_groups_never_repartitions_what_it_is_given():
    groups = _groups()
    keys_before = [g.structural_key for g in groups]

    outline, _ = await name_groups(
        groups,
        PlannerInputs(repo_name="r", production_files=[m for g in groups for m in g.members]),
        provider=None,
    )

    assert [p.structural_key for p in outline.pages] == keys_before
    assert [g.structural_key for g in groups] == keys_before, "naming mutated the grouping"


async def test_repair_is_budgeted_for_the_number_of_titles_it_must_fix():
    """A first call that names nothing leaves repair naming the whole repo.

    Repair was written for the few titles a long request loses near the end of
    its list, and a flat token budget covers that. It does not cover the case
    that matters: every group falling back at once, where a truncated repair
    ships an outline whose every title came from a path. Observed for real.
    """
    seen: list[int] = []

    class SectionsOnly(MockProvider):
        """Answers with sections and no names, so every group falls back."""

        async def generate(self, system_prompt: str = "", user_prompt: str = "", **kwargs):
            seen.append(int(kwargs.get("max_tokens") or 0))
            if "GROUPS TO RENAME" in user_prompt:
                return GeneratedResponse(content="{}", input_tokens=1, output_tokens=1)
            gids = [f"g{i:02d}" for i in range(1, len(_many_groups()) + 1)]
            return GeneratedResponse(
                content=json.dumps({"sections": [{"title": "Core", "groups": gids}], "names": {}}),
                input_tokens=1,
                output_tokens=1,
            )

    groups = _many_groups()
    await name_groups(
        groups,
        PlannerInputs(repo_name="r", production_files=[m for g in groups for m in g.members]),
        provider=SectionsOnly(),
    )

    assert len(seen) == 2, "repair did not run after a response that named nothing"
    assert seen[1] > 2000, (
        f"repair asked for {seen[1]} tokens to rename {len(groups)} groups, "
        "which truncates and leaves every title path-derived"
    )


def _many_groups() -> list[ConceptGroup]:
    return [
        ConceptGroup(
            members=[f"src/p{n}/f{i}.py" for i in range(4)],
            dirs=[f"src/p{n}"],
            target_path=f"src/p{n}",
        )
        for n in range(30)
    ]


async def test_duplicate_model_titles_are_forced_apart():
    """Repair can decline. A reader still must not see two identical rows."""

    class SameTitle(MockProvider):
        async def generate(self, system_prompt: str = "", user_prompt: str = "", **kwargs):
            return GeneratedResponse(
                content=json.dumps(
                    {
                        "sections": [{"title": "Core", "groups": ["g01", "g02"]}],
                        "names": {
                            "g01": {"title": "Shared Name", "scope": "Covers one half of it."},
                            "g02": {"title": "Shared Name", "scope": "Covers the other half."},
                        },
                    }
                ),
                input_tokens=1,
                output_tokens=1,
            )

    groups = _groups()
    outline, _ = await name_groups(
        groups,
        PlannerInputs(repo_name="r", production_files=[m for g in groups for m in g.members]),
        provider=SameTitle(),
        repair=False,
    )

    titles = [p.title for p in outline.pages]
    assert len(set(titles)) == len(titles), f"duplicate titles survived: {titles}"
