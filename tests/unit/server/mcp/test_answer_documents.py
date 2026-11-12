"""get_answer reaches documents through the live line scan.

Markdown gets no wiki page, so a question word that only a few documents write
names them: this repo's join the hits, another workspace repo's are served as
``other_repo_lines``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from repowise.server.mcp_server import _state
from repowise.server.mcp_server.tool_answer.answer import _Ask, _user_prompt
from repowise.server.mcp_server.tool_answer.callers import NO_EVIDENCE
from repowise.server.mcp_server.tool_answer.documents import find_documents, place_documents

_GUIDE = "# Deploy guide\n\nTo roll back, flip the zanzibarflux switch.\n"


def _repo(root: Path, files: dict[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


async def _find(root: Path, question: str, scope: str | None = None, ctx=None):
    ctx = ctx or SimpleNamespace(alias="default", path=root)
    return await find_documents(question, root, ctx, scope=scope, exclude_spec=None)


@pytest.fixture(autouse=True)
def _single_repo(monkeypatch):
    monkeypatch.setattr(_state, "_registry", None)


async def test_a_word_only_a_document_writes_makes_it_a_hit(tmp_path):
    root = _repo(tmp_path / "r", {"guide.md": _GUIDE, "app.py": "def run():\n    pass\n"})

    docs = await _find(root, "How do I roll back with the zanzibarflux switch?")

    assert [h["target_path"] for h in docs.hits] == ["guide.md"]
    hit = docs.hits[0]
    assert hit["page_type"] == "file"
    assert hit["excerpt"] == "3: To roll back, flip the zanzibarflux switch."
    assert hit["summary"] == "Line 3: To roll back, flip the zanzibarflux switch."
    assert docs.other_repo_lines == []


async def test_a_word_code_also_writes_names_no_document(tmp_path):
    root = _repo(tmp_path / "r", {"guide.md": _GUIDE, "app.py": "zanzibarflux = 1\n"})

    docs = await _find(root, "What is zanzibarflux?")

    assert docs.hits == [] and docs.other_repo_lines == []


async def test_a_word_many_documents_write_names_none(tmp_path):
    files = {f"page{i}.md": "the zanzibarflux switch\n" for i in range(4)}
    root = _repo(tmp_path / "r", files)

    docs = await _find(root, "What is the zanzibarflux switch?")

    assert docs.hits == []


async def test_words_match_whole_words_only(tmp_path):
    root = _repo(tmp_path / "r", {"guide.md": "the stall alarm\n", "setup.py": "install()\n"})

    docs = await _find(root, "Why does the stall alarm fire?")

    assert [h["target_path"] for h in docs.hits] == ["guide.md"]


async def test_docs_sites_and_vendored_documents_need_a_scope(tmp_path):
    root = _repo(
        tmp_path / "r",
        {"docs/guide.md": _GUIDE, "vendor/lib/README.md": "zanzibarflux notes\n"},
    )
    question = "How do I roll back with the zanzibarflux switch?"

    assert (await _find(root, question)).hits == []
    scoped = await _find(root, question, scope="docs/")
    assert [h["target_path"] for h in scoped.hits] == ["docs/guide.md"]


async def test_no_document_word_leaves_the_answer_as_it_was(tmp_path):
    root = _repo(tmp_path / "r", {"app.py": "def run():\n    pass\n"})

    docs = await _find(root, "How does run work?")

    assert docs.hits == [] and docs.other_repo_lines == []


def _workspace(monkeypatch, ws: Path, members: dict[str, Path]) -> None:
    entries = {alias: SimpleNamespace(path=str(path)) for alias, path in members.items()}
    registry = SimpleNamespace(
        workspace_root=ws,
        ws_config=SimpleNamespace(get_repo=entries.get),
        get_all_aliases=lambda: list(entries),
    )
    monkeypatch.setattr(_state, "_registry", registry)


async def test_other_workspace_repos_documents_are_served_as_lines(tmp_path, monkeypatch):
    code = _repo(tmp_path / "code", {"deploy.py": "def deploy(queue):\n    return queue\n"})
    mirror = _repo(tmp_path / "mirror", {"jira/PROJ-12.md": "Deploys stall when quokkaballast fills.\n"})
    _workspace(monkeypatch, tmp_path, {"code": code, "mirror": mirror})

    docs = await _find(
        code, "Why do deploys stall when quokkaballast fills?", ctx=SimpleNamespace(alias="code")
    )

    assert docs.hits == []
    assert docs.other_repo_lines == [
        {
            "repo": "mirror",
            "path": "jira/PROJ-12.md",
            "line": 1,
            "text": "Deploys stall when quokkaballast fills.",
        }
    ]


async def test_code_in_another_workspace_repo_keeps_a_word_from_naming_documents(
    tmp_path, monkeypatch
):
    code = _repo(tmp_path / "code", {"guide.md": _GUIDE})
    other = _repo(tmp_path / "other", {"flux.py": "zanzibarflux = True\n"})
    _workspace(monkeypatch, tmp_path, {"code": code, "other": other})

    docs = await _find(code, "What is zanzibarflux?", ctx=SimpleNamespace(alias="code"))

    assert docs.hits == []


async def test_a_scope_reads_this_repo_only(tmp_path, monkeypatch):
    code = _repo(tmp_path / "code", {"app.py": "x = 1\n"})
    mirror = _repo(tmp_path / "mirror", {"a.md": "quokkaballast\n"})
    _workspace(monkeypatch, tmp_path, {"code": code, "mirror": mirror})

    docs = await _find(
        code, "What is quokkaballast?", scope="src/", ctx=SimpleNamespace(alias="code")
    )

    assert docs.other_repo_lines == []


async def test_other_repo_lines_reach_the_synthesis_prompt():
    ask = _Ask("Why do deploys stall?", SimpleNamespace(), None, "r", None, 0.0)
    rows = [{"repo": "mirror", "path": "jira/PROJ-12.md", "line": 17, "text": "Deploys stall."}]

    prompt = await _user_prompt(ask, [], NO_EVIDENCE, rows)

    assert "- mirror: jira/PROJ-12.md:17: Deploys stall." in prompt
    assert "other repositories" not in await _user_prompt(ask, [], NO_EVIDENCE, [])


def _page(path: str, score: float) -> dict:
    return {"target_path": path, "page_type": "file_page", "score": score}


def test_a_document_joins_below_the_top_three():
    doc = {"target_path": "guide.md", "page_type": "file", "score": 0.0, "excerpt": "3: x"}
    hits = [_page(f"m{i}.py", 5.0 - i) for i in range(5)]

    placed = place_documents(hits, [doc])

    assert [h["target_path"] for h in placed] == ["m0.py", "m1.py", "m2.py", "guide.md", "m3.py", "m4.py"]
    assert place_documents([], [doc]) == [doc]
    assert place_documents(hits, []) is hits


def test_a_hit_already_on_the_document_gets_its_lines():
    doc = {"target_path": "guide.md", "page_type": "file", "score": 0.0, "excerpt": "3: x"}
    hits = [_page("guide.md", 9.0)]

    placed = place_documents(hits, [doc])

    assert placed == [{**_page("guide.md", 9.0), "excerpt": "3: x"}]
