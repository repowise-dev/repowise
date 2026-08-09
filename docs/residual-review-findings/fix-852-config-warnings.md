# Residual Review Findings

Run context: LFG pipeline for issue #852 (config warnings), branch `fix/852-config-warnings`, plan `docs/plans/2026-08-09-cli-config-warning-surfacing.md`. Review: ce-code-review (correctness, testing, maintainability, reliability, adversarial in-process — no cross-model peer: host not attestable). Findings below are report-only/advisory residuals not applied by the pipeline.

## Residual Review Findings

- P2 · `packages/cli/src/repowise/cli/providers/embedders.py` (maintainability) — Warning markup re-spelled at 5+ sites (`[yellow]Warning:[/yellow]`) with `console` vs the canonical `err_console` stream at `helpers.py:773`; add a shared `warn()` helper and unify the stream. https://github.com/repowise-dev/repowise/issues/1368
- P2 · `packages/cli/src/repowise/cli/commands/init_cmd/command.py:1214` (adversarial) — The init header probes the embedder and warns, but the generation phase rebuilds it; if the rebuild fails later (e.g. local Ollama dies mid-run) the degradation is silent. Thread the built instance or move the check into the shared generation entry. https://github.com/repowise-dev/repowise/issues/1369
- P2 · `packages/cli/src/repowise/cli/commands/update_cmd/deterministic.py:171-175` (correctness) — Update's deterministic page path builds an embedder without a degradation warning; R3 coverage is partial (workspace init, reindex, generate, upgrade_flow also unwarned). https://github.com/repowise-dev/repowise/issues/1370
- P3 · `packages/cli/src/repowise/cli/commands/update_cmd/persistence.py:1071-1076` (adversarial) — `REPOWISE_FULL_RESCORE_INTERVAL_DAYS=abc` warns once per repo in workspace/watch/commit-triggered updates (KTD3 one-shot per invocation is violated by the workspace fan-out loop). https://github.com/repowise-dev/repowise/issues/1371
- P3 · `packages/cli/src/repowise/cli/commands/update_cmd/reporting.py:161-178` (adversarial) — The degraded panel promises "(will retry on the next update)" but a config error (embedder degradation) will not self-heal on retry; reword the panel or split config errors into a separate channel. https://github.com/repowise-dev/repowise/issues/1372
