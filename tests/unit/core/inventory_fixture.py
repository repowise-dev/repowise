"""A small indexed repository with third-party imports, for the inventory tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import (
    DeadCodeFinding,
    ExternalSystem,
    GraphEdge,
    GraphNode,
    Repository,
)

REPO = "repo1"
_NOW = datetime(2026, 10, 1, tzinfo=UTC)

CATALOG = """\
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: app
  annotations:
    repowise.dev/paths: src/app/**
spec: {type: service, lifecycle: production, owner: group:default/platform}
---
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: jobs
  annotations:
    repowise.dev/paths: src/jobs/**
spec: {type: service, lifecycle: production}
"""

SOURCES = {
    "src/app/main.py": "from src.app import util\n",
    "src/app/util.py": "import os\nfrom requests import get\nfrom requests.compat import urljoin\n",
    # A comment naming the module is not an import statement.
    "src/jobs/worker.py": "# import requests directly\nfrom requests.adapters import HTTPAdapter\n"
    "import yaml\n",
    "src/old/legacy.py": "import requests\n",
    "tests/test_util.py": "import requests\n",
}


def write_repo(root: Path) -> None:
    for rel, text in SOURCES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    (root / ".repowise").mkdir(exist_ok=True)
    (root / ".repowise" / "components.yaml").write_text(CATALOG, encoding="utf-8")
    (root / ".github").mkdir(exist_ok=True)
    (root / ".github" / "CODEOWNERS").write_text(
        "/src/jobs/ @acme/batch-team\n/src/old/ @someone\n", encoding="utf-8"
    )


def rows(root: Path) -> list:
    out: list = [
        Repository(
            id=REPO,
            name="demo",
            local_path=str(root),
            settings_json="{}",
            created_at=_NOW,
            updated_at=_NOW,
        ),
        ExternalSystem(
            id=1, repository_id=REPO, name="requests", ecosystem="pypi",
            version=">=2.31", declared_in="pyproject.toml",
        ),
        ExternalSystem(
            id=2, repository_id=REPO, name="PyYAML", ecosystem="pypi",
            version="6.0", declared_in="pyproject.toml",
        ),
        ExternalSystem(
            id=3, repository_id=REPO, name="pytest", ecosystem="pypi",
            declared_in="pyproject.toml", is_dev_dep=True,
        ),
        # The same distribution spelled another way in a second manifest.
        ExternalSystem(
            id=4, repository_id=REPO, name="pyyaml", ecosystem="pypi",
            version="6.0.1", declared_in="requirements.txt",
        ),
    ]  # fmt: skip
    for path in SOURCES:
        out.append(
            GraphNode(
                repository_id=REPO,
                node_id=path,
                node_type="file",
                language="python",
                is_entry_point=path == "src/app/main.py",
                is_test=path.startswith("tests/"),
            )
        )
    for node_id, system in (
        ("external:requests", 1),
        ("external:requests.adapters", 1),
        ("external:yaml", None),
        ("external:requests.compat", None),
    ):
        out.append(
            GraphNode(
                repository_id=REPO,
                node_id=node_id,
                node_type="external",
                external_system_id=system,
            )
        )
    for src, dst, names in (
        ("src/app/main.py", "src/app/util.py", '["util"]'),
        ("src/app/util.py", "external:requests", '["get"]'),
        ("src/jobs/worker.py", "external:requests.adapters", '["HTTPAdapter"]'),
        ("src/jobs/worker.py", "external:yaml", "[]"),
        ("src/app/util.py", "external:requests.compat", '["urljoin"]'),
        ("src/old/legacy.py", "external:requests", "[]"),
        ("tests/test_util.py", "external:requests", "[]"),
    ):
        out.append(
            GraphEdge(
                repository_id=REPO,
                source_node_id=src,
                target_node_id=dst,
                imported_names_json=names,
                edge_type="imports",
            )
        )
    out.append(
        DeadCodeFinding(
            repository_id=REPO, kind="unreachable_file", file_path="src/old/legacy.py",
            confidence=0.9,
        )
    )  # fmt: skip
    return out


@pytest.fixture
async def session(tmp_path):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await init_db(engine)
    write_repo(tmp_path)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        s.add_all(rows(tmp_path))
        await s.commit()
        yield s
    await engine.dispose()
