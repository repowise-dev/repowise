"""In-repo contract links on get_context cards and in get_change_risk."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from repowise.server.mcp_server import _repo_contracts as rc

ROUTES = """from fastapi import APIRouter

router = APIRouter(prefix="/api")


@router.get("/items/{item_id}")
def get_item(item_id: str):
    return {"id": item_id}


@router.delete("/items/{item_id}")
def delete_item(item_id: str):
    return None
"""

CLIENT = """export async function getItem(id: string) {
  const res = await fetch(`/api/items/${id}`);
  return res.json();
}

export async function deleteItem(id: string) {
  return fetch(`/api/items/${id}`, { method: "DELETE" });
}
"""

ROUTES_FILE = "server/routes.py"
CLIENT_FILE = "web/client.ts"


def _git(repo: Path, *args: str) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@e.x",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@e.x",
    }
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A server and its client as two services, with contracts read and committed."""
    from repowise.core.workspace.repo_contracts import run_repo_contract_extraction

    root = tmp_path / "shop"
    _write(root, ROUTES_FILE, ROUTES)
    _write(root, "server/pyproject.toml", '[project]\nname = "server"\nversion = "0.1.0"\n')
    _write(root, CLIENT_FILE, CLIENT)
    _write(root, "web/package.json", '{"name": "web", "version": "0.1.0"}\n')
    _write(root, ".gitignore", ".repowise/\n")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    result = asyncio.run(run_repo_contract_extraction(root))
    assert result.links == 2
    return root


def _card(repo: Path, *targets: str, include: set[str] | None = None) -> dict:
    response = {"targets": {t: {"target": t} for t in targets}}
    rc.attach_contract_lines(response, repo, include or set())
    return response["targets"]


# -- get_context -----------------------------------------------------------


def test_route_file_says_where_in_the_repo_it_is_called_from(repo: Path) -> None:
    card = _card(repo, ROUTES_FILE)[ROUTES_FILE]
    assert card["in_repo_contracts"] == (
        f"2 routes called from 2 places in this repo: {CLIENT_FILE}:2,7"
    )
    assert "in_repo_callers" not in card


def _bind_symbols(repo: Path) -> None:
    """Symbol ids an indexed repo binds; this fixture repo has no index."""
    store_path = repo / ".repowise" / "contracts.json"
    data = json.loads(store_path.read_text(encoding="utf-8"))
    for link in data["contract_links"]:
        verb = "delete" if "DELETE" in link["contract_id"] else "get"
        link["provider_symbol_id"] = f"{ROUTES_FILE}::{verb}_item"
        link["consumer_symbol_id"] = f"{CLIENT_FILE}::{verb}Item"
    store_path.write_text(json.dumps(data), encoding="utf-8")


def test_handler_and_client_function_targets_count_only_their_own_route(repo: Path) -> None:
    _bind_symbols(repo)
    handler, caller = f"{ROUTES_FILE}::delete_item", f"{CLIENT_FILE}::getItem"
    cards = _card(repo, handler, caller)
    assert cards[handler]["in_repo_contracts"] == (
        f"1 route called from 1 place in this repo: {CLIENT_FILE}:7"
    )
    assert cards[caller]["in_repo_contracts"] == (
        f"calls 1 route served in this repo: {ROUTES_FILE} (GET /api/items/{{param}} L6)"
    )


def test_client_file_names_the_routes_it_calls_and_their_handlers(repo: Path) -> None:
    card = _card(repo, CLIENT_FILE)[CLIENT_FILE]
    assert card["in_repo_contracts"] == (
        f"calls 2 routes served in this repo: {ROUTES_FILE} "
        "(GET /api/items/{param} L6, DELETE /api/items/{param} L11)"
    )


def test_include_lists_the_rows_with_their_total(repo: Path) -> None:
    cards = _card(repo, ROUTES_FILE, CLIENT_FILE, include={"callers", "callees"})
    callers = cards[ROUTES_FILE]["in_repo_callers"]
    assert cards[ROUTES_FILE]["in_repo_callers_total"] == 2
    assert [(r["route"], r["file"], r["line"]) for r in callers] == [
        ("GET /api/items/{param}", CLIENT_FILE, 2),
        ("DELETE /api/items/{param}", CLIENT_FILE, 7),
    ]
    routes = cards[CLIENT_FILE]["in_repo_routes"]
    assert cards[CLIENT_FILE]["in_repo_routes_total"] == 2
    assert {r["file"] for r in routes} == {ROUTES_FILE}
    # Exact rows carry no match_type; the strategy is not repeated per row.
    assert all("match_type" not in r and "strategy" not in r for r in callers + routes)


def test_unlinked_file_and_missing_store_add_nothing(repo: Path, tmp_path: Path) -> None:
    assert _card(repo, "server/pyproject.toml")["server/pyproject.toml"] == {
        "target": "server/pyproject.toml"
    }
    bare = tmp_path / "bare"
    bare.mkdir()
    assert _card(bare, ROUTES_FILE)[ROUTES_FILE] == {"target": ROUTES_FILE}


def test_a_card_with_workspace_contract_rows_is_left_alone(repo: Path) -> None:
    response = {"targets": {ROUTES_FILE: {"cross_repo": {"contracts": {"consumers": [{"x": 1}]}}}}}
    rc.attach_contract_lines(response, repo, set())
    assert "in_repo_contracts" not in response["targets"][ROUTES_FILE]


def _demote(repo: Path, method: str) -> None:
    store_path = repo / ".repowise" / "contracts.json"
    data = json.loads(store_path.read_text(encoding="utf-8"))
    for link in data["contract_links"]:
        if method in link["contract_id"]:
            link["match_type"] = "candidate"
    store_path.write_text(json.dumps(data), encoding="utf-8")


def test_candidate_links_stay_out_of_the_line_and_are_labelled_in_rows(repo: Path) -> None:
    _demote(repo, "DELETE")
    card = _card(repo, ROUTES_FILE)[ROUTES_FILE]
    assert card["in_repo_contracts"] == (
        f"1 route called from 1 place in this repo: {CLIENT_FILE}:2"
    )
    rows = _card(repo, ROUTES_FILE, include={"callers"})[ROUTES_FILE]["in_repo_callers"]
    assert [r.get("match_type") for r in rows] == [None, "candidate"]


def test_only_candidate_links_add_nothing_by_default_and_rows_when_asked(repo: Path) -> None:
    _demote(repo, "::")
    assert _card(repo, ROUTES_FILE, CLIENT_FILE) == {
        ROUTES_FILE: {"target": ROUTES_FILE},
        CLIENT_FILE: {"target": CLIENT_FILE},
    }
    cards = _card(repo, ROUTES_FILE, CLIENT_FILE, include={"callers", "callees"})
    assert "in_repo_contracts" not in cards[ROUTES_FILE]
    assert {r["match_type"] for r in cards[ROUTES_FILE]["in_repo_callers"]} == {"candidate"}
    assert cards[CLIENT_FILE]["in_repo_routes_total"] == 2


def test_files_with_no_contract_role_gain_zero_bytes(repo: Path) -> None:
    others = [
        "server/pyproject.toml",
        "web/package.json",
        ".gitignore",
        "server/missing.py",
        "server",
        f"{ROUTES_FILE}::not_a_handler",
        f"{CLIENT_FILE}::notACaller",
    ]
    for include in (set(), {"callers", "callees"}):
        before = {t: {"target": t, "summary": "x"} for t in others}
        response = {"targets": json.loads(json.dumps(before))}
        rc.attach_contract_lines(response, repo, include)
        assert json.dumps(response["targets"]) == json.dumps(before)


def test_more_sites_than_shown_points_at_the_include(repo: Path) -> None:
    store_path = repo / ".repowise" / "contracts.json"
    data = json.loads(store_path.read_text(encoding="utf-8"))
    get_consumer = next(
        c for c in data["contracts"] if c["role"] == "consumer" and "GET" in c["contract_id"]
    )
    for line in (20, 30, 40):
        data["contracts"].append({**get_consumer, "line": line})
    store_path.write_text(json.dumps(data), encoding="utf-8")
    card = _card(repo, ROUTES_FILE)[ROUTES_FILE]
    assert card["in_repo_contracts"] == (
        f"2 routes called from 5 places in this repo: {CLIENT_FILE}:2,7,20"
        ' and 2 more (include=["callers"] lists them)'
    )


# -- get_change_risk -------------------------------------------------------


def _breaks(repo: Path, revspec: str | None = None) -> list:
    return rc.route_breaks(repo, revspec) or []


def test_changed_path_breaks_its_in_repo_caller(repo: Path) -> None:
    _write(repo, ROUTES_FILE, ROUTES.replace('get("/items/{item_id}")', 'get("/things/{item_id}")'))
    breaks = _breaks(repo)
    assert [(b.contract_id, b.provider_file) for b in breaks] == [
        ("http::GET::/api/items/{param}", ROUTES_FILE)
    ]
    assert [(c["file"], c["line"]) for c in breaks[0].callers] == [(CLIENT_FILE, 2)]
    payload = rc.route_breaks_payload(breaks, detail=False)
    assert payload == {
        "in_repo_contracts": (
            "stops serving 1 route that 1 place in this repo call: GET /api/items/{param} "
            f"({ROUTES_FILE}) from {CLIENT_FILE}:2"
        )
    }
    detailed = rc.route_breaks_payload(breaks, detail=True)
    assert detailed["in_repo_breaks"][0]["callers_total"] == 1


def test_changed_method_breaks_and_a_renamed_param_does_not(repo: Path) -> None:
    _write(
        repo,
        ROUTES_FILE,
        ROUTES.replace("@router.delete(", "@router.post(").replace(
            'get("/items/{item_id}")', 'get("/items/{id}")'
        ),
    )
    assert [b.contract_id for b in _breaks(repo)] == ["http::DELETE::/api/items/{param}"]


def test_a_body_edit_or_an_unlinked_file_breaks_nothing(repo: Path) -> None:
    _write(repo, ROUTES_FILE, ROUTES.replace('return {"id": item_id}', "return {}"))
    _write(repo, "server/other.py", "X = 1\n")
    assert rc.route_breaks(repo, None) == []
    assert rc.route_breaks(repo, None, {"server/other.py"}) is None


def test_a_caller_edited_in_the_same_change_is_not_broken(repo: Path) -> None:
    _write(repo, ROUTES_FILE, ROUTES.replace('get("/items/{item_id}")', 'get("/things/{item_id}")'))
    _write(repo, CLIENT_FILE, CLIENT.replace("/api/items/${id}`);", "/api/things/${id}`);"))
    assert _breaks(repo) == []


def test_a_route_moved_to_another_changed_file_is_still_served(repo: Path) -> None:
    head, _, tail = ROUTES.partition('@router.delete("/items/{item_id}")')
    _write(repo, ROUTES_FILE, head)
    _write(
        repo,
        "server/admin.py",
        ROUTES.split("\n\n\n")[0] + "\n\n\n" + '@router.delete("/items/{item_id}")' + tail,
    )
    assert _breaks(repo) == []


def test_a_deleted_route_file_breaks_every_caller_in_a_commit(repo: Path) -> None:
    (repo / ROUTES_FILE).unlink()
    _git(repo, "commit", "-q", "-am", "drop routes")
    assert rc.may_break_routes(repo, set())
    breaks = rc.route_breaks(repo, "HEAD", set())
    assert sorted(b.contract_id for b in breaks) == [
        "http::DELETE::/api/items/{param}",
        "http::GET::/api/items/{param}",
    ]
    line = rc.route_breaks_payload(breaks, detail=False)["in_repo_contracts"]
    assert line.startswith("stops serving 2 routes that 2 places in this repo call: ")


def test_a_tie_on_the_trailing_path_is_settled_by_line_or_dropped() -> None:
    from repowise.server.mcp_server._enrichment import CrossRepoEnricher

    def link(cid: str) -> dict:
        return {
            "contract_id": cid,
            "contract_type": "http",
            "match_type": "exact",
            "provider_repo": "r",
            "provider_file": "a.py",
            "consumer_repo": "r",
            "consumer_file": "c.ts",
        }

    def provider(cid: str, line: int) -> dict:
        return {
            "repo": "r",
            "contract_id": cid,
            "contract_type": "http",
            "role": "provider",
            "file_path": "a.py",
            "line": line,
        }

    ids = ["http::GET::/v1/items", "http::GET::/v2/items"]
    enricher = CrossRepoEnricher.from_data(
        contracts={
            "contracts": [provider(ids[0], 5), provider(ids[1], 9)],
            "contract_links": [link(i) for i in ids],
        }
    )
    store = rc.RepoContracts(enricher, "r")
    links = [link(i) for i in ids]
    gone = SimpleNamespace(contract_id="http::GET::/items", line=9)
    assert rc._stored_for(store, gone, links)["contract_id"] == ids[1]
    assert (
        rc._stored_for(store, SimpleNamespace(contract_id="http::GET::/items", line=None), links)
        is None
    )
    root = SimpleNamespace(contract_id="http::GET::/", line=None)
    assert rc._stored_for(store, root, links) is None


def test_get_change_risk_attaches_the_line_only_when_a_route_breaks(repo: Path) -> None:
    from repowise.server.mcp_server.tool_change_risk import tool

    result = SimpleNamespace(working_tree=True)
    ctx = SimpleNamespace(path=repo)

    async def run(changed: set[str]) -> object:
        return await tool._start_in_repo_contracts(ctx, None, result, changed)

    assert asyncio.run(run({ROUTES_FILE})) is None
    _write(repo, ROUTES_FILE, ROUTES.replace('get("/items/{item_id}")', 'get("/things/{item_id}")'))
    assert asyncio.run(run(set())) is None
    breaks = asyncio.run(run({ROUTES_FILE}))
    assert [b.contract_id for b in breaks] == ["http::GET::/api/items/{param}"]


def test_deleting_a_short_route_beside_a_kept_linked_one_breaks_nothing(repo: Path) -> None:
    # Mounted elsewhere: read alone, the file's routes carry no /api prefix,
    # so a deleted `/{item_id}` ends the stored `/api/items/{param}` too.
    mounted = ROUTES.replace('APIRouter(prefix="/api")', "APIRouter()")
    short = '\n\n@router.get("/{item_id}")\ndef get_any(item_id: str):\n    return None\n'
    _write(repo, ROUTES_FILE, mounted + short)
    _git(repo, "commit", "-q", "-am", "mount elsewhere, add a short route")
    _write(repo, ROUTES_FILE, mounted)
    assert _breaks(repo) == []


def test_a_topic_link_is_not_called_a_route(repo: Path) -> None:
    store_path = repo / ".repowise" / "contracts.json"
    data = json.loads(store_path.read_text(encoding="utf-8"))
    for link in data["contract_links"]:
        link["contract_type"] = "topic"
    store_path.write_text(json.dumps(data), encoding="utf-8")
    assert _card(repo, ROUTES_FILE)[ROUTES_FILE] == {"target": ROUTES_FILE}


def test_two_broken_routes_count_the_named_route_and_the_rest_apart() -> None:
    def site(file: str, line: int) -> dict:
        return {"route": "r", "file": file, "line": line}

    breaks = [
        rc._Break("http::GET::/a", "s.py", [site("c.ts", 1)], 0),
        rc._Break("http::GET::/b", "s.py", [site("d.ts", 2), site("e.ts", 3)], 0),
    ]
    line = rc.route_breaks_payload(breaks, detail=False)["in_repo_contracts"]
    assert line == (
        "stops serving 2 routes that 3 places in this repo call: GET /a (s.py) from c.ts:1; "
        '1 more route (include=["findings"] lists them)'
    )
