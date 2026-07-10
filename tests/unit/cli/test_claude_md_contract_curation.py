"""Workspace CLAUDE.md contract-table curation.

Raw contract links include intra-repo rows and one row per provider file for
the same (contract, consumer); in a real workspace that rendered 30+
near-duplicate rows before the first genuinely cross-repo contract.
"""

from __future__ import annotations

from repowise.cli.commands.claude_md_cmd import _MAX_CONTRACT_LINKS, _curate_contract_links


def _link(
    provider_repo,
    consumer_repo,
    contract_id,
    provider_file="src/models.py",
    consumer_file="src/client.ts",
):
    return {
        "provider_repo": provider_repo,
        "consumer_repo": consumer_repo,
        "provider_file": provider_file,
        "consumer_file": consumer_file,
        "contract_id": contract_id,
        "contract_type": "data",
    }


def test_intra_repo_links_are_dropped():
    links = [
        _link("api", "api", "data::repositories"),
        _link("api", "web", "http::GET::/repos"),
    ]
    out = _curate_contract_links(links)
    assert len(out) == 1
    assert out[0]["consumer_repo"] == "web"


def test_duplicate_providers_collapse_preferring_non_migration():
    links = [
        _link("api", "web", "data::users", provider_file="alembic/versions/0001_init.py"),
        _link("api", "web", "data::users", provider_file="src/models.py"),
    ]
    out = _curate_contract_links(links)
    assert len(out) == 1
    assert out[0]["provider_file"] == "src/models.py"


def test_distinct_consumers_survive():
    links = [
        _link("api", "web", "data::users", consumer_file="a.ts"),
        _link("api", "web", "data::users", consumer_file="b.ts"),
    ]
    assert len(_curate_contract_links(links)) == 2


def test_capped():
    links = [_link("api", "web", f"http::GET::/x{i}") for i in range(40)]
    assert len(_curate_contract_links(links)) == _MAX_CONTRACT_LINKS


def test_malformed_entries_ignored():
    assert _curate_contract_links(["nope", None]) == []
