"""Unified `git cat-file --batch` stream reader.

Parses git's batch stdout stream:
- `<oid> <type> <size>\\n<body bytes>\\n` for objects present in the repository
- `<sha> submodule\\n` for submodules whose commit is not in this repository
- `<spec> missing\\n` for missing objects
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence

GIT_TIMEOUT_SECONDS = 60


def cat_file_batch(
    repo_path: str,
    specs: Sequence[str],
    *,
    on_missing: str = "omit",
    timeout: float = GIT_TIMEOUT_SECONDS,
    check: bool = False,
) -> list[tuple[str, bytes | None]] | None:
    """Read a batch of git specs in one subprocess invocation.

    Returns a list of `(spec, blob_bytes)` pairs matching the order of `specs`.
    If an object is not a blob (a directory tree or a submodule commit), its
    bytes value is `None`, and its stream body is safely consumed so subsequent
    objects stay aligned.

    - `on_missing='omit'`: Missing objects or non-blobs have `None` bytes.
    - `on_missing='raise'`: Raises `ValueError` on missing object line.
    - `check=True`: Raises `subprocess.CalledProcessError` if git exits non-zero.
    - `check=False`: Returns `None` if git exits non-zero.
    """
    if not specs:
        return []
    request = "\n".join(specs) + "\n"
    env = {**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_LITERAL_PATHSPECS": "1"}
    try:
        proc = subprocess.run(
            ["git", "cat-file", "--batch"],
            cwd=repo_path,
            input=request.encode("utf-8"),
            capture_output=True,
            timeout=timeout,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        if check:
            raise
        return None
    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode, proc.args, output=proc.stdout, stderr=proc.stderr
        )
    if proc.returncode != 0:
        return None

    data, pos = proc.stdout, 0
    out: list[tuple[str, bytes | None]] = []
    for spec in specs:
        nl = data.find(b"\n", pos)
        if nl == -1:
            if on_missing == "raise":
                raise ValueError(f"git cat-file stream ended unexpectedly for {spec!r}")
            break
        header = data[pos:nl]
        fields = header.decode("utf-8", "replace").split()
        if header.endswith(b" missing"):
            pos = nl + 1
            if on_missing == "raise":
                is_submodule = False
                if ":" in spec:
                    rev, path = spec.split(":", 1)
                    if rev and path:
                        try:
                            chk = subprocess.run(
                                ["git", "ls-tree", rev, path],
                                cwd=repo_path,
                                capture_output=True,
                                timeout=5.0,
                                text=True,
                            )
                            if chk.returncode == 0 and chk.stdout.startswith("160000 commit "):
                                is_submodule = True
                        except (OSError, subprocess.SubprocessError):
                            pass
                if not is_submodule:
                    raise ValueError(f"git has no object for {spec!r}")
            out.append((spec, None))
            continue
        if fields[1:] == ["submodule"]:
            pos = nl + 1
            out.append((spec, None))
            continue
        if len(fields) < 3 or not fields[2].isdigit():
            if on_missing == "raise":
                raise ValueError(
                    f"git cat-file unparseable header for {spec!r}: {header.decode('utf-8', 'replace')}"
                )
            break  # unparseable header: stop rather than misread the stream
        obj_type = fields[1]
        size = int(fields[2])
        start = nl + 1
        pos = start + size + 1
        blob = data[start : start + size] if obj_type == "blob" else None
        out.append((spec, blob))
    return out
