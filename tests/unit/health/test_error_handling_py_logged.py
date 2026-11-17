"""Python ``broad_except``: an ``except Exception`` that logs the failure is not broad.

A handler that reports the error through a logger (or ``warnings.warn``) and
then continues or returns a fallback hides nothing. A silent one, a
``debug``-only one and a bare ``except:`` (which also catches Ctrl-C) stay
findings.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file


def _kinds(source: bytes) -> list[str]:
    return [h.kind for h in walk_file("<fixture>", "python", source).error_handling_hits]


def _handler(body: str, catch: str = "except Exception as e:") -> bytes:
    lines = "\n".join(f"        {x}" for x in body.split("\n"))
    return f"def f():\n    try:\n        x()\n    {catch}\n{lines}\n".encode()


@pytest.mark.parametrize(
    "body",
    [
        'logger.exception("failed")',
        'logger.warning("failed: %s", e)\n        return None',
        'self.logger.error("x")\n        continue',
        'app_log.error("Uncaught exception", exc_info=True)',
        '_LOGGER.error("x")\n        result = None',
        'logging.exception("x")',
        'log.info("skipping %s", e)',
        'warnings.warn(f"plugin failed: {e}")',
        'warn("plugin failed")',
    ],
)
def test_logging_broad_handler_is_not_broad(body: str) -> None:
    assert "broad_except" not in _kinds(_handler(body))


@pytest.mark.parametrize(
    ("body", "catch"),
    [
        ("return None", "except Exception:"),
        ("result = None", "except Exception as e:"),
        ('logger.debug("failed")', "except Exception:"),
        ('catalog.error("x")', "except Exception:"),
        ("logger.setLevel(10)", "except Exception:"),
        ('logger.exception("x")', "except:"),
        ('logger.exception("x")', "except BaseException:"),
    ],
)
def test_silent_or_bare_handler_still_fires(body: str, catch: str) -> None:
    kinds = _kinds(_handler(body, catch))
    assert ("bare_except" in kinds) or ("broad_except" in kinds), kinds
