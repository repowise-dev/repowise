"""Sealed cross-tool identifier and evidence-reference round trips."""

from __future__ import annotations

import re
import subprocess
from copy import copy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import pytest
from sqlalchemy import select

from repowise.core.persistence.models import (
    DeadCodeFinding,
    GraphEdge,
    GraphNode,
    HealthFinding,
    RefactoringSuggestion,
    WikiSymbol,
)


def _write_numbered_source(root) -> None:
    service = ["# filler" for _ in range(750)]
    service[0] = '"""Authentication service."""'
    service[9] = "class AuthService:"
    service[10:19] = [f"    class_value_{i} = {i}" for i in range(9)]
    service[19] = "    async def login(self, username: str, password: str):"
    service[20:40] = [f"        step_{i} = {i}" for i in range(20)]
    service[40] = "        return username"
    service[99] = "def load_users():"
    service[100] = "    return []"
    service[700] = "class AfterAuth:"
    service[701] = "    pass"

    models = ["# filler" for _ in range(35)]
    models[4] = "class User:"
    models[5:30] = [f"    field_{i} = {i}" for i in range(25)]
    models[30] = "def fetch_one():"
    models[31] = "    return None"

    files = {
        "src/auth/service.py": "\n".join(service) + "\n",
        "src/auth/middleware.py": "from .service import AuthService\n",
        "src/db/models.py": "\n".join(models) + "\n",
        "src/legacy/old_auth.py": (
            "# Keep this shim because deployed plugins still import it.\n"
            "def old_auth():\n    return None\n"
        ),
        "tests/test_service.py": "def test_service():\n    assert True\n",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


@pytest.fixture
def reference_repo(tmp_path, monkeypatch, setup_mcp):
    import repowise.server.mcp_server as mcp_mod

    _write_numbered_source(tmp_path)
    (tmp_path / ".repowise").mkdir()
    for args in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "matrix@example.test"],
        ["git", "config", "user.name", "Matrix Fixture"],
        ["git", "add", "."],
        ["git", "commit", "-qm", "sealed fixture"],
    ):
        subprocess.run(args, cwd=tmp_path, check=True, capture_output=True)
    with (tmp_path / "src/auth/service.py").open("a", encoding="utf-8") as handle:
        handle.write("# committed matrix change\n")
    for args in (
        ["git", "add", "src/auth/service.py"],
        ["git", "commit", "-qm", "change fixture"],
    ):
        subprocess.run(args, cwd=tmp_path, check=True, capture_output=True)
    monkeypatch.setattr(mcp_mod, "_repo_path", str(tmp_path))
    return tmp_path


Status = Literal["PASS", "N/A"]


@dataclass(frozen=True)
class Ref:
    emitter: str
    kind: str
    value: str
    location: tuple[str, ...]
    expected_path: str | None = None
    expected_name: str | None = None
    expected_object: dict[str, Any] | None = None
    expected_contains: tuple[str, ...] = ()
    entity_family: str | None = None


@dataclass(frozen=True)
class Cell:
    emitter: str
    kind: str
    target: str
    status: Status


_PATH_FIELDS = {
    "affected_files",
    "candidate",
    "candidates",
    "citations",
    "entry_points",
    "file",
    "file_path",
    "files",
    "path",
    "source_file",
    "successor_paths",
    "target_path",
    "targets",
    "test_file",
}
_SYMBOL_FIELDS = {
    "callee",
    "callee_id",
    "caller",
    "caller_id",
    "node_id",
    "source_node_id",
    "symbol_id",
    "target_node_id",
}
_DECISION_COLLECTIONS = {
    "decisions",
    "lineage",
    "recent_reversals",
    "top_active",
}
_DECISION_OBJECT_FIELDS = {"newer", "older", "superseded_by"}
_REFERENCE_KINDS = (
    "file",
    "symbol",
    "repository",
    "decision/evidence",
    "omission/continuation",
    "finding",
    "refactoring plan",
)
_EXPECTED_KINDS = {
    "get_answer": {"file"},
    "get_change_risk": set(),
    "get_context": {
        "file",
        "symbol",
        "decision/evidence",
        "omission/continuation",
    },
    "get_dead_code": {"file", "finding"},
    "get_health": {"file", "symbol", "finding", "refactoring plan"},
    "get_overview": {"file"},
    "get_risk": {"file"},
    "get_symbol": {"file", "omission/continuation"},
    "get_why": {"file", "decision/evidence"},
    "search_codebase": {"file", "symbol"},
}

_TARGETS_BY_KIND = {
    "file": ("get_context",),
    "symbol": ("get_context", "get_symbol"),
    "repository": (
        "get_answer",
        "get_change_risk",
        "get_context",
        "get_dead_code",
        "get_health",
        "get_overview",
        "get_risk",
        "get_symbol",
        "get_why",
        "search_codebase",
    ),
    "decision/evidence": ("get_why",),
    "omission/continuation": ("get_symbol",),
    "finding": ("get_dead_code", "get_health"),
    "refactoring plan": ("get_health",),
}


def _looks_like_path(value: str) -> bool:
    if not value or value.startswith(("http://", "https://", "repowise#")):
        return False
    if " -> " in value or "\n" in value:
        return False
    return "/" in value or "\\" in value or "." in value.rsplit("/", 1)[-1]


def _append_source_ref(
    refs: list[Ref], emitter: str, value: str, location: tuple[str, ...], row: dict | None
) -> None:
    if re.search(r":\d+-\d+$", value):
        path = value.rsplit(":", 1)[0].replace("\\", "/")
        refs.append(
            Ref(
                emitter,
                "omission/continuation",
                value.replace("\\", "/"),
                location,
                expected_path=path,
                entity_family="continuation",
            )
        )
    elif "::" in value:
        path, symbol = value.split("::", 1)
        refs.append(
            Ref(
                emitter,
                "symbol",
                value.replace("\\", "/"),
                location,
                expected_path=path.replace("\\", "/"),
                expected_name=(row or {}).get("name") or symbol.rsplit("::", 1)[-1],
            )
        )
    elif _looks_like_path(value):
        refs.append(
            Ref(
                emitter,
                "file",
                value.replace("\\", "/"),
                location,
                expected_path=value.replace("\\", "/"),
            )
        )


def _without_projection_counts(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_projection_counts(item)
            for key, item in value.items()
            if not key.endswith(("_emitted", "_total", "_truncated", "_omitted"))
        }
    if isinstance(value, list):
        return [_without_projection_counts(item) for item in value]
    return value


def _extract_contract_references(
    emitter: str,
    value: Any,
    *,
    location: tuple[str, ...] = (),
    field: str = "",
) -> list[Ref]:
    """Extract public pointers without collapsing duplicate emission sites."""

    refs: list[Ref] = []
    if isinstance(value, dict):
        if field == "evidence_refs" and isinstance(value.get("id"), str):
            refs.append(
                Ref(
                    emitter,
                    "decision/evidence",
                    value["id"],
                    location,
                    expected_object=value,
                    entity_family="evidence",
                )
            )
        identifier = value.get("id")
        if isinstance(identifier, str):
            if field in _DECISION_COLLECTIONS or field in _DECISION_OBJECT_FIELDS:
                refs.append(
                    Ref(
                        emitter,
                        "decision/evidence",
                        identifier,
                        (*location, "id"),
                        entity_family="decision",
                    )
                )
            elif identifier.startswith("finding_"):
                refs.append(
                    Ref(
                        emitter,
                        "finding",
                        identifier,
                        (*location, "id"),
                        expected_object=value,
                        entity_family="dead_code" if emitter == "get_dead_code" else "health",
                    )
                )
            elif identifier.startswith(("plan_", "refac")):
                refs.append(
                    Ref(
                        emitter,
                        "refactoring plan",
                        identifier,
                        (*location, "id"),
                        expected_object=value,
                    )
                )
        for key, item in value.items():
            child_location = (*location, str(key))
            if key == "continuation" and isinstance(item, str):
                refs.append(
                    Ref(
                        emitter,
                        "omission/continuation",
                        item,
                        child_location,
                        entity_family=(
                            "omission" if item.startswith("repowise#") else "continuation"
                        ),
                    )
                )
            elif key == "refs" and field == "omitted" and isinstance(item, list):
                refs.extend(
                    Ref(
                        emitter,
                        "omission/continuation",
                        ref,
                        (*child_location, str(index)),
                        expected_contains=("call_062",)
                        if emitter == "get_context"
                        else (),
                        entity_family="omission",
                    )
                    for index, ref in enumerate(item)
                    if isinstance(ref, str)
                )
            elif key in (_SYMBOL_FIELDS | _PATH_FIELDS) and isinstance(item, str):
                _append_source_ref(refs, emitter, item, child_location, value)
            elif key == "targets" and isinstance(item, dict):
                for target in item:
                    if isinstance(target, str):
                        _append_source_ref(
                            refs,
                            emitter,
                            target,
                            (*child_location, target),
                            item.get(target) if isinstance(item.get(target), dict) else None,
                        )
            refs.extend(
                _extract_contract_references(
                    emitter,
                    item,
                    location=child_location,
                    field=key,
                )
            )
    elif isinstance(value, list):
        for index, item in enumerate(value):
            child_location = (*location, str(index))
            if isinstance(item, str) and field in _PATH_FIELDS:
                _append_source_ref(refs, emitter, item, child_location, None)
            refs.extend(
                _extract_contract_references(
                    emitter,
                    item,
                    location=child_location,
                    field=field,
                )
            )
    return refs


async def _seed_context_omission(session, repository_id: str, root) -> str:
    target = "src/auth/service.py::login"
    now = datetime(2026, 8, 26, tzinfo=UTC)
    session.add(
        GraphNode(
            id="matrix-login-node",
            repository_id=repository_id,
            node_id=target,
            node_type="symbol",
            name="login",
            file_path="src/auth/service.py",
            kind="method",
            start_line=20,
            end_line=41,
            created_at=now,
        )
    )
    session.add_all(
        [
            WikiSymbol(
                id="matrix-load-users",
                repository_id=repository_id,
                file_path="src/auth/service.py",
                symbol_id="src/auth/service.py::load_users",
                name="load_users",
                qualified_name="load_users",
                kind="function",
                signature="def load_users()",
                start_line=100,
                end_line=101,
                language="python",
                created_at=now,
                updated_at=now,
            ),
            WikiSymbol(
                id="matrix-fetch-one",
                repository_id=repository_id,
                file_path="src/db/models.py",
                symbol_id="src/db/models.py::fetch_one",
                name="fetch_one",
                qualified_name="fetch_one",
                kind="function",
                signature="def fetch_one()",
                start_line=31,
                end_line=32,
                language="python",
                created_at=now,
                updated_at=now,
            ),
        ]
    )
    for index in range(63):
        caller = f"src/generated/caller_{index:03d}.py::call_{index:03d}"
        file_path, name = caller.split("::", 1)
        source_path = root / file_path
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(f"def {name}():\n    return {index}\n", encoding="utf-8")
        session.add_all(
            [
                WikiSymbol(
                    id=f"matrix-symbol-caller-{index}",
                    repository_id=repository_id,
                    file_path=file_path,
                    symbol_id=caller,
                    name=name,
                    qualified_name=name,
                    kind="function",
                    signature=f"def {name}()",
                    start_line=1,
                    end_line=2,
                    language="python",
                    created_at=now,
                    updated_at=now,
                ),
                GraphNode(
                    id=f"matrix-caller-file-{index}",
                    repository_id=repository_id,
                    node_id=file_path,
                    node_type="file",
                    name=f"caller_{index:03d}.py",
                    file_path=file_path,
                    language="python",
                    symbol_count=1,
                    created_at=now,
                ),
                GraphNode(
                    id=f"matrix-caller-{index}",
                    repository_id=repository_id,
                    node_id=caller,
                    node_type="symbol",
                    name=name,
                    file_path=file_path,
                    kind="function",
                    start_line=1,
                    end_line=2,
                    created_at=now,
                ),
                GraphEdge(
                    id=f"matrix-in-{index}",
                    repository_id=repository_id,
                    source_node_id=caller,
                    target_node_id=target,
                    edge_type="calls",
                    confidence=0.99,
                    created_at=now,
                ),
            ]
        )
    await session.commit()
    return target


def _cell_status(emitter: str, kind: str, target: str) -> Status:
    if kind not in _EXPECTED_KINDS[emitter]:
        return "N/A"
    if kind == "finding":
        owner = "get_dead_code" if emitter == "get_dead_code" else "get_health"
        return "PASS" if target == owner else "N/A"
    return "PASS"


_CELL_LEDGER = tuple(
    Cell(emitter, kind, target, _cell_status(emitter, kind, target))
    for emitter in _EXPECTED_KINDS
    for kind in _REFERENCE_KINDS
    for target in _TARGETS_BY_KIND[kind]
)


@pytest.mark.asyncio
async def test_canonical_emitter_reference_inventory(reference_repo, health_data, session) -> None:
    from repowise.server.mcp_server import (
        get_answer,
        get_change_risk,
        get_context,
        get_dead_code,
        get_health,
        get_overview,
        get_risk,
        get_symbol,
        get_why,
        search_codebase,
        tool_middleware,
    )

    await _seed_plan(session, health_data)
    context_target = await _seed_context_omission(session, health_data, reference_repo)
    calls = {
        "get_answer": (get_answer, ("where is login defined",), {}),
        "get_change_risk": (get_change_risk, ("HEAD",), {"baseline": 0}),
        "get_context": (get_context, (["src/auth/service.py"],), {"include": ["decisions"]}),
        "get_dead_code": (get_dead_code, (), {"min_confidence": "low"}),
        # Every block this emitter can produce, so the inventory sees every
        # reference kind it can mint. The only fixture finding whose function
        # resolves to a symbol is the performance one.
        "get_health": (
            get_health,
            (),
            {"include": ["biomarkers", "refactoring", "performance"]},
        ),
        "get_overview": (get_overview, (), {"include": ["decisions"]}),
        "get_risk": (get_risk, (["src/auth/service.py"],), {}),
        "get_symbol": (get_symbol, ("src/auth/service.py:1-750",), {}),
        "get_why": (get_why, ("why is JWT used for authentication",), {}),
        "search_codebase": (search_codebase, ("login",), {"mode": "symbol", "limit": 5}),
    }
    responses: dict[str, list[dict[str, Any]]] = {
        name: [await tool_middleware(tool)(*args, **kwargs)]
        for name, (tool, args, kwargs) in calls.items()
    }
    responses["get_context"].append(
        await tool_middleware(get_context)(
            [context_target], include=["callers", "callees"]
        )
    )
    # The plan list is an opt-in projection now: ``include=["refactoring"]``
    # leads with composed opportunities. The plan reference is still emitted by
    # get_health, so the inventory asks the call that carries it.
    responses["get_health"].append(
        await tool_middleware(get_health)(
            include=["refactoring"], only=["refactoring_plans"]
        )
    )
    inventory = {
        emitter: [
            ref
            for response_index, response in enumerate(emitter_responses)
            for ref in _extract_contract_references(
                emitter,
                response,
                location=(f"response[{response_index}]",),
            )
        ]
        for emitter, emitter_responses in responses.items()
    }

    assert len(_CELL_LEDGER) == sum(
        len(_TARGETS_BY_KIND[kind])
        for _emitter in _EXPECTED_KINDS
        for kind in _REFERENCE_KINDS
    )
    for cell in _CELL_LEDGER:
        refs = [ref for ref in inventory[cell.emitter] if ref.kind == cell.kind]
        if cell.kind == "finding":
            family = "dead_code" if cell.target == "get_dead_code" else "health"
            refs = [ref for ref in refs if ref.entity_family == family]
        if cell.status == "PASS":
            assert refs, cell
        else:
            assert not refs, cell

    for emitter, refs in inventory.items():
        assert {ref.kind for ref in refs} == _EXPECTED_KINDS[emitter]
        for ref in refs:
            targets = _TARGETS_BY_KIND[ref.kind]
            if ref.kind == "finding":
                targets = (
                    "get_dead_code" if ref.entity_family == "dead_code" else "get_health",
                )
            for target in targets:
                if target == "get_context":
                    resolved = await tool_middleware(get_context)([ref.value])
                    card = resolved["targets"][ref.value]
                    assert card.get("error") is None, (ref, card)
                    assert card.get("target") == ref.value
                    if ref.kind == "symbol":
                        _assert_symbol_card(
                            resolved,
                            ref.value,
                            ref.expected_path or "",
                            ref.expected_name or "",
                        )
                        assert "resolved_to" not in card
                    else:
                        expected_type = (
                            "file"
                            if "." in (ref.expected_path or "").rsplit("/", 1)[-1]
                            else "module"
                        )
                        assert card["type"] == expected_type, (ref, card)
                        assert "resolved_to" not in card
                        if expected_type == "file":
                            assert card["path"] == ref.expected_path
                        assert card.get("docs") or card.get("summary") or card.get("files")
                elif target == "get_symbol":
                    resolved = await tool_middleware(get_symbol)(ref.value)
                    if ref.kind == "symbol":
                        candidate = resolved
                        if "candidates" in resolved:
                            candidate = next(
                                row
                                for row in resolved["candidates"]
                                if row.get("symbol_id") == ref.value
                            )
                        assert candidate["verified"] is True
                        assert candidate["symbol_id"] == ref.value
                        assert candidate["file"] == ref.expected_path
                        assert candidate["source"]
                    elif ref.entity_family == "omission":
                        assert resolved["kind"] == "omission"
                        assert resolved["content"]
                        if ref.expected_contains:
                            assert any(
                                marker in resolved["content"]
                                for marker in ref.expected_contains
                            )
                    else:
                        path, bounds = ref.value.rsplit(":", 1)
                        start_text, end_text = bounds.split("-", 1)
                        start, end = int(start_text), int(end_text)
                        assert resolved["kind"] == "range"
                        assert resolved["file"] == path
                        assert resolved["start_line"] == start
                        assert resolved["end_line"] == min(end, start + 199)
                        assert resolved["verified"] is True
                        assert resolved["source"]
                elif target == "get_why":
                    resolved = await tool_middleware(get_why)(
                        **(
                            {"reference": ref.expected_object}
                            if ref.entity_family == "evidence"
                            else {"id": ref.value}
                        )
                    )
                    assert resolved["resolved"] is True
                    if ref.entity_family == "evidence":
                        assert ref.expected_object in resolved["evidence_refs"]
                    else:
                        decision = next(
                            row for row in resolved["decisions"] if row["id"] == ref.value
                        )
                        assert decision["id"] == ref.value
                        assert decision.get("title") or decision.get("decision")
                elif target == "get_dead_code":
                    resolved = await tool_middleware(get_dead_code)(
                        min_confidence="low", finding_id=ref.value
                    )
                    assert resolved["resolved"] is True
                    assert resolved["finding"] == ref.expected_object
                elif target == "get_health" and ref.kind == "finding":
                    resolved = await tool_middleware(get_health)(finding_id=ref.value)
                    assert resolved["resolved"] is True
                    assert _without_projection_counts(
                        resolved["finding"]
                    ) == _without_projection_counts(ref.expected_object)
                elif target == "get_health":
                    resolved = await tool_middleware(get_health)(plan_id=ref.value)
                    assert resolved["resolved"] is True
                    assert resolved["plan"]["id"] == ref.value
                    assert resolved["plan"]["file_path"] == ref.expected_object["file_path"]


async def _seed_plan(session, repository_id: str) -> None:
    from repowise.core.persistence import crud

    await crud.save_refactoring_suggestions(
        session,
        repository_id,
        [
            {
                "refactoring_type": "extract_method",
                "file_path": "src/auth/service.py",
                "target_symbol": "login",
                "line_start": 20,
                "line_end": 40,
                "plan": {"groups": []},
                "evidence": {},
                "impact_delta": 1.0,
                "effort_bucket": "S",
                "blast_radius": {"dependents_count": 0},
                "confidence": "high",
                "source_biomarker": "complex_method",
            }
        ],
    )
    await session.commit()


def _assert_symbol_card(result: dict, symbol_id: str, path: str, name: str) -> None:
    card = result["targets"][symbol_id]
    assert card["target"] == symbol_id
    assert card["type"] == "symbol"
    assert card["docs"]["file_path"] == path
    assert card["docs"]["name"] == name
    assert "resolved_to" not in card


@pytest.mark.asyncio
async def test_get_symbol_consumes_symbol_range_and_omission_references_verbatim(
    reference_repo,
) -> None:
    from repowise.server.mcp_server import get_context, get_symbol, search_codebase
    from repowise.server.mcp_server._budget import OmissionCollector

    search = await search_codebase("login", mode="symbol", limit=5)
    symbol_id = next(row["symbol_id"] for row in search["results"] if row["name"] == "login")

    symbol = await get_symbol(symbol_id)
    assert symbol["verified"] is True
    assert symbol["file"] == "src/auth/service.py"
    assert symbol["source"]

    context = await get_context([symbol_id])
    _assert_symbol_card(context, symbol_id, "src/auth/service.py", "login")

    range_id = "src/auth/service.py:20-25"
    source_range = await get_symbol(range_id)
    assert source_range["kind"] == "range"
    assert source_range["file"] == "src/auth/service.py"
    assert source_range["start_line"] == 20
    assert source_range["end_line"] == 25
    assert source_range["verified"] is True
    assert source_range["source"]

    truncated = await get_symbol("src/auth/service.py:1-750")
    continuation = truncated["continuation_reference"]
    continued = await get_symbol(reference=continuation)
    assert continued["symbol_id"] == continuation["id"]
    assert continued["file"] == continuation["path"]
    assert continued["start_line"] == continuation["range"][0]
    assert continued["source"]

    collector = OmissionCollector("matrix", repo_root=reference_repo)
    collector.add("sealed", {"symbol_id": symbol_id, "evidence": "recover me"})
    emitted: dict = {}
    collector.attach(emitted)
    [omission_id] = emitted["_meta"]["omitted"]["refs"]
    assert omission_id.startswith("repowise#")
    recovered = await get_symbol(omission_id)
    assert recovered["kind"] == "omission"
    assert "recover me" in recovered["content"]


@pytest.mark.asyncio
async def test_context_file_card_fallback_is_a_firing_negative_control(
    reference_repo,
) -> None:
    from repowise.server.mcp_server import get_context

    missing_id = "src/auth/service.py::NoSuchMethod"
    result = await get_context([missing_id])
    assert result["targets"][missing_id].get("error") is None
    with pytest.raises(AssertionError):
        _assert_symbol_card(result, missing_id, "src/auth/service.py", "NoSuchMethod")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "src/auth/middleware.py",
        "src/auth/service.py",
        "src/db/models.py",
        "src/legacy/old_auth.py",
        "src/auth",
        "src/db",
    ],
)
async def test_emitted_file_paths_resolve_to_content(reference_repo, path: str) -> None:
    from repowise.server.mcp_server import get_context

    result = await get_context([path])
    card = result["targets"][path]
    expected_type = "file" if "." in path.rsplit("/", 1)[-1] else "module"
    assert card.get("error") is None
    assert card["target"] == path
    assert card["type"] == expected_type, (path, card)
    assert "resolved_to" not in card
    assert card.get("docs") or card.get("summary") or card.get("files")


@pytest.mark.asyncio
async def test_decision_and_evidence_ids_round_trip_through_get_why(
    reference_repo,
) -> None:
    from repowise.server.mcp_server import get_why

    emitted = await get_why("why is JWT used for authentication")
    decision = next(row for row in emitted["decisions"] if row["id"] == "dec1")
    [evidence] = decision["evidence_refs"]

    decision_result = await get_why(id=decision["id"])
    assert decision_result["resolved"] is True
    assert decision_result["decisions"][0]["id"] == decision["id"]

    evidence_result = await get_why(reference=evidence)
    assert evidence_result["resolved"] is True
    assert evidence_result["evidence_refs"] == [evidence]

    archaeology = await get_why("src/legacy/old_auth.py")
    rationale = archaeology["code_rationale"][0]
    [rationale_ref] = rationale["evidence_refs"]
    from repowise.server.mcp_server import _why_evidence

    with _why_evidence._reference_cache_lock:
        _why_evidence._reference_cache.clear()
    rationale_result = await get_why(reference=rationale_ref)
    assert rationale_result["resolved"] is True
    assert rationale_result["evidence_refs"] == [rationale_ref]


@pytest.mark.asyncio
async def test_finding_and_plan_ids_are_stable_and_resolve_in_one_call(
    reference_repo,
    health_data,
    session,
) -> None:
    from repowise.server.mcp_server import get_dead_code, get_health
    from repowise.server.mcp_server.tool_dead_code import _dead_code_finding_id
    from repowise.server.mcp_server.tool_health import (
        _health_finding_id,
        _refactoring_plan_id,
    )

    await _seed_plan(session, health_data)

    health = await get_health(
        include=["biomarkers", "refactoring"],
        only=["top_findings", "refactoring_plans"],
        limit=10,
    )
    health_finding = health["top_findings"][0]
    health_lookup = await get_health(finding_id=health_finding["id"])
    assert health_lookup["resolved"] is True
    assert health_lookup["finding"] == health_finding

    [plan] = health["refactoring_plans"]
    plan_lookup = await get_health(plan_id=plan["id"])
    assert plan_lookup["resolved"] is True
    assert plan_lookup["plan"]["id"] == plan["id"]

    dead = await get_dead_code(min_confidence="low")
    dead_finding = next(
        row
        for tier in dead["tiers"].values()
        for row in tier["findings"]
    )
    dead_lookup = await get_dead_code(
        min_confidence="low", finding_id=dead_finding["id"]
    )
    assert dead_lookup["resolved"] is True
    assert dead_lookup["finding"] == dead_finding

    # Storage UUID replacement cannot move any public identifier.
    health_row = (await session.execute(select(HealthFinding))).scalars().first()
    dead_row = (await session.execute(select(DeadCodeFinding))).scalars().first()
    plan_row = (await session.execute(select(RefactoringSuggestion))).scalars().first()
    for row, public_id, factory in (
        (health_row, health_finding["id"], _health_finding_id),
        (dead_row, dead_finding["id"], _dead_code_finding_id),
        (plan_row, plan["id"], _refactoring_plan_id),
    ):
        replacement = copy(row)
        replacement.id = "different-storage-uuid"
        assert factory(replacement, "default") == public_id
