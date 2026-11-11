"""The interim ``repowise check`` evidence, read from an index and from git."""

from __future__ import annotations

from sqlalchemy import select

from repowise.core.analysis.changed_lines import change_set
from repowise.core.persistence.crud import bulk_upsert_decisions
from repowise.core.persistence.crud.authority import accept_decision
from repowise.core.persistence.models import DecisionRecord, GitMetadata, GraphNode
from repowise.core.policy import evaluate, parse_policy
from repowise.core.policy.interim_evidence import gather_evidence
from tests.unit.analysis.test_security_gate_extras import _git, _write, init_repo
from tests.unit.persistence.helpers import insert_repo
from tests.unit.persistence.test_decision_authority import _dict

CATALOG = """\
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: payments
  annotations:
    repowise.dev/paths: src/pay/**
    repowise.dev/tags: pci
spec: {type: service, lifecycle: production, owner: group:default/payments-team}
"""
FILES = [f"src/pay/m{i}.py" for i in range(6)]


def _repo(tmp_path):
    root = init_repo(tmp_path)
    (root / ".repowise").mkdir()
    (root / "src" / "pay").mkdir(parents=True)
    _write(root, ".repowise/components.yaml", CATALOG)
    _write(root, "src/pay/m0.py", "x = 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "pay\n\nCo-authored-by: Claude <noreply@anthropic.com>")
    return root


async def _seed(session, repo_id: str) -> None:
    session.add_all(GraphNode(repository_id=repo_id, node_id=f, node_type="file") for f in FILES)
    session.add_all(
        GitMetadata(
            repository_id=repo_id,
            file_path=f,
            primary_owner_name="Ada",
            primary_owner_email="ada@example.com",
        )
        for f in FILES
    )
    await bulk_upsert_decisions(
        session, repo_id, [_dict("Use idempotent charges", affected_files=["src/pay/m0.py"])]
    )
    record = (
        await session.execute(select(DecisionRecord).where(DecisionRecord.repository_id == repo_id))
    ).scalar_one()
    await accept_decision(session, record, accepter="tester")
    await session.flush()


async def test_index_adds_bus_factor_tests_and_decisions(async_session, tmp_path):
    root = _repo(tmp_path)
    repo = await insert_repo(async_session, local_path=str(root))
    await _seed(async_session, repo.id)

    change = change_set(str(root), "main...HEAD")
    evidence = await gather_evidence(root, change, session=async_session, repo_id=repo.id)
    items = {i["kind"]: i for i in evidence["items"]}

    (payments,) = items["components_touched"]["details"]
    assert payments["name"] == "payments" and payments["tags"] == ["pci"]
    assert payments["owner"] == "payments-team" and payments["bus_factor"] == 1
    assert payments["files"] == ["src/pay/m0.py"]
    assert items["provenance"]["value"]["label"] == "agent"
    assert items["provenance"]["details"][0]["tier"] == 3
    (decision,) = items["decisions_crossed"]["details"]
    assert decision["title"] == "Use idempotent charges" and decision["paths"] == ["src/pay/m0.py"]
    # An empty coverage map and no test found: not a measured absence.
    assert items["reaching_tests"]["status"] == "unknown"
    assert items["contracts"]["status"] == "unknown"

    policy = parse_policy(
        """\
rules:
  - name: pci-agent
    match: {components: {tags: [pci]}}
    when: {attribution: agent, agent_tier: [3], decision_crossed: true, bus_factor_below: 2}
    action: require_owner_review
  - name: pci-untested
    match: {components: {tags: [pci]}}
    when: {reaching_tests: missing}
    action: fail
"""
    )
    fired, untested = evaluate(policy, evidence)
    assert fired.outcome == "triggered", fired.message
    assert fired.owners == ["payments-team"]
    assert untested.outcome == "unknown"


async def test_a_measured_map_makes_an_untested_file_a_known_absence(
    async_session, tmp_path, monkeypatch
):
    from repowise.core.analysis import test_collection

    async def measured(*args, **kwargs):
        out = test_collection.empty_result(1)
        out.update(map_empty=False, map_current=True, unknown=["src/pay/m0.py"])
        return out

    monkeypatch.setattr(test_collection, "collect", measured)
    root = _repo(tmp_path)
    repo = await insert_repo(async_session, local_path=str(root))
    change = change_set(str(root), "main...HEAD")
    evidence = await gather_evidence(root, change, session=async_session, repo_id=repo.id)
    (item,) = [i for i in evidence["items"] if i["kind"] == "reaching_tests"]
    assert item["status"] == "ok" and item["basis"] == "measured"
    assert item["details"] == [{"path": "src/pay/m0.py", "tests": [], "basis": "measured"}]


async def test_git_alone_reads_declared_components_and_provenance(tmp_path):
    root = _repo(tmp_path)
    evidence = await gather_evidence(root, change_set(str(root), "main...HEAD"))
    items = {i["kind"]: i for i in evidence["items"]}
    assert items["components_touched"]["basis"] == "declared"
    assert items["components_touched"]["details"][0]["bus_factor"] is None
    assert items["provenance"]["status"] == "ok"
    for kind in ("reaching_tests", "decisions_crossed", "contracts"):
        assert items[kind]["status"] == "unknown" and items[kind]["basis"] == "unresolved"
    assert evidence["schema_version"] == 1 and evidence["revspec"] == "main...HEAD"


async def test_attribution_off_leaves_provenance_unknown(tmp_path):
    root = _repo(tmp_path)
    _write(root, ".repowise/config.yaml", "attribution:\n  enabled: false\n")
    evidence = await gather_evidence(root, change_set(str(root), "main...HEAD"))
    (prov,) = [i for i in evidence["items"] if i["kind"] == "provenance"]
    assert prov["status"] == "unknown" and prov["value"] is None
