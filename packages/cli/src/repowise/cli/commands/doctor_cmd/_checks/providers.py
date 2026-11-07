"""Checks 5-7: providers, hosted account, stale pages."""

from __future__ import annotations

from pathlib import Path as _DoctorPath

from repowise.cli.helpers import run_async

from .._types import DoctorCheck, _check
from .context import CheckContext, open_store


async def _stale_page_counts(session: object, repo_id: str) -> dict[str, int]:
    """Count stale pages by type without hydrating their rendered content."""
    from sqlalchemy import func, select

    from repowise.core.persistence.models import Page

    result = await session.execute(  # type: ignore[attr-defined]
        select(Page.page_type, func.count())
        .where(
            Page.repository_id == repo_id,
            Page.freshness_status.in_(["stale", "expired"]),
        )
        .group_by(Page.page_type)
    )
    return {str(page_type): int(count) for page_type, count in result.all()}


def _stale_page_guidance(counts: dict[str, int]) -> str:
    """Describe stale work by owner and name commands that can clear it."""
    from repowise.core.generation.models import MODEL_WRITTEN_PAGE_TYPES

    total = sum(counts.values())
    model_written = sum(
        count for page_type, count in counts.items() if page_type in MODEL_WRITTEN_PAGE_TYPES
    )
    structural = total - model_written
    categories = []
    if model_written:
        categories.append(f"{model_written} model-written")
    if structural:
        categories.append(f"{structural} structural")

    detail = (
        f"{total} stale ({', '.join(categories)}) — content no longer matches its "
        "current inputs or generation selection."
    )
    if model_written:
        detail += (
            " `repowise generate --stale` is the cheaper refresh for model-written "
            "pages that are still selected."
        )
    detail += (
        " `repowise update --full` performs the authoritative reconciliation and "
        "retires pages no longer selected."
    )
    return detail + " `--repair` only fixes store drift."


def _provider_checks(repo_path: _DoctorPath) -> list[DoctorCheck]:
    """Report provider implementations, configuration errors, and usability.

    These are three distinct claims.  In particular, an empty validation
    warning list means no configured key is malformed; it does not prove that
    an LLM provider will resolve for this repository.
    """
    checks: list[DoctorCheck] = []

    try:
        from repowise.core.providers import list_providers

        providers = list_providers()
        checks.append(
            _check(
                "Providers",
                bool(providers),
                f"Implementations loaded: {', '.join(providers)}",
                check_id="providers",
            )
        )
    except Exception as exc:
        checks.append(_check("Providers", False, str(exc), check_id="providers"))

    from repowise.cli.helpers import validate_provider_config

    config_warnings = validate_provider_config(repo_path=repo_path)
    config_ok = not config_warnings
    config_detail = "No misconfigured provider keys" if config_ok else "; ".join(config_warnings)
    checks.append(_check("Provider config", config_ok, config_detail, check_id="provider-config"))

    from repowise.core.providers.llm.registry import provider_available_for_repo

    llm_available = provider_available_for_repo(repo_path)
    llm_detail = (
        "Resolves for this repository"
        if llm_available
        else "None configured — prose degrades to a structural wiki; set a key or pass --provider"
    )
    # Keyless operation is supported, so this is informational rather than a
    # failing health check.  The detail tells users what init will actually do.
    checks.append(_check("LLM provider", True, llm_detail, check_id="llm-provider"))
    return checks


def _hosted_account_check() -> DoctorCheck:
    """Informational: signed out is not a failure."""
    name, check_id = "Hosted account", "hosted-account"
    try:
        from repowise.cli.platform import auth, credentials

        creds = credentials.load()
        if creds is None:
            return _check(
                name,
                True,
                "Not signed in (optional: repowise login). "
                "Publish this repo free: repowise publish",
                check_id=check_id,
            )
        if creds.get("stale"):
            return _check(
                name,
                False,
                "Session expired or revoked; run repowise login",
                check_id=check_id,
                fix="repowise login",
            )
        account = auth.fetch_account()
        cached = (account or creds.get("account") or {}) or {}
        who = cached.get("github_username") or cached.get("email") or "unknown"
        tier = cached.get("tier") or "free"
        suffix = "" if account else " (offline, using cached identity)"
        return _check(name, True, f"{who} ({tier}){suffix}", check_id=check_id)
    except Exception as e:
        return _check(name, False, str(e), check_id=check_id)


async def _read_stale_counts(repo_path: _DoctorPath) -> dict[str, int]:
    from repowise.core.persistence import get_repository_by_path, get_session

    engine, sf = await open_store(repo_path)
    async with get_session(sf) as session:
        repo = await get_repository_by_path(session, str(repo_path))
        if repo:
            counts = await _stale_page_counts(session, repo.id)
            await engine.dispose()
            return counts
    await engine.dispose()
    return {}


def _stale_pages_row(ctx: CheckContext) -> DoctorCheck:
    """Sets ``ctx.stale_counts`` and ``ctx.stale_count`` for ``--repair``."""
    name, check_id = "Stale pages", "stale-pages"
    try:
        ctx.stale_counts = run_async(_read_stale_counts(ctx.repo_path))
        ctx.stale_count = sum(ctx.stale_counts.values())
        if ctx.stale_count:
            return _check(name, False, _stale_page_guidance(ctx.stale_counts), check_id=check_id)
        return _check(name, True, "0 stale", check_id=check_id)
    except Exception:
        return _check(name, True, "Could not check", check_id=check_id, warn=True)


def provider_checks(ctx: CheckContext) -> list[DoctorCheck]:
    """Checks 5-7, in order."""
    checks = list(_provider_checks(ctx.repo_path))
    checks.append(_hosted_account_check())
    if ctx.db_ok and ctx.page_count > 0:
        checks.append(_stale_pages_row(ctx))
    return checks
