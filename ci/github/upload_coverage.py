"""Uploads the pull request's coverage to the Repowise pull request bot.

Run by the "Upload coverage" step of action.yml. Reports resolve the way the
coverage gate resolves them (the coverage-report input, else coverage.paths in
.repowise/config.yaml, else discovery) and travel as repowise-coverage-v1 JSON:
line numbers per repository path, never source code. Identity is a GitHub
OIDC token; a fork's pull request, which cannot mint one, is sent without it.

An upload problem never fails the job: it prints a warning, sets the step
output ``upload`` to ``failed`` or ``skipped``, and exits 0. Inputs arrive as
environment variables set by action.yml; nothing is interpolated into code.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from importlib import metadata
from pathlib import Path

AUDIENCE = "repowise"
TIMEOUT = 60


class UploadError(Exception):
    """A problem that stops the upload; the message is shown as a warning."""


def warn(message: str) -> None:
    # Escaped so a multi-line server message cannot start a workflow command.
    text = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::warning title=Repowise coverage upload::{text}")


def set_output(status: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as out:
            out.write(f"upload={status}\n")


def build_report(cwd: Path, report_lines: str) -> tuple[dict, list[str]]:
    """The repowise-coverage-v1 ``files`` map and the formats read, resolved like the gate."""
    from repowise.core import git_refs
    from repowise.core.analysis.health.coverage import (
        CoverageConfig,
        build_coverage_map,
        expand_report_args,
    )
    from repowise.core.repo_config import RepoConfigError, load_repo_config

    root_text = git_refs.toplevel(str(cwd.resolve()))
    if not root_text:
        raise UploadError("Not a git repository, so report paths cannot be matched to files.")
    root = Path(root_text)
    try:
        cfg = CoverageConfig.from_repo_config(load_repo_config(root))
    except RepoConfigError as exc:
        raise UploadError(str(exc)) from None
    args = [line.strip() for line in report_lines.splitlines() if line.strip()]
    if args:
        try:
            # Relative to the working directory, as the gate reads --report.
            prefixes = expand_report_args(args, cwd)
        except FileNotFoundError as exc:
            raise UploadError(f"coverage-report: {exc}") from None
    else:
        prefixes = cfg.reports(root)
    if not prefixes:
        raise UploadError(
            "No coverage report found. Set coverage-report, or coverage.paths in "
            ".repowise/config.yaml."
        )
    print(f"Reading {', '.join(str(p) for p in prefixes)}")
    resolved, errors = build_coverage_map(
        root,
        list(prefixes),
        set(git_refs.tracked_paths(str(root))),
        coverage_format=cfg.format,
        strip_prefix=cfg.strip_prefix,
        path_prefix=cfg.path_prefix,
        report_prefixes=prefixes,
        ignore=cfg.ignore,
    )
    for path, err in errors:
        print(f"{path}: {err}")
    if not resolved.files:
        raise UploadError(
            "No report path matched a file in this repository. If the report paths carry "
            "a build prefix, set coverage.strip_prefix in .repowise/config.yaml."
        )
    if resolved.mapping_partial:
        print("More than half the report paths did not match a file in this repository.")
    files = {
        fc.file_path: {
            "covered_lines": sorted(fc.covered_lines),
            "coverable_lines": sorted(fc.coverable_lines),
            "line_coverage_pct": fc.line_coverage_pct,
        }
        for fc in sorted(resolved.files, key=lambda f: f.file_path)
    }
    return files, list(resolved.source_formats)


def oidc_token(env: dict[str, str]) -> str:
    """A GitHub OIDC token for the Repowise audience. Never printed."""
    url = env["ACTIONS_ID_TOKEN_REQUEST_URL"]
    sep = "&" if "?" in url else "?"
    request = urllib.request.Request(
        f"{url}{sep}audience={urllib.parse.quote(AUDIENCE)}",
        headers={"Authorization": f"bearer {env.get('ACTIONS_ID_TOKEN_REQUEST_TOKEN', '')}"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        token = json.load(response).get("value")
    if not token:
        raise ValueError("the token response had no value")
    return token


def identity(env: dict[str, str], pr_number: int | None) -> tuple[str | None, str]:
    """``(token, auth)``: auth is ``oidc``, ``tokenless``, ``private-fork`` or ``skip``."""
    from_fork = env.get("FROM_FORK") == "true" and pr_number is not None
    if env.get("ACTIONS_ID_TOKEN_REQUEST_URL"):
        try:
            return oidc_token(env), "oidc"
        except (OSError, ValueError) as exc:
            if not from_fork:
                raise UploadError(f"Could not get a GitHub OIDC token: {exc}") from None
    if from_fork:
        # A fork's pull request cannot mint a token; the server checks the PR instead,
        # and only on a public repository.
        return None, "private-fork" if env.get("REPO_PRIVATE") == "true" else "tokenless"
    return None, "skip"


def post(url: str, body: dict, token: str | None) -> dict:
    headers = {"Content-Type": "application/json", "User-Agent": "repowise-action"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise UploadError(f"Upload refused ({exc.code}). {_server_message(exc)}") from None
    except (urllib.error.URLError, OSError) as exc:
        raise UploadError(f"Could not reach {url}: {exc}") from None


def _server_message(exc: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(exc.read().decode("utf-8", "replace"))
    except (OSError, ValueError):
        return exc.reason or ""
    if not isinstance(payload, dict):
        return ""
    detail = payload.get("detail")
    parts = [detail if isinstance(detail, str) else json.dumps(detail)] if detail else []
    if payload.get("hint"):
        parts.append(f"Hint: {payload['hint']}")
    return " ".join(parts)


def _version() -> str:
    try:
        return metadata.version("repowise")
    except metadata.PackageNotFoundError:
        return ""


def upload(env: dict[str, str], cwd: Path) -> str:
    """Run the upload; the step output's value."""
    repository = env.get("REPOSITORY", "")
    head_sha = env.get("HEAD_SHA", "").lower()
    pr_text = env.get("PR_NUMBER", "").strip()
    pr_number = int(pr_text) if pr_text.isdigit() else None

    token, auth = identity(env, pr_number)
    if auth == "skip":
        warn(
            "Coverage was not uploaded: the job cannot prove it runs in this repository. "
            "Add `permissions: id-token: write` to the workflow or job."
        )
        return "skipped"
    if auth == "private-fork":
        warn(
            "Coverage was not uploaded: a pull request from a fork of a private repository "
            "cannot prove where it runs."
        )
        return "skipped"

    files, formats = build_report(cwd, env.get("COVERAGE_REPORT", ""))
    body = {
        "repository": repository,
        "head_sha": head_sha,
        "pr_number": pr_number,
        "event": env.get("EVENT_NAME", ""),
        "report": {"format": "repowise-coverage-v1", "commit_sha": head_sha, "files": files},
        "run_url": env.get("RUN_URL", ""),
        "repowise_version": _version(),
        "source_formats": formats,
    }
    if auth == "tokenless":
        print("Pull request from a fork: uploading without a verified identity.")
    reply = post(env["UPLOAD_URL"], body, token)
    print(
        f"Coverage accepted for {head_sha[:12]}: {reply.get('files', len(files))} files, "
        f"{reply.get('covered_lines', '?')} of {reply.get('coverable_lines', '?')} "
        "coverable lines covered."
    )
    print(f"Pull request bot notified: {'yes' if reply.get('bot_notified') else 'no'}.")
    return "accepted"


def main() -> int:
    try:
        status = upload(dict(os.environ), Path.cwd())
    except UploadError as exc:
        warn(str(exc))
        status = "failed"
    except Exception as exc:  # an upload problem never fails the job
        warn(f"Coverage upload failed: {type(exc).__name__}: {exc}")
        status = "failed"
    set_output(status)
    return 0


if __name__ == "__main__":
    sys.exit(main())
