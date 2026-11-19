"""Text-mode subprocess output must not depend on the Windows locale (#2186).

The guard covers explicit keyword arguments, including aliased imports; it
does not follow values passed through **kwargs or custom process wrappers.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from repowise.core.providers.llm.codex_cli import (
    CodexModelReasoning,
    _load_codex_model_catalog,
)

_PACKAGES = Path(__file__).resolve().parents[2] / "packages"
_SPAWNERS = {"run", "Popen", "check_output", "check_call", "call"}


def _subprocess_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess":
                    names.update(f"{alias.asname or alias.name}.{name}" for name in _SPAWNERS)
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            names.update(alias.asname or alias.name for alias in node.names if alias.name in _SPAWNERS)
    return names


def _missing_decoding(source: str) -> list[int]:
    tree = ast.parse(source)
    names = _subprocess_names(tree)
    missing: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or ast.unparse(node.func) not in names:
            continue
        keywords = {kw.arg: kw.value.value for kw in node.keywords if isinstance(kw.value, ast.Constant)}
        text_mode = any(keywords.get(key) for key in ("text", "universal_newlines", "encoding", "errors"))
        if text_mode and (keywords.get("encoding"), keywords.get("errors")) != ("utf-8", "replace"):
            missing.append(node.lineno)
    return missing


def test_text_subprocesses_use_explicit_utf8_replacement_decoding():
    missing = []
    for source_root in sorted(_PACKAGES.glob("*/src")):
        for path in sorted(source_root.rglob("*.py")):
            for line in _missing_decoding(path.read_text(encoding="utf-8")):
                missing.append(f"{path.relative_to(_PACKAGES.parent)}:{line}")
    assert not missing, (
        'Text-mode subprocess calls need encoding="utf-8", errors="replace":\n'
        + "\n".join(missing)
    )


@pytest.mark.parametrize(
    ("source", "missing"),
    [
        ("import subprocess as sp; sp.run([], text=True)", [1]),
        ("from subprocess import check_output as read; read([], universal_newlines=True)", [1]),
        ("import subprocess; subprocess.Popen([], encoding='utf-8')", [1]),
        ("import subprocess; subprocess.run([], text=True, errors='replace')", [1]),
        ("import subprocess; subprocess.run([], text=True, encoding='cp1252', errors='replace')", [1]),
        ("import subprocess; subprocess.run([], text=True, encoding='utf-8', errors='strict')", [1]),
        ("import subprocess; subprocess.run([], encoding='utf-8', errors='replace')", []),
        ("import subprocess; subprocess.run([], text=False)", []),
        ("import subprocess; subprocess.run([])", []),
        ("other.run([], text=True)", []),
    ],
)
def test_decoding_guard_recognizes_text_calls(source, missing):
    assert _missing_decoding(source) == missing


@pytest.mark.parametrize(
    ("slug_bytes", "slug"),
    [(b"caf\xc3\xa9\xe2\x80\x9d", "café”"), (b"invalid-\xff", "invalid-\ufffd")],
)
def test_codex_catalog_decodes_real_child_output(monkeypatch, tmp_path, slug_bytes, slug):
    # Python's default Windows codec must not control decoding in the parent.
    monkeypatch.setattr(subprocess, "_text_encoding", lambda: "cp1252")
    payload = (
        b'{"models":[{"slug":"' + slug_bytes
        + b'","default_reasoning_level":"high","supported_reasoning_levels":[{"effort":"high"}]}]}'
    )
    # Use Python as the CLI: its first argument, "debug", names this script.
    # subprocess.run and its stdout/stderr decoding remain completely real.
    (tmp_path / "debug").write_text(
        f"import sys\nsys.stdout.buffer.write({payload!r})\nsys.stderr.buffer.write(b'warning: \\xff')\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    catalog = _load_codex_model_catalog.__wrapped__(sys.executable)

    assert catalog == {
        slug: CodexModelReasoning(slug=slug, default_effort="high", supported_efforts=("high",)),
    }
