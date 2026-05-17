"""Onboarding subkind: Development Guide.

How to do common things in this codebase — add a feature, write a test,
follow naming conventions — derived from structural repetition that's
already visible in the file tree.

Gate: at least 2 detectable repeated patterns (e.g., a strong "test
mirror" plus a recurring filename suffix, or two distinct filename
suffix groups). Without that signal, the guide would be inventing
conventions rather than reporting them.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from ..registry import SubkindSpec, register
from ..signals import OnboardingSignals
from ..slots import SLOT_DEVELOPMENT_GUIDE, SLOT_TITLES

_GATE_MIN_SIGNALS = 2
_MIN_SUFFIX_GROUP = 3
_TOP_SUFFIX_GROUPS = 6
_MIN_TEST_MIRROR_RATIO = 0.5

# Recognised test-tree roots — anything else with "test" in the name still
# counts but is treated as a weaker mirror signal.
_TEST_ROOTS: tuple[str, ...] = (
    "tests/", "test/", "__tests__/", "spec/", "specs/",
)


@dataclass
class SuffixPattern:
    """A repeating filename suffix — e.g. `_handler.py` x 7."""

    suffix: str
    examples: list[str] = field(default_factory=list)
    file_count: int = 0


@dataclass
class TestMirror:
    test_root: str
    matched_files: int
    source_files: int


@dataclass
class DevelopmentGuideContext:
    repo_name: str
    suffix_patterns: list[SuffixPattern] = field(default_factory=list)
    test_mirror: TestMirror | None = None
    parallel_dirs: list[str] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)


def _split_basename(name: str) -> list[str]:
    """Split a basename into trailing tokens for suffix grouping.

    Handles snake_case and camelCase, stripping the file extension.
    Example: `UserHandlerService.cs` → ['user', 'handler', 'service']
             `api_route_handler.py`   → ['api', 'route', 'handler']
    """
    stem = PurePosixPath(name).stem
    # CamelCase → space, then lower + split on underscore/hyphen/space.
    spaced = re.sub(r"(?<!^)(?=[A-Z])", " ", stem).lower()
    tokens = re.split(r"[\s_\-]+", spaced)
    return [t for t in tokens if t]


def _find_suffix_patterns(signals: OnboardingSignals) -> list[SuffixPattern]:
    by_suffix: dict[str, list[str]] = defaultdict(list)
    for pf in signals.parsed_files:
        path = pf.file_info.path
        # Skip test files — patterns inside tests/ are the mirror signal,
        # not the suffix signal.
        if any(seg in path for seg in _TEST_ROOTS):
            continue
        tokens = _split_basename(PurePosixPath(path).name)
        if len(tokens) < 2:
            continue
        suffix = tokens[-1]
        # Filter out generic single-letter / stop-word suffixes.
        if len(suffix) <= 2 or suffix in {"py", "ts", "js", "cs", "go", "rs"}:
            continue
        by_suffix[suffix].append(path)

    groups: list[SuffixPattern] = []
    for suffix, paths in by_suffix.items():
        if len(paths) < _MIN_SUFFIX_GROUP:
            continue
        groups.append(
            SuffixPattern(
                suffix=suffix,
                file_count=len(paths),
                examples=sorted(paths)[:3],
            )
        )
    groups.sort(key=lambda g: g.file_count, reverse=True)
    return groups[:_TOP_SUFFIX_GROUPS]


def _find_test_mirror(signals: OnboardingSignals) -> TestMirror | None:
    """Detect a "tests mirror source layout" convention.

    For every file under a known test root, strip the root and try to
    locate a non-test file that shares the remaining path stem. A mirror
    is reported when ≥ 50% of test files match.
    """
    source_paths: set[str] = {
        pf.file_info.path
        for pf in signals.parsed_files
        if not any(seg in pf.file_info.path for seg in _TEST_ROOTS)
    }

    best: TestMirror | None = None
    for root in _TEST_ROOTS:
        test_files = [pf.file_info.path for pf in signals.parsed_files if pf.file_info.path.startswith(root)]
        if len(test_files) < _MIN_SUFFIX_GROUP:
            continue
        # Strip leading "test_" / "_test" / "spec_" from the basename and
        # look for the resulting stem anywhere in the source tree.
        matched = 0
        for tf in test_files:
            stem = PurePosixPath(tf).stem
            stem_normalised = re.sub(r"^test_|^spec_|_test$|_spec$", "", stem, flags=re.IGNORECASE)
            if not stem_normalised:
                continue
            if any(stem_normalised in PurePosixPath(p).stem for p in source_paths):
                matched += 1
        if test_files and matched / len(test_files) >= _MIN_TEST_MIRROR_RATIO:
            candidate = TestMirror(
                test_root=root.rstrip("/"),
                matched_files=matched,
                source_files=len(test_files),
            )
            if best is None or candidate.matched_files > best.matched_files:
                best = candidate
    return best


def _find_parallel_dirs(signals: OnboardingSignals) -> list[str]:
    """Find sibling directories that look structurally parallel.

    A parallel layout shows up when multiple siblings under the same parent
    each contain the same set of basenames (e.g. `services/auth/{api.py,
    service.py, models.py}` repeated under `services/billing/…`).
    """
    by_parent: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for pf in signals.parsed_files:
        parts = PurePosixPath(pf.file_info.path).parts
        if len(parts) < 3:
            continue
        grandparent = parts[-3]
        parent = parts[-2]
        by_parent[grandparent][parent].add(parts[-1])

    parallel: list[str] = []
    for grandparent, siblings in by_parent.items():
        if len(siblings) < 3:
            continue
        # Look for a shared core of basenames across siblings.
        all_names: list[set[str]] = list(siblings.values())
        if not all_names:
            continue
        shared = set.intersection(*all_names)
        if len(shared) >= 2:
            parallel.append(grandparent)
    return sorted(parallel)[:5]


def _build(signals: OnboardingSignals) -> DevelopmentGuideContext | None:
    suffix_patterns = _find_suffix_patterns(signals)
    test_mirror = _find_test_mirror(signals)
    parallel_dirs = _find_parallel_dirs(signals)

    signal_count = (
        (1 if suffix_patterns else 0)
        + (1 if test_mirror else 0)
        + (1 if parallel_dirs else 0)
    )
    if signal_count < _GATE_MIN_SIGNALS:
        return None

    return DevelopmentGuideContext(
        repo_name=signals.repo_name,
        suffix_patterns=suffix_patterns,
        test_mirror=test_mirror,
        parallel_dirs=parallel_dirs,
        entry_points=list(getattr(signals.repo_structure, "entry_points", []))[:5],
    )


register(
    SubkindSpec(
        slot=SLOT_DEVELOPMENT_GUIDE,
        title=SLOT_TITLES[SLOT_DEVELOPMENT_GUIDE],
        template="development_guide.j2",
        build_context=_build,
    )
)
