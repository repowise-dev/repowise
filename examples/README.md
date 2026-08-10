# Examples

Copy-paste walkthroughs for common Repowise setups. Each example is a
self-contained README under `examples/<name>/`.

## Available examples

| Example | What it shows |
|---------|----------------|
| [codex/](codex/) | Codex CLI setup: `repowise init --codex`, smoke checks, local plugin install |
| [dead-code/](dead-code/) | `repowise dead-code` — unused exports and cleanup candidates (no LLM key) |
| [decisions/](decisions/) | `repowise decision` list/health/confirm — architectural records (no LLM key) |
| [distill/](distill/) | `distill` / `expand` / `saved` — compress command output (no LLM key) |
| [health-coverage/](health-coverage/) | Code health, coverage ingest, and impacted-tests (no LLM key) |
| [hooks-sync/](hooks-sync/) | `hook install` + `watch` — keep the index fresh (no LLM key) |
| [opencode/](opencode/) | OpenCode as the LLM provider for wiki generation |
| [risk/](risk/) | `repowise risk` change / PR defect scoring (no LLM key) |
| [search/](search/) | `repowise search` fulltext / symbol / semantic wiki lookup |
| [security-scan/](security-scan/) | Working-tree security signals + OSS `security scan --history` (no LLM key) |
| [structurizr-export/](structurizr-export/) | `export --format structurizr` fragment vs standalone (no LLM key) |
| [wiki-export/](wiki-export/) | `export` markdown / html / json wiki pages (no LLM key) |

## Conventions

- One directory per topic: `examples/<name>/README.md`.
- Prefer real CLI commands that match `docs/reference/CLI_REFERENCE.md`.
- Link deeper docs instead of duplicating them.
- Keep smoke checks short and runnable without inventing fake APIs.

## Related docs

- [Quickstart](../docs/start/QUICKSTART.md)
- [CLI reference](../docs/reference/CLI_REFERENCE.md)
- [Codex integration](../docs/agent/CODEX.md)
- [OpenCode integration](../docs/agent/OPENCODE.md)
- [Code health](../docs/layers/CODE_HEALTH.md)
- [Dead code](../docs/layers/DEAD_CODE.md)
- [Decisions](../docs/layers/DECISIONS.md)
- [Change risk](../docs/layers/CHANGE_RISK.md)
- [Test intelligence](../docs/layers/TEST_INTELLIGENCE.md)
- [Distill](../docs/agent/DISTILL.md)
- [Auto-sync](../docs/scale/AUTO_SYNC.md)
- [Structurizr export](../docs/architecture/structurizr-export.md)
- [CLI: security](../docs/reference/CLI_REFERENCE.md)
