import { deadCodeRiskFactorLabel } from "@repowise-dev/types/dead-code";

import { bulletList, FLAVOR_PREAMBLE, type AiPromptFlavor } from "./shared";

// ─────────────────────────────────────────────────────────────────────
// Dead-code cleanup prompt (bulk — the safe-to-delete pile)
// ─────────────────────────────────────────────────────────────────────

export interface DeadCodePromptFinding {
  file_path: string;
  symbol_name?: string | null;
  kind?: string | null;
  reason?: string | null;
  lines?: number | null;
  confidence?: number | null;
  risk_factors?: string[] | null;
}

export interface BuildDeadCodePromptOptions {
  findings: DeadCodePromptFinding[];
  flavor?: AiPromptFlavor;
  repoName?: string;
}

// Cap the file list so a big cleanup pile doesn't produce a giant prompt; the
// tail is summarized so the agent still knows the full scope.
const MAX_DEAD_CODE_FILES = 20;

export function buildDeadCodeAiPrompt({
  findings,
  flavor = "generic",
  repoName,
}: BuildDeadCodePromptOptions): string {
  const repoLine = repoName ? ` (\`${repoName}\`)` : "";

  const byFile = new Map<string, DeadCodePromptFinding[]>();
  for (const f of findings) {
    byFile.set(f.file_path, [...(byFile.get(f.file_path) ?? []), f]);
  }
  const files = Array.from(byFile.entries()).sort(
    (a, b) =>
      b[1].reduce((s, f) => s + (f.lines ?? 0), 0) -
      a[1].reduce((s, f) => s + (f.lines ?? 0), 0),
  );
  const shown = files.slice(0, MAX_DEAD_CODE_FILES);
  const hidden = files.slice(MAX_DEAD_CODE_FILES);
  const totalLines = findings.reduce((s, f) => s + (f.lines ?? 0), 0);

  const fileBlock = shown
    .map(([path, fs]) => {
      const symbols = fs.map((f) => f.symbol_name).filter(Boolean).join(", ");
      const kinds = Array.from(new Set(fs.map((f) => f.kind).filter(Boolean)));
      const reason = fs.map((f) => f.reason).filter(Boolean)[0];
      const risk = Array.from(
        new Set(fs.flatMap((f) => f.risk_factors ?? [])),
      );
      return [
        `- \`${path}\`${symbols ? ` — ${symbols}` : ""}`,
        kinds.length ? `  - Kind: ${kinds.join(", ")}` : null,
        reason ? `  - Why flagged: ${reason}` : null,
        risk.length
          ? `  - Runtime-load risk to rule out first: ${risk.map(deadCodeRiskFactorLabel).join(", ")}`
          : null,
      ]
        .filter(Boolean)
        .join("\n");
    })
    .join("\n");

  const hiddenLine =
    hidden.length > 0
      ? `…and ${hidden.length} more file${hidden.length === 1 ? "" : "s"} in the same pile (open the dead-code report in repowise for the full list).`
      : null;

  const constraintList = [
    "**Verify before deleting.** Each entry was flagged by static analysis, not proven dead. Search the whole repo (including config, DI containers, string-based imports, templates, and tests) for every symbol before removing it.",
    "Watch for dynamic access: reflection, `getattr`/`importlib`, dependency-injection registries, plugin discovery, serialization, and public-API re-exports can use code that looks unreferenced.",
    "Delete in small, reviewable commits grouped by area — not one giant sweep. Keep each commit independently revertible.",
    "Run the full test suite (and a build/type-check) after each group. If anything fails, the symbol wasn't dead — restore it and note why.",
    "Remove now-orphaned imports, fixtures, and tests that only existed for the deleted code.",
    "If a finding turns out to be reachable, mark it as a false positive in your summary instead of forcing the deletion.",
  ];

  const completionContract = [
    "1. A short plan grouping the deletions into safe, independently-revertible commits.",
    "2. The deletions themselves, with the cross-repo search you ran to confirm each one is unused.",
    "3. The test/build result after each group.",
    "4. A list of any findings you skipped as false positives, with the reference that kept them alive.",
  ];

  return [
    FLAVOR_PREAMBLE[flavor],
    "",
    `## Dead-code cleanup${repoLine}`,
    "",
    bulletList([
      `Files in this pile: **${files.length}**`,
      `Estimated reclaimable lines: **${totalLines.toLocaleString()}**`,
      "Source: repowise dead-code analysis (high-confidence, safe-to-delete tier).",
    ]),
    "",
    "## Files to clean up (largest first)",
    "",
    fileBlock,
    hiddenLine ?? "",
    "",
    "## Hard constraints",
    "",
    bulletList(constraintList),
    "",
    "## What I expect back",
    "",
    completionContract.join("\n"),
    "",
    flavor === "claude-code-mcp"
      ? "For each file, call `get_risk([...])` to see who still imports it and `get_context([...])` for its exported surface before deleting — repowise already mapped the dependency graph, so use it instead of grepping blind. A file with live dependents is not dead; surface that and skip it."
      : "Start with the largest files. For each, run a repo-wide search for its name and every exported symbol before you delete anything — the analyzer can't see dynamic or string-based references. A file with live dependents is not dead; skip it and say so.",
  ]
    .filter((s) => s !== "")
    .join("\n");
}
