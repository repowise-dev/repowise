"""Tests for the documentation drift endpoints.

Weighted toward what the surface may not claim rather than toward what it
serves. A drift dashboard has two easy ways to lie: reporting a store nobody
filled as a clean tree, and letting a reference row read as "this document
describes your file". Both have a test here.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from repowise.core.analysis.doc_drift.constants import (
    DETECTION_BASIS,
    REFERENCE_BASIS,
)
from repowise.core.analysis.doc_drift.models import (
    DocDriftFindingData,
    DriftKind,
    ResolvedDocReference,
)
from repowise.core.analysis.doc_drift.serialize import derive_doc_drift_id
from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from tests.unit.server.conftest import create_test_repo


def _finding(**over) -> DocDriftFindingData:
    base = {
        "kind": DriftKind.PATH,
        "file_path": "docs/architecture.md",
        "line_number": 42,
        "target": "src/auth.py",
        "confidence": 0.9,
        "reason": "No file matches this path.",
        "origin": "path_no_candidate",
        "evidence": ["line 42: src/auth.py"],
        "raw": "src/auth.py",
        "context": "The resolver lives in src/auth.py.",
    }
    base.update(over)
    return DocDriftFindingData(**base)


async def _seed(session_factory, repo_id: str, *, findings=None, references=None):
    """Write drift rows the way the pipeline writes them."""
    async with get_session(session_factory) as session:
        await crud.replace_doc_drift_findings(session, repo_id, list(findings or []))
        await crud.replace_doc_drift_references(
            session, repo_id, list(references or [])
        )


def _reference(**over) -> ResolvedDocReference:
    base = {
        "doc_path": "docs/overview.md",
        "target_path": "src/auth.py",
        "kind": DriftKind.LINK,
        "line": 9,
        "section": "Extension points",
    }
    base.update(over)
    return ResolvedDocReference(**base)


class TestFindings:
    @pytest.mark.asyncio
    async def test_it_serves_findings_with_the_rollup_over_them(
        self, client: AsyncClient, app
    ) -> None:
        repo = await create_test_repo(client)
        await _seed(
            app.state.session_factory,
            repo["id"],
            findings=[
                _finding(),
                _finding(line_number=50, kind=DriftKind.ANCHOR, confidence=0.95),
                _finding(file_path="docs/cli.md", confidence=0.5),
            ],
            references=[_reference()],
        )

        resp = await client.get(f"/api/repos/{repo['id']}/doc-drift")
        assert resp.status_code == 200
        data = resp.json()

        assert data["unavailable"] is None
        assert len(data["findings"]) == 3
        assert data["findings_emitted"] == 3
        assert data["summary"]["findings_total"] == 3
        assert data["summary"]["documents"] == 2
        assert data["summary"]["confidence"] == {"high": 2, "medium": 1, "low": 0}
        assert data["summary"]["by_kind"] == {"anchor": 1, "path": 2}

    @pytest.mark.asyncio
    async def test_a_count_never_travels_without_what_it_covers(
        self, client: AsyncClient, app
    ) -> None:
        """Most references in a real tree are uncheckable; the basis says so."""
        repo = await create_test_repo(client)
        await _seed(
            app.state.session_factory,
            repo["id"],
            findings=[_finding()],
            references=[_reference()],
        )

        resp = await client.get(f"/api/repos/{repo['id']}/doc-drift")
        assert resp.json()["summary"]["findings_basis"] == DETECTION_BASIS

    @pytest.mark.asyncio
    async def test_the_finding_names_the_document_to_edit(
        self, client: AsyncClient, app
    ) -> None:
        """Not the target it names. The opposite reading is the whole risk."""
        repo = await create_test_repo(client)
        await _seed(
            app.state.session_factory,
            repo["id"],
            findings=[_finding()],
            references=[_reference()],
        )

        finding = (await client.get(f"/api/repos/{repo['id']}/doc-drift")).json()[
            "findings"
        ][0]
        assert finding["file_path"] == "docs/architecture.md"
        assert finding["target"] == "src/auth.py"

    @pytest.mark.asyncio
    async def test_every_finding_carries_the_id_a_client_keys_triage_on(
        self, client: AsyncClient, app
    ) -> None:
        repo = await create_test_repo(client)
        await _seed(
            app.state.session_factory,
            repo["id"],
            findings=[_finding()],
            references=[_reference()],
        )

        finding = (await client.get(f"/api/repos/{repo['id']}/doc-drift")).json()[
            "findings"
        ][0]
        assert finding["id"] == derive_doc_drift_id(
            "docs/architecture.md", "path", 42, "src/auth.py"
        )

    @pytest.mark.asyncio
    async def test_an_unfilled_store_refuses_rather_than_reporting_a_clean_tree(
        self, client: AsyncClient, app
    ) -> None:
        """The pass only runs when an update has work, so "never analysed"
        looks exactly like "every document is correct"."""
        repo = await create_test_repo(client)

        resp = await client.get(f"/api/repos/{repo['id']}/doc-drift")
        assert resp.status_code == 200
        data = resp.json()
        assert data["unavailable"] == "not_computed"
        assert data["findings"] == []
        assert data["summary"] is None

    @pytest.mark.asyncio
    async def test_a_repository_with_documentation_and_no_drift_reads_as_clean(
        self, client: AsyncClient, app
    ) -> None:
        """The other side of the probe: references stored, no findings."""
        repo = await create_test_repo(client)
        await _seed(app.state.session_factory, repo["id"], references=[_reference()])

        data = (await client.get(f"/api/repos/{repo['id']}/doc-drift")).json()
        assert data["unavailable"] is None
        assert data["summary"]["findings_total"] == 0

    @pytest.mark.asyncio
    async def test_a_narrowing_filter_cannot_claim_the_pass_never_ran(
        self, client: AsyncClient, app
    ) -> None:
        """The probe answers a question about the index, not about the query.

        A repository whose documents resolve to nothing stores no references,
        so probing the reference table alone would let ``min_confidence`` turn
        findings that exist into "nobody has ever checked this".
        """
        repo = await create_test_repo(client)
        await _seed(
            app.state.session_factory,
            repo["id"],
            findings=[_finding(confidence=0.5)],
            references=[],
        )

        data = (
            await client.get(
                f"/api/repos/{repo['id']}/doc-drift", params={"min_confidence": 0.9}
            )
        ).json()
        assert data["unavailable"] is None
        assert data["summary"]["findings_total"] == 0

    @pytest.mark.asyncio
    async def test_a_document_filter_matching_nothing_is_not_a_missing_pass(
        self, client: AsyncClient, app
    ) -> None:
        repo = await create_test_repo(client)
        await _seed(
            app.state.session_factory, repo["id"], findings=[_finding()], references=[]
        )

        data = (
            await client.get(
                f"/api/repos/{repo['id']}/doc-drift",
                params={"document": "docs/absent.md"},
            )
        ).json()
        assert data["unavailable"] is None

    @pytest.mark.asyncio
    async def test_an_unknown_repository_is_not_answered_as_documented(
        self, client: AsyncClient
    ) -> None:
        resp = await client.get("/api/repos/does-not-exist/doc-drift")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_the_confidence_floor_filters_the_list_and_its_summary_together(
        self, client: AsyncClient, app
    ) -> None:
        repo = await create_test_repo(client)
        await _seed(
            app.state.session_factory,
            repo["id"],
            findings=[_finding(), _finding(file_path="docs/cli.md", confidence=0.5)],
            references=[_reference()],
        )

        data = (
            await client.get(
                f"/api/repos/{repo['id']}/doc-drift", params={"min_confidence": 0.8}
            )
        ).json()
        assert len(data["findings"]) == 1
        assert data["summary"]["findings_total"] == 1

    @pytest.mark.asyncio
    async def test_the_kind_filter_narrows_the_summary_too(
        self, client: AsyncClient, app
    ) -> None:
        repo = await create_test_repo(client)
        await _seed(
            app.state.session_factory,
            repo["id"],
            findings=[_finding(), _finding(line_number=9, kind=DriftKind.ANCHOR)],
            references=[_reference()],
        )

        data = (
            await client.get(
                f"/api/repos/{repo['id']}/doc-drift", params={"kind": "anchor"}
            )
        ).json()
        assert data["summary"]["by_kind"] == {"anchor": 1}
        assert data["summary"]["findings_total"] == 1

    @pytest.mark.asyncio
    async def test_a_capped_page_still_counts_the_whole_repository(
        self, client: AsyncClient, app
    ) -> None:
        repo = await create_test_repo(client)
        await _seed(
            app.state.session_factory,
            repo["id"],
            findings=[_finding(line_number=n) for n in range(1, 6)],
            references=[_reference()],
        )

        data = (
            await client.get(f"/api/repos/{repo['id']}/doc-drift", params={"limit": 2})
        ).json()
        assert data["findings_emitted"] == 2
        assert len(data["findings"]) == 2
        assert data["summary"]["findings_total"] == 5

    @pytest.mark.asyncio
    async def test_one_document_can_be_asked_about_on_its_own(
        self, client: AsyncClient, app
    ) -> None:
        repo = await create_test_repo(client)
        await _seed(
            app.state.session_factory,
            repo["id"],
            findings=[_finding(), _finding(file_path="docs/cli.md")],
            references=[_reference()],
        )

        data = (
            await client.get(
                f"/api/repos/{repo['id']}/doc-drift",
                params={"document": "docs/cli.md"},
            )
        ).json()
        assert [f["file_path"] for f in data["findings"]] == ["docs/cli.md"]


class TestStaleIndex:
    """An index older than the store must say so, and say which cause.

    Reached for real: starting the server against a pre-drift index creates
    the tables without running the pass, so "table absent" and "table empty"
    are different states with different advice. Telling a reader to re-index
    when the database is merely locked is the third.
    """

    @staticmethod
    async def _drop_tables(session_factory) -> None:
        async with get_session(session_factory) as session:
            await session.execute(text("DROP TABLE doc_drift_findings"))
            await session.execute(text("DROP TABLE doc_drift_references"))

    @pytest.mark.asyncio
    async def test_findings_name_the_stale_index_rather_than_a_failure(
        self, client: AsyncClient, app
    ) -> None:
        repo = await create_test_repo(client)
        await self._drop_tables(app.state.session_factory)

        resp = await client.get(f"/api/repos/{repo['id']}/doc-drift")
        assert resp.status_code == 200
        data = resp.json()
        assert data["unavailable"] == "index_predates_doc_drift"
        assert data["summary"] is None

    @pytest.mark.asyncio
    async def test_the_reverse_view_says_the_same_thing(
        self, client: AsyncClient, app
    ) -> None:
        repo = await create_test_repo(client)
        await self._drop_tables(app.state.session_factory)

        data = (
            await client.get(
                f"/api/repos/{repo['id']}/doc-drift/references",
                params={"target": "src/auth.py"},
            )
        ).json()
        assert data["unavailable"] == "index_predates_doc_drift"
        # The refusal still carries what a reference would have claimed, so a
        # reader is never left to infer it from an empty list.
        assert data["references_basis"] == REFERENCE_BASIS


class TestReferences:
    @pytest.mark.asyncio
    async def test_it_answers_which_documents_name_a_file(
        self, client: AsyncClient, app
    ) -> None:
        repo = await create_test_repo(client)
        await _seed(
            app.state.session_factory,
            repo["id"],
            references=[_reference(), _reference(doc_path="docs/cli.md", line=3)],
        )

        resp = await client.get(
            f"/api/repos/{repo['id']}/doc-drift/references",
            params={"target": "src/auth.py"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["target_path"] == "src/auth.py"
        assert data["documents"] == 2
        assert {r["document"] for r in data["references"]} == {
            "docs/overview.md",
            "docs/cli.md",
        }
        assert data["references"][0]["section"] in {"Extension points", ""}

    @pytest.mark.asyncio
    async def test_absence_is_not_proof_that_nothing_documents_the_file(
        self, client: AsyncClient, app
    ) -> None:
        repo = await create_test_repo(client)
        await _seed(app.state.session_factory, repo["id"], references=[_reference()])

        data = (
            await client.get(
                f"/api/repos/{repo['id']}/doc-drift/references",
                params={"target": "src/never_mentioned.py"},
            )
        ).json()
        assert data["references"] == []
        assert data["references_basis"] == REFERENCE_BASIS

    @pytest.mark.asyncio
    async def test_an_unfilled_store_refuses_the_reverse_question_too(
        self, client: AsyncClient
    ) -> None:
        repo = await create_test_repo(client)

        data = (
            await client.get(
                f"/api/repos/{repo['id']}/doc-drift/references",
                params={"target": "src/auth.py"},
            )
        ).json()
        assert data["unavailable"] == "not_computed"
        assert data["references"] == []

    @pytest.mark.asyncio
    async def test_drift_is_reported_against_the_document_not_the_target(
        self, client: AsyncClient, app
    ) -> None:
        """``documents_with_drift`` counts a document's own findings, which
        need not be about the file that was asked about."""
        repo = await create_test_repo(client)
        await _seed(
            app.state.session_factory,
            repo["id"],
            findings=[_finding(file_path="docs/overview.md", target="src/gone.py")],
            references=[_reference()],
        )

        data = (
            await client.get(
                f"/api/repos/{repo['id']}/doc-drift/references",
                params={"target": "src/auth.py"},
            )
        ).json()
        assert data["documents_with_drift"] == [
            {"document": "docs/overview.md", "findings": 1}
        ]

    @pytest.mark.asyncio
    async def test_a_link_with_a_fragment_is_one_mention_not_two(
        self, client: AsyncClient, app
    ) -> None:
        repo = await create_test_repo(client)
        await _seed(
            app.state.session_factory,
            repo["id"],
            references=[
                _reference(kind=DriftKind.LINK),
                _reference(kind=DriftKind.ANCHOR),
            ],
        )

        data = (
            await client.get(
                f"/api/repos/{repo['id']}/doc-drift/references",
                params={"target": "src/auth.py"},
            )
        ).json()
        assert len(data["references"]) == 1
        assert data["documents"] == 1

    @pytest.mark.asyncio
    async def test_a_document_past_the_display_cap_still_reports_its_drift(
        self, client: AsyncClient, app
    ) -> None:
        repo = await create_test_repo(client)
        await _seed(
            app.state.session_factory,
            repo["id"],
            findings=[_finding(file_path="docs/z.md", target="src/gone.py")],
            references=[
                _reference(doc_path=f"docs/{name}.md", line=1)
                for name in ("a", "b", "z")
            ],
        )

        data = (
            await client.get(
                f"/api/repos/{repo['id']}/doc-drift/references",
                params={"target": "src/auth.py", "limit": 1},
            )
        ).json()
        assert len(data["references"]) == 1
        assert data["documents_with_drift"] == [{"document": "docs/z.md", "findings": 1}]

    @pytest.mark.asyncio
    async def test_a_capped_answer_says_how_much_it_left_out(
        self, client: AsyncClient, app
    ) -> None:
        """Otherwise 50 rows sit beside an uncapped document count with no way
        for a reader to tell a complete answer from a slice."""
        repo = await create_test_repo(client)
        await _seed(
            app.state.session_factory,
            repo["id"],
            references=[
                _reference(doc_path=f"docs/{n}.md", line=1) for n in range(1, 6)
            ],
        )

        data = (
            await client.get(
                f"/api/repos/{repo['id']}/doc-drift/references",
                params={"target": "src/auth.py", "limit": 2},
            )
        ).json()
        assert data["references_emitted"] == 2
        assert data["references_total"] == 5
        assert data["documents"] == 5

    @pytest.mark.asyncio
    async def test_an_unknown_repository_is_not_answered_as_undocumented(
        self, client: AsyncClient
    ) -> None:
        resp = await client.get(
            "/api/repos/does-not-exist/doc-drift/references",
            params={"target": "src/auth.py"},
        )
        assert resp.status_code == 404
