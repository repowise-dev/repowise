"""The repository rationale harvest is kept beside the index per git blob.

A query reads stored rows for unchanged blobs and reads only changed files, so
the first question after an index write no longer reads every tracked file. The
served rows are the same as a live harvest's, in both query modes.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from repowise.core.analysis.decisions import rationale_harvest as rh
from repowise.core.pipeline.index_persistence import publish_index_persistence

_SOURCE = (
    "def send(request):\n"
    "    # Retry on 429 here rather than in the shared client, because the\n"
    "    # client is shared across tenants and a global backoff would starve them.\n"
    "    return request\n"
    "\n"
    "# Helper for formatting tenant names.\n"
    "def fmt(name):\n"
    "    return name\n"
)
_TEST_SOURCE = (
    "def test_send():\n"
    "    # Kept small because the fixture server is shared across workers.\n"
    "    assert True\n"
    "\n"
    "# Plain note about the fixture layout.\n"
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t.dev", "-c", "user.name=t", *args], cwd=repo, check=True
    )


def _repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    (tmp_path / "svc").mkdir()
    (tmp_path / "svc" / "client.py").write_text(_SOURCE, encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_client.py").write_text(_TEST_SOURCE, encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    (tmp_path / ".repowise").mkdir()
    return tmp_path


def _store(repo: Path) -> dict:
    return json.loads((repo / ".repowise" / "rationale_harvest.json").read_text(encoding="utf-8"))


def _both_modes(repo: Path) -> tuple[list[dict], list[dict]]:
    return rh.harvest_repo_rationale(repo, False), rh.harvest_repo_rationale(repo, True)


def test_stored_rows_serve_the_same_answer_as_a_live_harvest(tmp_path):
    repo = _repo(tmp_path)
    live = _both_modes(repo)

    rh.refresh_rationale_store(repo)

    assert set(_store(repo)["files"]) == {"svc/client.py", "tests/test_client.py"}
    assert _both_modes(repo) == live
    marker, plain = live
    # Marker mode takes causal comments from any file; plain mode every
    # production comment and nothing from tests.
    assert {r["path"] for r in marker} == {"svc/client.py", "tests/test_client.py"}
    assert [r["lines"][0] for r in plain] == [2, 6]


def test_a_query_reads_only_files_whose_blob_changed(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    rh.refresh_rationale_store(repo)
    (repo / "svc" / "client.py").write_text(_SOURCE[_SOURCE.index("# Helper"):], encoding="utf-8")
    _git(repo, "commit", "-qam", "edit")
    read: list[str] = []
    real = rh._read_under
    monkeypatch.setattr(rh, "_read_under", lambda root, path: read.append(path) or real(root, path))

    plain = rh.harvest_repo_rationale(repo, True)

    assert read == ["svc/client.py"]
    assert [r["comment"] for r in plain] == ["Helper for formatting tenant names."]


def test_a_stored_row_is_never_served_for_a_changed_blob(tmp_path):
    repo = _repo(tmp_path)
    rh.refresh_rationale_store(repo)
    data = _store(repo)
    data["files"]["svc/client.py"][2] = [[1, 1, "stale row", 1, True]]
    (repo / ".repowise" / "rationale_harvest.json").write_text(json.dumps(data), encoding="utf-8")
    assert "stale row" in str(rh.harvest_repo_rationale(repo, False))

    data["files"]["svc/client.py"][0] = "0" * 40
    (repo / ".repowise" / "rationale_harvest.json").write_text(json.dumps(data), encoding="utf-8")

    assert "stale row" not in str(rh.harvest_repo_rationale(repo, False))


def test_a_store_from_other_harvest_code_is_ignored(tmp_path):
    repo = _repo(tmp_path)
    rh.refresh_rationale_store(repo)
    data = _store(repo)
    data["fingerprint"] = "other"
    for entry in data["files"].values():
        entry[2] = []
    (repo / ".repowise" / "rationale_harvest.json").write_text(json.dumps(data), encoding="utf-8")

    assert rh.harvest_repo_rationale(repo, True)


def test_a_file_dirty_at_write_time_is_not_stored(tmp_path):
    repo = _repo(tmp_path)
    (repo / "svc" / "client.py").write_text(_SOURCE + "# Edited, uncommitted.\n", encoding="utf-8")

    rh.refresh_rationale_store(repo)

    assert set(_store(repo)["files"]) == {"tests/test_client.py"}


def test_an_index_write_refreshes_the_store(tmp_path):
    repo = _repo(tmp_path)

    asyncio.run(publish_index_persistence("repo-id", repo))

    assert "svc/client.py" in _store(repo)["files"]


def test_no_index_directory_writes_nothing(tmp_path):
    repo = _repo(tmp_path)
    (repo / ".repowise").rmdir()

    rh.refresh_rationale_store(repo)

    assert not (repo / ".repowise").exists()


def test_stored_rows_keep_the_per_file_cap_and_strength_order(tmp_path):
    repo = _repo(tmp_path)
    lines = [
        "# Kept small because the pool is shared.",
        "# Never retry here, because the caller retries and to avoid a storm.",
        "# Split out since the parser is slow, so it runs once.",
        "# Cached because the lookup is slow, otherwise every call pays it.",
        "# Ordered this way because the lock must be held first.",
    ]
    source = "\n".join(f"{line}\nx{i} = {i}\n" for i, line in enumerate(lines))
    (repo / "svc" / "many.py").write_text(source, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "many")
    live = _both_modes(repo)

    rh.refresh_rationale_store(repo)

    assert _both_modes(repo) == live
    marker = [r for r in live[0] if r["path"] == "svc/many.py"]
    assert len(marker) == 3


def test_a_dirty_file_in_a_subdirectory_root_is_not_stored(tmp_path):
    _repo(tmp_path)
    sub = tmp_path / "svc"
    (sub / ".repowise").mkdir()
    (sub / "client.py").write_text(_SOURCE + "# Edited, uncommitted.\n", encoding="utf-8")

    rh.refresh_rationale_store(sub)

    assert _store(sub)["files"] == {}
