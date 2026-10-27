"""The Alembic chain has one head and no duplicated revision ids.

Two branches that each add the next-numbered migration both merge cleanly in
git, so the clash only shows when a database upgrades.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

_ALEMBIC = Path(__file__).resolve().parents[3] / "packages" / "core" / "alembic"


def _script() -> ScriptDirectory:
    config = Config()
    config.set_main_option("script_location", str(_ALEMBIC))
    return ScriptDirectory.from_config(config)


def test_one_head():
    assert len(_script().get_heads()) == 1


def test_revision_ids_are_unique():
    ids = Counter()
    for path in (_ALEMBIC / "versions").glob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("revision:"):
                ids[line.split("=", 1)[1].strip().strip("\"'")] += 1
    assert [rev for rev, n in ids.items() if n > 1] == []
