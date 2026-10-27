from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from repowise.core import attribution


@pytest.fixture
def user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private user config file (absent until written), and no DO_NOT_TRACK."""
    path = tmp_path / "home" / ".repowise" / "config.yaml"
    path.parent.mkdir(parents=True)
    monkeypatch.setattr(attribution, "user_config_path", lambda: path)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    return path


class Repo:
    def __init__(self, root: Path) -> None:
        self.root = root

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.root, check=True, capture_output=True, text=True
        ).stdout

    def write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))

    def commit(self, message: str, *paths: str) -> str:
        self.git("add", "-A", *paths) if paths else self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD").strip()

    def configure(self, text: str) -> None:
        self.write(".repowise/config.yaml", text)


@pytest.fixture
def repo(tmp_path: Path, user_config: Path) -> Repo:
    root = (tmp_path / "repo").resolve()
    root.mkdir()
    r = Repo(root)
    r.git("init", "-q", "-b", "main")
    for key, value in (
        ("user.email", "dev@example.com"),
        ("user.name", "Dev"),
        ("commit.gpgsign", "false"),
        ("core.autocrlf", "false"),
    ):
        r.git("config", key, value)
    r.write(".gitignore", ".repowise/\n")
    r.write("README.txt", "demo\n")
    r.commit("init")
    return r
