"""Path and origin rules that lower a finding's severity or narrow it to secrets."""

from __future__ import annotations

from pathlib import PurePosixPath

from repowise.core.support_paths import DOC_EXTENSIONS
from repowise.core.test_paths import is_test_related_path

from .patterns import SECRET_KINDS

# Broader than ``is_test_related_path`` on purpose. That one asks whether a file
# is a test and keeps bare ``fixtures/``, ``mocks/`` and ``spec/`` as production
# outside a test tree; this asks whether a match there is probably fake, and a
# ``high`` that is only probably real is not one to raise.
_LOW_SEVERITY_PATH_TOKENS: frozenset[str] = frozenset(
    {
        "test",
        "tests",
        "__tests__",
        "__test__",
        "fixtures",
        "__fixtures__",
        "spec",
        "specs",
        "mock",
        "mocks",
        "__mocks__",
        "example",
        "examples",
    }
)


def _is_low_severity_path(file_path: str) -> bool:
    """True when *file_path* is test material or under fixture, spec, mock, or example directories."""
    posix_path = file_path.replace("\\", "/")
    parts = [p.lower() for p in PurePosixPath(posix_path).parts]
    return any(p in _LOW_SEVERITY_PATH_TOKENS for p in parts) or is_test_related_path(posix_path)


# Code that runs at build or release time, or only in docs: a sink there is not
# a shipped one.
_NON_SHIPPING_ORIGINS = frozenset({"tooling", "docs_example"})
# A bundler's or generator's output, or a copied library: nobody here wrote its
# calls, so only a secret it carries is news.
_MACHINE_ORIGINS = frozenset({"generated", "vendored"})
_DECLARATION_SUFFIXES = (".d.ts", ".d.mts", ".d.cts")


def _holds_no_written_code(file_path: str, origin: str) -> bool:
    """Whether only secret kinds apply: machine output, or a TS declaration file, which never runs."""
    return origin in _MACHINE_ORIGINS or file_path.lower().endswith(_DECLARATION_SUFFIXES)


def _is_low_for_path(kind: str, is_low_sev_file: bool, origin: str) -> bool:
    """Test and fixture material never carries a shipped sink, whatever the kind.

    Nor do scripts, tools or docs examples, though a secret there still leaks.
    """
    return is_low_sev_file or (origin in _NON_SHIPPING_ORIGINS and kind not in SECRET_KINDS)


# Prose is documentation, not executable code. Only secret kinds scan it,
# because a key pasted into a README is still a leak.
_PROSE_EXTENSIONS = tuple(DOC_EXTENSIONS)
