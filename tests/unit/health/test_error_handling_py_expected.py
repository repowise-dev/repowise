"""Python ``swallowed_catch``: an empty handler for an expected outcome is not swallowed.

``except ImportError: pass`` (an optional dependency), ``except KeyError: pass``
(delete a key that may be gone) and the like are the ``contextlib.suppress``
idiom. Broad or unrelated types with an empty body stay findings.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file


def _kinds(source: bytes) -> list[str]:
    return [h.kind for h in walk_file("<fixture>", "python", source).error_handling_hits]


@pytest.mark.parametrize(
    "source",
    [
        b"try:\n    import ujson as json\nexcept ImportError:\n    pass\n",
        b"try:\n    import yaml\nexcept ModuleNotFoundError:\n    yaml = None\n    pass\n",
        b"try:\n    del cache[key]\nexcept KeyError:\n    pass\n",
        b"try:\n    os.remove(p)\nexcept FileNotFoundError:\n    pass\n",
        b"try:\n    os.mkdir(p)\nexcept FileExistsError:\n    pass\n",
        b"try:\n    os.kill(pid, 9)\nexcept ProcessLookupError:\n    pass\n",
        b"try:\n    first = next(it)\nexcept StopIteration:\n    pass\n",
        b"try:\n    await task\nexcept asyncio.CancelledError:\n    pass\n",
        b"try:\n    import a\nexcept (ImportError, KeyError) as e:  # optional\n    pass\n",
        b"try:\n    shutil.rmtree(d)\nexcept builtins.FileNotFoundError:\n    ...\n",
        b"try:\n    sock.connect(addr)\nexcept BlockingIOError:\n    # EINPROGRESS\n    pass\n",
        b"try:\n    stdin.close()\nexcept BrokenPipeError:\n    pass\n",
        b"try:\n    loop.run_forever()\nexcept KeyboardInterrupt:\n    pass\n",
    ],
)
def test_expected_exception_with_empty_body_is_not_swallowed(source: bytes) -> None:
    assert "swallowed_catch" not in _kinds(source)


@pytest.mark.parametrize(
    ("source", "kinds"),
    [
        (b"try:\n    x()\nexcept ValueError:\n    pass\n", ["swallowed_catch"]),
        (b"try:\n    x()\nexcept (ImportError, ValueError):\n    pass\n", ["swallowed_catch"]),
        (b"try:\n    x()\nexcept OSError:\n    pass\n", ["swallowed_catch"]),
        (b"try:\n    x()\nexcept Exception:\n    pass\n", ["swallowed_catch", "broad_except"]),
        (b"try:\n    x()\nexcept:\n    pass\n", ["swallowed_catch", "bare_except"]),
    ],
)
def test_broad_or_unrelated_empty_handler_stays_swallowed(source: bytes, kinds: list[str]) -> None:
    assert _kinds(source) == kinds
