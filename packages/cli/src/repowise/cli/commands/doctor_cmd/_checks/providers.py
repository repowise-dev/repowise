"""Checks 5-7: providers, hosted account, stale pages."""

from __future__ import annotations

from pathlib import Path as _DoctorPath

from repowise.cli.commands._page_freshness import WHOLE_WIKI_REMEDY
from repowise.cli.helpers import run_async

from .._types import DoctorCheck, _check
from .context import CheckContext, open_store


async def _stale_page_counts(session: object, repo_id: str) -> dict[str, int]:
    """Count stale pages by type without hydrating their rendered content.

    A model-free page is filed under ``model_free``, not its type: no model writes one.
    """
    from sqlalchemy import func, select

    from repowise.core.persistence.models import Page

    from ..._page_freshness import stale_model_free_counts

    stale = (Page.repository_id == repo_id, Page.freshness_status.in_(["stale", "expired"]))
    result = await session.execute(  # type: ignore[attr-defined]
        select(Page.page_type, func.count()).where(*stale).group_by(Page.page_type)
    )
    counts = {str(page_type): int(count) for page_type, count in result.all()}
    for page_type, n in (await stale_model_free_counts(session, stale)).items():
        counts[page_type] -= n
        counts["model_free"] = counts.get("model_free", 0) + n
    return counts


# What `repowise update --full` does, by `stale_remedy`. A model wiki's text is
# completed in `_stale_page_guidance`, only when a model-written page is stale.
_STALE_REMEDY = {
    "llm": "refreshes them",
    "model": WHOLE_WIKI_REMEDY,
    "code": "rebuilds them from code, with no model calls",
}


def _stale_page_guidance(counts: dict[str, int], remedy: str | None) -> str:
    """Describe stale work by owner and name commands that can clear it.

    ``remedy`` is ``stale_remedy``'s answer, or ``None`` to name no command.
    """
    from repowise.core.generation.models import MODEL_WRITTEN_PAGE_TYPES

    total = sum(counts.values())
    model_written = sum(
        count for page_type, count in counts.items() if page_type in MODEL_WRITTEN_PAGE_TYPES
    )
    structural = total - model_written
    categories = []
    if model_written:
        # Only a wiki a model wrote has pages a model wrote.
        label = "model-written" if remedy == "llm" else "of a type a model writes"
        categories.append(f"{model_written} {label}")
    if structural:
        categories.append(f"{structural} structural")

    detail = (
        f"{total} stale ({', '.join(categories)}) — content no longer matches its "
        "current inputs or generation selection."
    )
    if remedy:
        detail += f" `repowise update --full` {_STALE_REMEDY[remedy]}"
        if model_written and remedy == "llm":
            detail += ", pricing the model-written ones before it rewrites them"
        detail += "."
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
    llm_detail = "Resolves for this repository" if llm_available else _no_provider_detail(repo_path)
    # Keyless operation is supported, so this is informational rather than a
    # failing health check.  The detail tells users what init will actually do.
    checks.append(_check("LLM provider", True, llm_detail, check_id="llm-provider"))
    extra_row = _provider_extra_check(repo_path)
    if extra_row is not None:
        checks.append(extra_row)
    embedder_row = _embedder_package_check(repo_path)
    if embedder_row is not None:
        checks.append(embedder_row)
    return checks


def _provider_extra_check(repo_path: _DoctorPath) -> DoctorCheck | None:
    """A failing row when the chosen provider's ``repowise[<extra>]`` is missing, else none.

    An upgrade that moved a provider's package out of the default install
    leaves the saved choice in place and the package gone; the row names the
    one command that brings it back.
    """
    from repowise.core.extras import missing_message, plan_install
    from repowise.core.providers.llm.registry import provider_missing_extra
    from repowise.core.providers.llm.resolve import resolve_llm

    try:
        provider = resolve_llm(repo_path).provider
    except Exception:
        return None  # the provider-config row reports a broken config
    extra = provider_missing_extra(provider) if provider else None
    if extra is None:
        return None
    return _check(
        "LLM provider package",
        False,
        missing_message(extra, f"The {provider} provider"),
        check_id="provider-extra",
        fix=plan_install(extra).command,
    )


def _embedder_package_check(repo_path: _DoctorPath) -> DoctorCheck | None:
    """A warning when the repo's embedder cannot be imported here, else no row.

    An optional extra (``local-embed`` for fastembed) installed where the index
    was built but not where the MCP server or ``update`` runs leaves search
    without its vectors; the install line is the fix.
    """
    from repowise.cli.providers import resolve_embedder_for_repo
    from repowise.core.providers.embedding.registry import (
        _BUILTIN_EMBEDDERS,
        import_builtin_embedder,
    )

    name = resolve_embedder_for_repo(repo_path)
    if name not in _BUILTIN_EMBEDDERS:
        return None
    try:
        import_builtin_embedder(name)
    except ImportError as exc:
        return _check("Embedder", True, str(exc), check_id="embedder-package", warn=True)
    return None


def _no_provider_detail(repo_path: _DoctorPath) -> str:
    """The shared upgrade line, naming a signed-in agent CLI on a paid plan when there is one."""
    from repowise.cli.ui.subscription import upgrade_route
    from repowise.core.index_scope import load_index_scope, upgrade_line

    return f"None configured. {upgrade_line(load_index_scope(repo_path), upgrade_route(repo_path))}"


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
                True,
                "Session expired or revoked; run repowise login",
                check_id=check_id,
                warn=True,
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
    """Sets ``ctx.stale_guidance`` for ``--repair``."""
    name, check_id = "Stale pages", "stale-pages"
    try:
        counts = run_async(_read_stale_counts(ctx.repo_path))
        if sum(counts.values()):
            from ..._page_freshness import stale_remedy

            # Without a readable state.json `update --full` stops; the state.json row names the fix.
            remedy = stale_remedy(ctx.state, ctx.repo_path) if ctx.state_ok else None
            ctx.stale_guidance = _stale_page_guidance(counts, remedy)
            return _check(name, False, ctx.stale_guidance, check_id=check_id)
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
