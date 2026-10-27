"""``/api/repos/{id}/setup`` and ``/api/setup``: what is set up for a repo, and the switches.

Reads follow the usual auth. Writes change files on this machine (agent
configs, git hooks, the coverage store, ``~/.repowise/platform.json``), so they
are accepted only when the server has no API key or tokens configured, the
caller is on loopback, and the browser's ``Origin`` is this server's own UI.
Every write runs the CLI command a person would type, so the two never differ.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.models import Repository
from repowise.core.platform import account, onboarding
from repowise.server.auth import get_authenticator
from repowise.server.deps import auth_is_open, client_is_local, get_db_session, verify_api_key
from repowise.server.routers._local_git import resolve_local_repo
from repowise.server.services import setup_cli, setup_status

router = APIRouter(tags=["setup"], dependencies=[Depends(verify_api_key)])

_LOOPBACK_NAMES = frozenset({"localhost", "127.0.0.1", "::1"})

#: An agent id as the registry spells them; ``--target`` keywords are refused
#: so one click can never mean "every agent".
_TARGET_RE = re.compile(r"[a-z][a-z0-9-]{0,31}\Z")
_TARGET_KEYWORDS = frozenset({"all", "auto", "none"})
_REPO_ID_RE = r"^[A-Za-z0-9:_.-]{1,64}$"


# -- models ------------------------------------------------------------------


class SetupIndex(BaseModel):
    state: Literal["up_to_date", "behind", "incomplete"]
    indexed_commit: str | None = None
    live_head: str | None = None
    indexed_at: str | None = None
    file_count: int = 0
    #: Files whose parse reported errors, so some symbols may be missing.
    parse_error_count: int = 0
    #: The first of them, by path.
    parse_error_files: list[str] = Field(default_factory=list)


class SetupRegistration(BaseModel):
    method: str
    scope: str
    config_path: str


class SetupAgent(BaseModel):
    id: str
    display_name: str
    tier: str | None = None
    docs_url: str | None = None
    #: The agent is on this machine.
    installed: bool
    #: Some config wires it to repowise.
    connected: bool
    registrations: list[SetupRegistration] = Field(default_factory=list)
    #: The files ``repowise agents add`` writes for it, per scope.
    config_files: dict[str, list[str]] = Field(default_factory=dict)
    #: This agent's calls to this repo's index, from the local savings ledger.
    first_call_at: str | None = None
    last_call_at: str | None = None


class SetupCalls(BaseModel):
    """Every call to this repo's index, whichever agent made it."""

    first_call_at: str | None = None
    last_call_at: str | None = None
    #: Some calls came from a client that named no known agent, so they count
    #: here and on no agent row.
    unattributed: bool = False


class SetupSync(BaseModel):
    #: ``None`` when doctor could not say.
    installed: bool | None = None
    detail: str = ""


class SetupRewriteHook(BaseModel):
    #: ``not_applicable`` when Claude Code is not on this machine.
    state: Literal["installed", "not_installed", "not_applicable", "unknown"]
    detail: str = ""


class SetupCoverageReport(BaseModel):
    #: Relative to the repo root.
    path: str
    modified_at: str
    age_days: int


class SetupCoverage(BaseModel):
    #: When coverage was last stored for this repo; ``None`` when never.
    ingested_at: str | None = None
    #: ``coverage.paths`` from ``.repowise/config.yaml``.
    configured_paths: list[str] = Field(default_factory=list)
    #: Reports found on disk by the same discovery ``coverage add`` uses.
    discovered: list[SetupCoverageReport] = Field(default_factory=list)


class SetupModel(BaseModel):
    #: ``deterministic`` = built from the index, ``llm`` = written by a model.
    docs_mode: Literal["none", "deterministic", "llm"]
    active_provider: str | None = None
    active_model: str | None = None
    #: Providers ``/api/providers`` reports as usable (a key, or none needed).
    configured_providers: list[str] = Field(default_factory=list)


class SetupCheck(BaseModel):
    id: str
    name: str
    level: Literal["ok", "warn", "fail"]
    detail: str = ""
    fix: str | None = None


class OnboardingState(BaseModel):
    """Per-machine onboarding state, plus the switches that hide hosted surfaces."""

    #: Repo id -> when the welcome was seen on this machine.
    welcome_seen: dict[str, str] = Field(default_factory=dict)
    setup_pill_hidden: bool = False
    hosted_card_hidden: bool = False
    #: Off with ``repowise config hints off``, ``REPOWISE_NO_HINTS`` or ``DO_NOT_TRACK``.
    hints_enabled: bool
    signed_in: bool


class SetupResponse(BaseModel):
    repo_id: str
    generated_at: str
    index: SetupIndex
    #: ``False`` when ``repowise agents`` could not run; ``agents`` is then empty.
    agents_available: bool
    agents: list[SetupAgent] = Field(default_factory=list)
    #: ``False`` when the savings ledger could not be read; ``calls`` and the
    #: agents' call times are then empty, which is not the same as "no calls".
    calls_available: bool
    calls: SetupCalls
    sync: SetupSync
    rewrite_hook: SetupRewriteHook
    #: ``False`` when stored coverage or the report search could not be read.
    coverage_available: bool
    coverage: SetupCoverage
    #: ``False`` when the provider settings could not be read; ``docs_mode`` still holds.
    model_available: bool
    model: SetupModel
    #: ``False`` when ``repowise doctor`` could not run; ``checks`` is then empty.
    checks_available: bool
    checks: list[SetupCheck] = Field(default_factory=list)
    onboarding: OnboardingState
    #: Whether this caller may use the write routes.
    writes_allowed: bool


class SetupFileChange(BaseModel):
    path: str
    #: ``created | updated | removed | unchanged | kept``.
    action: str
    scope: str
    reason: str | None = None


class SetupWriteResponse(BaseModel):
    """What a write did. ``ok`` is the command's own success."""

    ok: bool
    #: The same change as a terminal command.
    command: str
    message: str = ""
    #: The command's output, last lines first trimmed.
    output: list[str] = Field(default_factory=list)
    #: Agent config files, for the agents routes.
    files: list[SetupFileChange] = Field(default_factory=list)


class HookToggle(BaseModel):
    enabled: bool


class CoverageAddRequest(BaseModel):
    #: A report file inside the repo, relative to its root or absolute.
    path: str = Field(..., min_length=1, max_length=1024)


class OnboardingPatch(BaseModel):
    #: Marks the welcome seen for this repo, stamped with the server's time.
    welcome_seen_repo: str | None = Field(None, pattern=_REPO_ID_RE)
    setup_pill_hidden: bool | None = None
    hosted_card_hidden: bool | None = None


# -- write guard ---------------------------------------------------------------


def _ui_ports(request: Request) -> set[int]:
    """The API's own port, and the UI's when ``repowise serve`` started one.

    Nothing else on the usual UI port is trusted by default; a UI started some
    other way sets ``REPOWISE_UI_PORT`` for the API.
    """
    ports = {request.url.port or (443 if request.url.scheme == "https" else 80)}
    raw = os.environ.get("REPOWISE_UI_PORT", "")
    if raw.isdigit():
        ports.add(int(raw))
    return ports


def _loopback_port(value: str) -> int | None:
    """The port of an http(s) origin on a loopback name, else ``None``."""
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or parts.hostname not in _LOOPBACK_NAMES:
        return None
    try:
        return parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        return None


def _from_own_ui(request: Request) -> bool:
    """A browser request whose Origin is this server's UI on loopback.

    Browsers send ``Origin`` on every write, same-origin included, and a page
    cannot forge it. The Host check also turns away a DNS-rebound name.
    """
    if request.headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
        return False
    if _loopback_port(f"http://{request.headers.get('host') or ''}") is None:
        return False
    port = _loopback_port(request.headers.get("origin") or "")
    return port is not None and port in _ui_ports(request)


def require_local_write(request: Request) -> None:
    """403 unless this write comes from the local UI of a keyless, loopback server."""
    if get_authenticator().configured():
        raise HTTPException(
            status_code=403,
            detail="This server uses an API key or access tokens, so setup changes "
            "from the browser are off. Run the command in a terminal on this machine.",
        )
    if not client_is_local(request):
        raise HTTPException(
            status_code=403, detail="Setup changes are accepted only from this machine."
        )
    if not _from_own_ui(request):
        raise HTTPException(
            status_code=403,
            detail="Setup changes are accepted only from the repowise web UI on this machine.",
        )


def _onboarding_state() -> OnboardingState:
    return OnboardingState(
        **onboarding.load_onboarding(),
        hints_enabled=account.hints_enabled(),
        signed_in=account.is_signed_in(),
    )


# -- reads ---------------------------------------------------------------------


@router.get("/api/repos/{repo_id}/setup", response_model=SetupResponse)
async def get_setup(
    request: Request,
    repo: Repository = Depends(resolve_local_repo),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Everything the Setup page shows for one repo; see ``setup_status`` for the caching."""
    payload = await setup_status.setup_payload(session, repo)
    return {
        **payload,
        "onboarding": _onboarding_state(),
        "writes_allowed": auth_is_open(request),
    }


@router.get("/api/setup/onboarding", response_model=OnboardingState)
async def get_onboarding() -> OnboardingState:
    """Per-machine onboarding state, without the repo checks the full payload runs."""
    return _onboarding_state()


# -- writes --------------------------------------------------------------------


def _check_target(target: str) -> None:
    if not _TARGET_RE.match(target) or target in _TARGET_KEYWORDS:
        raise HTTPException(status_code=400, detail=f"Not an agent id: {target}")


def _file_changes(payload: Any) -> list[SetupFileChange]:
    """Every file ``repowise agents add|remove --format json`` reports."""
    changes: list[SetupFileChange] = []
    agents = payload.get("agents") if isinstance(payload, dict) else None
    for agent in agents if isinstance(agents, list) else []:
        for scope, write in (agent.get("writes") or {}).items():
            for f in write.get("files") or []:
                changes.append(
                    SetupFileChange(
                        path=str(f.get("path", "")),
                        action=str(f.get("action", "")),
                        scope=str(scope),
                        reason=f.get("reason"),
                    )
                )
    return changes


#: What a hook command also changes, said on success so the switch holds no surprise.
_HOOK_ALSO = {
    ("post-commit", False): "This also turned off the pre-commit security check and "
    "the health gate, if they were on.",
    ("rewrite", True): "Codex and Cursor get the rewrite hook too, when they are installed.",
    ("rewrite", False): "This also removed the Codex and Cursor rewrite hooks.",
}


def _write_response(
    run: setup_cli.CliRun, command: str, *, note: str = "", **extra: Any
) -> SetupWriteResponse:
    message = note if run.ok else f"That didn't work here. Run it in a terminal: {command}"
    output = [] if run.json() is not None else run.lines()
    return SetupWriteResponse(
        ok=run.ok, command=command, message=message, output=output, **extra
    )


async def _agents_write(repo: Repository, target: str, *, remove: bool) -> SetupWriteResponse:
    _check_target(target)
    run = await asyncio.to_thread(
        setup_cli.agents_write, Path(repo.local_path), target, remove=remove
    )
    setup_status.invalidate(repo.id)
    if run.exit_code == 2:
        # Click's usage error: the CLI does not know this id.
        raise HTTPException(status_code=400, detail=f"Not an agent id: {target}")
    verb = "remove" if remove else "add"
    command = f"repowise agents {verb} --target={target}"
    return _write_response(run, command, files=_file_changes(run.json()))


@router.post(
    "/api/repos/{repo_id}/setup/agents/{target}",
    response_model=SetupWriteResponse,
    dependencies=[Depends(require_local_write)],
)
async def connect_agent(
    target: str, repo: Repository = Depends(resolve_local_repo)
) -> SetupWriteResponse:
    """``repowise agents add --target=<target>`` for this repo."""
    return await _agents_write(repo, target, remove=False)


@router.delete(
    "/api/repos/{repo_id}/setup/agents/{target}",
    response_model=SetupWriteResponse,
    dependencies=[Depends(require_local_write)],
)
async def disconnect_agent(
    target: str, repo: Repository = Depends(resolve_local_repo)
) -> SetupWriteResponse:
    """``repowise agents remove --target=<target>`` for this repo."""
    return await _agents_write(repo, target, remove=True)


@router.put(
    "/api/repos/{repo_id}/setup/hooks/{name}",
    response_model=SetupWriteResponse,
    dependencies=[Depends(require_local_write)],
)
async def set_hook(
    name: str, body: HookToggle, repo: Repository = Depends(resolve_local_repo)
) -> SetupWriteResponse:
    """``repowise hook install|uninstall`` (``post-commit``) or ``hook rewrite ...`` (``rewrite``)."""
    if name not in setup_cli.HOOK_COMMANDS:
        raise HTTPException(status_code=404, detail=f"No hook named {name}")
    run = await asyncio.to_thread(
        setup_cli.hook_write, Path(repo.local_path), name, enabled=body.enabled
    )
    setup_status.invalidate(repo.id)
    verb = "install" if body.enabled else "uninstall"
    command = " ".join(["repowise", *setup_cli.HOOK_COMMANDS[name], verb])
    return _write_response(run, command, note=_HOOK_ALSO.get((name, body.enabled), ""))


def _report_inside_repo(repo_path: Path, raw: str) -> Path:
    """The report as an absolute file inside the repo, or a 400."""
    raw = raw.strip()
    if any(ch in raw for ch in "*?[=") or raw.startswith("-"):
        raise HTTPException(status_code=400, detail="Give one report file, without wildcards.")
    root = repo_path.resolve()
    candidate = Path(raw) if Path(raw).is_absolute() else root / raw
    try:
        report = candidate.resolve(strict=True)
    except OSError:
        raise HTTPException(status_code=400, detail=f"No file at {raw}") from None
    if not report.is_relative_to(root):
        raise HTTPException(status_code=400, detail="The report has to be inside this repository.")
    if not report.is_file():
        raise HTTPException(status_code=400, detail=f"{raw} is not a file")
    return report


@router.post(
    "/api/repos/{repo_id}/setup/coverage",
    response_model=SetupWriteResponse,
    dependencies=[Depends(require_local_write)],
)
async def add_coverage(
    body: CoverageAddRequest, repo: Repository = Depends(resolve_local_repo)
) -> SetupWriteResponse:
    """``repowise coverage add <report>`` for a report inside this repo."""
    repo_path = Path(repo.local_path)
    report = _report_inside_repo(repo_path, body.path)
    run = await asyncio.to_thread(setup_cli.coverage_add, repo_path, report)
    setup_status.invalidate(repo.id)
    relative = report.relative_to(repo_path.resolve()).as_posix()
    return _write_response(run, f"repowise coverage add {relative}")


@router.patch(
    "/api/setup/onboarding",
    response_model=OnboardingState,
    dependencies=[Depends(require_local_write)],
)
async def patch_onboarding(body: OnboardingPatch) -> OnboardingState:
    """Mark the welcome seen, or hide the setup pill or the hosted card, on this machine."""
    try:
        await asyncio.to_thread(
            onboarding.update_onboarding,
            welcome_seen_repo=body.welcome_seen_repo,
            setup_pill_hidden=body.setup_pill_hidden,
            hosted_card_hidden=body.hosted_card_hidden,
        )
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Couldn't save ~/.repowise/platform.json ({exc})"
        ) from exc
    setup_status.invalidate()
    return _onboarding_state()
