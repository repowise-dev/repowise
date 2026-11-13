"""Select a bounded stale template batch from the generator's native census."""

from __future__ import annotations

import hashlib
from typing import Any

from .. import onboarding
from ..models import compute_page_id
from ..scope import selection_page_ids
from . import levels
from .reuse import files_digest

RENDER_REVISION = "render_revision"


async def select_render_batch(run: Any, budget: int, changed_paths: set[str]) -> None:
    """Keep current pages and select changed subjects before renderer-only work."""
    candidates = selection_page_ids(run.selection, run.repo_name)
    await levels.assemble_file_contexts(run)
    subjects: dict[str, set[str]] = {}
    heads = levels._heads(run.sel_module_groups)
    for group in run.sel_module_groups:
        pid = compute_page_id("module_page", group.key)
        material = set(
            getattr(group, "context_paths", ()) or group.file_paths
        )
        contexts = [run.file_page_contexts[path] for path in material
                    if path in run.file_page_contexts]
        has_parts = bool(getattr(group, "is_rollup", False) or
                         levels._part_pages(group, run.sel_module_groups, heads))
        if not contexts or levels._is_empty_module(contexts, has_parts):
            candidates.discard(pid)
        subjects[pid] = material
    for key, members in run.sel_scc_groups:
        subjects[compute_page_id("scc_page", key)] = set(members)
    if not run.config.file_pages_only:
        for plan in run.flow_plans():
            candidates.add(plan.page_id)
            subjects[plan.page_id] = set(plan.files)
        if run.config.enable_onboarding:
            specs = onboarding.iter_specs()
            signals = await levels._onboarding_signals(run, specs)
            for spec in specs:
                if spec.build_context(signals) is not None:
                    candidates.add(compute_page_id("onboarding", onboarding.target_path(spec.slot)))
    else:
        candidates = {pid for pid in candidates if pid.split(":", 1)[0] in {
            "file_page", "symbol_spotlight", "api_contract", "infra_page",
        }}
    if run.only_page_ids is not None:
        candidates &= run.only_page_ids

    templates = ["file_page.j2", "symbol_spotlight.j2", "api_contract.j2",
                 "infra_page.j2", "scc_page.j2", "stub/module_page.j2",
                 "stub/repo_overview.j2", "stub/flow_page.j2"]
    templates.extend(f"stub/onboarding/{spec.template}" for spec in onboarding.iter_specs())
    from ...repo_config import config_dependency_fingerprints

    generation = (
        config_dependency_fingerprints(run.repo_path)["generation"] if run.repo_path else ""
    )
    renderer = hashlib.sha256("\x00".join(
        [generation, *(run.gen._structural_fingerprint(template) for template in templates)]
    ).encode()).hexdigest()
    all_paths = set(run.source_map)
    run.render_revisions = {}
    stale = []
    prior_pages = run.gen._prior_pages or {}
    source_digests: dict[frozenset[str], str] = {}
    for pid in sorted(candidates):
        kind, target = pid.split(":", 1)
        paths = subjects.get(pid)
        if paths is None:
            paths = {target.split("::", 1)[0]} if kind in {
                "file_page", "symbol_spotlight", "api_contract", "infra_page",
            } else all_paths
        subject = frozenset(paths)
        if subject not in source_digests:
            source_digests[subject] = hashlib.sha256(
                files_digest(paths, run.source_map).encode()
            ).hexdigest()
        revision = {"renderer": renderer, "source": source_digests[subject]}
        run.render_revisions[pid] = revision
        prior = prior_pages.get(pid)
        if prior is not None and prior.provider_name != "template":
            # A template catch-up cannot replace prose a model already wrote.
            continue
        if (prior is not None and prior.provider_name == "template"
                and prior.freshness_status == "fresh"
                and prior.metadata.get(RENDER_REVISION) == revision):
            continue
        previous = prior.metadata.get(RENDER_REVISION, {}) if prior is not None else {}
        source_changed = bool(paths & changed_paths) or (
            bool(previous) and previous.get("source") != revision["source"]
        )
        stale.append((not source_changed, pid))
    selected = {pid for _, pid in sorted(stale)[:budget]}
    run.only_page_ids = selected
    run.gen.render_selected_ids = selected
    run.gen.render_backlog = len(stale) - len(selected)
    run.gen.render_page_ids = candidates | {
        pid for pid, prior in prior_pages.items()
        if prior is not None and prior.provider_name != "template"
    }
    run.gen.render_stale_ids = {pid for _, pid in stale}
    for pid in candidates:
        run.gen._prior_pages.setdefault(pid, None)
    from types import SimpleNamespace

    from ..interlinking import LinkIndex

    links = [SimpleNamespace(page_id=pid, page_type=pid.split(":", 1)[0],
                             target_path=pid.split(":", 1)[1]) for pid in sorted(candidates)]
    run.render_link_index = LinkIndex.build(links, run.parsed_files)
    run.render_link_index.add_prior_page_ids(run.gen._prior_pages)
    if run.render_stats is not None:
        run.render_stats["render_stale_ids"] = run.gen.render_stale_ids
