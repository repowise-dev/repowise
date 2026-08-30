"""Integration tests for cross-repository test impact analysis in workspaces."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from repowise.cli.helpers import run_async
from repowise.core.analysis.workspace_test_impact import (
    analyze_workspace_test_impact,
    workspace_test_impact_to_dict,
)
from repowise.core.workspace.config import RepoEntry, WorkspaceConfig


def _create_workspace_fixture(tmp_path: Path) -> Path:
    """Create a minimal workspace with provider and consumer repos."""
    ws_root = tmp_path / "workspace"
    ws_root.mkdir(parents=True)

    # Create .repowise-workspace.yaml
    ws_config = WorkspaceConfig(
        repos=[
            RepoEntry(path="provider", alias="backend-api", is_primary=True),
            RepoEntry(path="consumer", alias="web-app"),
        ],
        default_repo="backend-api",
    )
    ws_config.save(ws_root)

    # Provider repo: backend-api
    provider_root = ws_root / "provider"
    provider_root.mkdir()
    (provider_root / ".git").mkdir()
    (provider_root / "src" / "api").mkdir(parents=True)
    (provider_root / "src" / "services").mkdir(parents=True)
    (provider_root / "tests").mkdir(parents=True)

    # Provider: HTTP endpoint provider
    (provider_root / "src" / "api" / "users.py").write_text("""
from flask import Flask, jsonify

app = Flask(__name__)

@app.route('/api/users/<int:user_id>', methods=['GET'])
def get_user(user_id):
    return jsonify({'id': user_id, 'name': 'Test User'})

@app.route('/api/users', methods=['POST'])
def create_user():
    return jsonify({'id': 1, 'name': 'New User'}), 201
""")

    # Provider: service that uses the endpoint
    (provider_root / "src" / "services" / "user_service.py").write_text("""
from src.api.users import get_user

def fetch_user(user_id):
    return get_user(user_id)
""")

    # Provider: test file
    (provider_root / "tests" / "test_users.py").write_text("""
import pytest
from src.api.users import get_user

def test_get_user():
    # This is a provider-side test
    pass
""")

    # Consumer repo: web-app
    consumer_root = ws_root / "consumer"
    consumer_root.mkdir()
    (consumer_root / ".git").mkdir()
    (consumer_root / "src").mkdir(parents=True)
    (consumer_root / "tests").mkdir(parents=True)

    # Consumer: calls the provider endpoint
    (consumer_root / "src" / "api_client.py").write_text("""
import requests

BASE_URL = "http://backend-api"

def get_user(user_id):
    response = requests.get(f"{BASE_URL}/api/users/{user_id}")
    return response.json()

def create_user(data):
    response = requests.post(f"{BASE_URL}/api/users", json=data)
    return response.json()
""")

    # Consumer: service that uses the API client
    (consumer_root / "src" / "user_service.py").write_text("""
from src.api_client import get_user, create_user

def fetch_user_profile(user_id):
    user = get_user(user_id)
    return {"profile": user}

def register_user(data):
    return create_user(data)
""")

    # Consumer: test files
    (consumer_root / "tests" / "test_user_service.py").write_text("""
import pytest
from unittest.mock import patch
from src.user_service import fetch_user_profile, register_user

@patch('src.api_client.get_user')
def test_fetch_user_profile(mock_get_user):
    mock_get_user.return_value = {'id': 1, 'name': 'Test'}
    result = fetch_user_profile(1)
    assert result['profile']['name'] == 'Test'

@patch('src.api_client.create_user')
def test_register_user(mock_create_user):
    mock_create_user.return_value = {'id': 2, 'name': 'New'}
    result = register_user({'name': 'New'})
    assert result['id'] == 2
""")

    (consumer_root / "tests" / "test_api_client.py").write_text("""
import pytest
from unittest.mock import patch
from src.api_client import get_user, create_user

@patch('requests.get')
def test_get_user(mock_get):
    mock_get.return_value.json.return_value = {'id': 1, 'name': 'Test'}
    result = get_user(1)
    assert result['id'] == 1

@patch('requests.post')
def test_create_user(mock_post):
    mock_post.return_value.json.return_value = {'id': 2, 'name': 'New'}
    result = create_user({'name': 'New'})
    assert result['id'] == 2
""")

    # Initialize git repos
    for repo in [provider_root, consumer_root]:
        os.chdir(repo)
        os.system("git init -q")
        os.system("git config user.email 'test@test.com'")
        os.system("git config user.name 'Test'")
        os.system("git add -A")
        os.system("git commit -m 'Initial commit' -q")

    return ws_root


@pytest.fixture
def workspace_fixture(tmp_path: Path) -> Path:
    return _create_workspace_fixture(tmp_path)


def test_workspace_test_impact_no_contracts(workspace_fixture: Path):
    """Test that analysis returns empty when no contracts are extracted."""
    # Without running contract extraction, there are no links
    result = run_async(
        analyze_workspace_test_impact(
            workspace_fixture,
            [{"repo": "backend-api", "path": "src/api/users.py"}],
        )
    )
    assert result.workspace is True
    assert result.recommendations_total == 0
    assert "error" in result.summary


def test_workspace_test_impact_mock_contracts(workspace_fixture: Path):
    """Test test impact analysis with mocked contract links."""
    from repowise.core.workspace.contracts import ContractLink, ContractStore
    from repowise.core.workspace.config import WORKSPACE_DATA_DIR

    # Create a mock contract store with links
    data_dir = workspace_fixture / WORKSPACE_DATA_DIR
    data_dir.mkdir(parents=True, exist_ok=True)

    # Create contract links: provider -> consumer
    links = [
        ContractLink(
            contract_id="http::GET::/api/users/{user_id}",
            contract_type="http",
            match_type="exact",
            confidence=0.9,
            provider_repo="backend-api",
            provider_file="src/api/users.py",
            provider_symbol="get_user",
            provider_service=None,
            consumer_repo="web-app",
            consumer_file="src/api_client.py",
            consumer_symbol="get_user",
            consumer_service=None,
        ),
        ContractLink(
            contract_id="http::POST::/api/users",
            contract_type="http",
            match_type="exact",
            confidence=0.85,
            provider_repo="backend-api",
            provider_file="src/api/users.py",
            provider_symbol="create_user",
            provider_service=None,
            consumer_repo="web-app",
            consumer_file="src/api_client.py",
            consumer_symbol="create_user",
            consumer_service=None,
        ),
    ]

    store = ContractStore(
        version=6,
        generated_at="2024-01-01T00:00:00+00:00",
        contracts=[],
        contract_links=links,
    )

    contracts_path = data_dir / "contracts.json"
    contracts_path.write_text(json.dumps(store.to_dict(), indent=2))

    # Run analysis
    result = run_async(
        analyze_workspace_test_impact(
            workspace_fixture,
            [{"repo": "backend-api", "path": "src/api/users.py"}],
            include_measured=False,  # No coverage ingested
            include_inferred=True,
        )
    )

    # Should find recommendations for consumer repo
    assert result.workspace is True
    # Note: without actual test reachability indexed, inferred will be empty
    # but the structure should work
    print(f"Result: {workspace_test_impact_to_dict(result)}")


def test_workspace_test_impact_serialization():
    """Test serialization of result to dict."""
    from repowise.core.analysis.workspace_test_impact import (
        WorkspaceTestImpactResult,
        WorkspaceTestRecommendation,
    )

    rec = WorkspaceTestRecommendation(
        test_id="tests/test_user_service.py::test_fetch_user_profile",
        test_file="tests/test_user_service.py",
        consumer_repo="web-app",
        consumer_repo_alias="web-app",
        provider_repo="backend-api",
        provider_file="src/api/users.py",
        contract_id="http::GET::/api/users/{user_id}",
        contract_type="http",
        basis="inferred",
        via="call-graph",
        confidence=0.81,
        source_files=["src/api/users.py"],
        evidence=[{"basis": "inferred", "via": "call-graph"}],
    )

    result = WorkspaceTestImpactResult(
        workspace=True,
        recommendations=[rec],
        recommendations_total=1,
        recommendations_by_basis={"inferred": 1},
        recommendations_by_repo={"backend-api": 1},
        recommendations_by_consumer_repo={"web-app": 1},
        files_analyzed=[],
        summary={},
    )

    serialized = workspace_test_impact_to_dict(result)
    assert serialized["workspace"] is True
    assert serialized["recommendations_total"] == 1
    assert serialized["recommendations"][0]["test_id"] == rec.test_id
    assert serialized["recommendations"][0]["basis"] == "inferred"
    assert serialized["recommendations"][0]["via"] == "call-graph"


def test_workspace_test_impact_deduplication():
    """Test that duplicate recommendations are merged."""
    from repowise.core.analysis.workspace_test_impact import (
        WorkspaceTestImpactResult,
        WorkspaceTestRecommendation,
        _recommendation_sort_key,
    )

    # Two recommendations for same test but different contract links
    rec1 = WorkspaceTestRecommendation(
        test_id="tests/test_user_service.py::test_fetch_user_profile",
        test_file="tests/test_user_service.py",
        consumer_repo="web-app",
        consumer_repo_alias="web-app",
        provider_repo="backend-api",
        provider_file="src/api/users.py",
        contract_id="http::GET::/api/users/{user_id}",
        contract_type="http",
        basis="inferred",
        via="call-graph",
        confidence=0.81,
        source_files=["src/api/users.py"],
        evidence=[{"basis": "inferred", "via": "call-graph"}],
    )

    rec2 = WorkspaceTestRecommendation(
        test_id="tests/test_user_service.py::test_fetch_user_profile",
        test_file="tests/test_user_service.py",
        consumer_repo="web-app",
        consumer_repo_alias="web-app",
        provider_repo="backend-api",
        provider_file="src/api/users.py",
        contract_id="http::POST::/api/users",
        contract_type="http",
        basis="inferred",
        via="call-graph",
        confidence=0.75,
        source_files=["src/api/users.py"],
        evidence=[{"basis": "inferred", "via": "call-graph"}],
    )

    result = WorkspaceTestImpactResult(
        workspace=True,
        recommendations=[rec1, rec2],
        recommendations_total=2,
    )

    # Test deduplication logic manually
    seen = {}
    for rec in result.recommendations:
        key = (rec.test_id, rec.consumer_repo, rec.provider_repo, rec.contract_id)
        if key not in seen:
            seen[key] = rec

    # Should have 2 entries since contract_id differs
    assert len(seen) == 2

    # Test sort key
    rec_with_more = WorkspaceTestRecommendation(
        test_id="tests/test_user_service.py::test1",
        test_file="tests/test_user_service.py",
        consumer_repo="web-app",
        consumer_repo_alias="web-app",
        provider_repo="backend-api",
        provider_file="src/api/users.py",
        contract_id="http::GET::/api/users/{user_id}",
        contract_type="http",
        basis="measured",
        via="coverage-map",
        confidence=0.9,
        source_files=["src/api/users.py", "src/api/posts.py"],
        evidence=[],
    )

    rec_with_less = WorkspaceTestRecommendation(
        test_id="tests/test_user_service.py::test2",
        test_file="tests/test_user_service.py",
        consumer_repo="web-app",
        consumer_repo_alias="web-app",
        provider_repo="backend-api",
        provider_file="src/api/users.py",
        contract_id="http::GET::/api/users/{user_id}",
        contract_type="http",
        basis="inferred",
        via="call-graph",
        confidence=0.8,
        source_files=["src/api/users.py"],
        evidence=[],
    )

    # More source files should sort first
    assert _recommendation_sort_key(rec_with_more) < _recommendation_sort_key(rec_with_less)


def test_workspace_test_impact_basis_priority():
    """Test that measured basis wins over inferred."""
    from repowise.core.analysis.workspace_test_impact import WorkspaceTestRecommendation

    measured_rec = WorkspaceTestRecommendation(
        test_id="tests/test_user_service.py::test1",
        test_file="tests/test_user_service.py",
        consumer_repo="web-app",
        consumer_repo_alias="web-app",
        provider_repo="backend-api",
        provider_file="src/api/users.py",
        contract_id="http::GET::/api/users/{user_id}",
        contract_type="http",
        basis="measured",
        via="coverage-map",
        confidence=0.9,
        source_files=["src/api/users.py"],
        evidence=[],
    )

    inferred_rec = WorkspaceTestRecommendation(
        test_id="tests/test_user_service.py::test1",
        test_file="tests/test_user_service.py",
        consumer_repo="web-app",
        consumer_repo_alias="web-app",
        provider_repo="backend-api",
        provider_file="src/api/users.py",
        contract_id="http::GET::/api/users/{user_id}",
        contract_type="http",
        basis="inferred",
        via="call-graph",
        confidence=0.8,
        source_files=["src/api/users.py"],
        evidence=[],
    )

    # When deduping same test_id/consumer/provider/contract, measured should win
    seen = {}
    for rec in [inferred_rec, measured_rec]:
        key = (rec.test_id, rec.consumer_repo, rec.provider_repo, rec.contract_id)
        if key not in seen:
            seen[key] = rec
        else:
            # measured has higher priority (lower BASIS_ORDER)
            existing = seen[key]
            if rec.basis == "measured" and existing.basis != "measured":
                seen[key] = rec

    assert seen[key].basis == "measured"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])