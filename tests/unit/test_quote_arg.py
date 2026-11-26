"""`quote_arg` keeps a printed test path one shell argument."""

from __future__ import annotations

import pytest

from repowise.core.test_runners.packages import quote_arg


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("app/routes/$id.test.tsx", "'app/routes/$id.test.tsx'"),
        ("app/routes/`id`.test.tsx", "'app/routes/`id`.test.tsx'"),
        ('app/routes/"id".test.tsx', "'app/routes/\"id\".test.tsx'"),
        ("app/routes/$id page.test.tsx", "'app/routes/$id page.test.tsx'"),
        ("app/routes/has space.test.tsx", '"app/routes/has space.test.tsx"'),
        ("app/routes/plain.test.tsx", "app/routes/plain.test.tsx"),
        # Ceiling: single quotes cannot contain one, so the path stays as is.
        ("app/routes/$id's.test.tsx", "app/routes/$id's.test.tsx"),
    ],
)
def test_quote_arg(path: str, expected: str) -> None:
    assert quote_arg(path) == expected
