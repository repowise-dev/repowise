/**
 * The parts every agent prompt shares: the per-flavor opening and closing
 * instructions and the bullet formatter the sections are written in.
 */

export type AiPromptFlavor =
  | "generic"
  | "claude-code"
  | "claude-code-mcp"
  | "cursor";

export const FLAVOR_PREAMBLE: Record<AiPromptFlavor, string> = {
  generic:
    "You are a senior engineer working on one file in this repository. The findings below were detected by a static analyzer — treat them as **leads, not ground truth**. Open the file, read its callers, tests, and neighbors, and verify each finding against the actual code before you act. If a finding is a false positive given the broader context, say so and skip it.",
  "claude-code":
    "You are Claude Code working in this repository. The findings below were detected by a static analyzer — treat them as leads to investigate, not commands to execute. Use Read, Grep, and Glob to explore the file, its callers, its tests, and any related modules before planning edits. Verify each finding against the actual code; flag any that turn out to be false positives. Use TodoWrite for non-trivial steps.",
  "claude-code-mcp":
    "You are Claude Code working in this repository, which is indexed by repowise and exposes its MCP tools. The findings below were detected by repowise's static analyzer — treat them as leads to investigate, not commands to execute. Before re-reading files by hand, pull the context repowise already computed: call `get_context([...])` for the file skeleton (every signature + the bodies of the most central symbols, ~37% of a full Read), `get_symbol(\"file::Name\")` for the exact bytes of one function, `get_risk([...])` before editing to see blast radius, co-change partners, and test gaps, and `get_why(...)` for the decision behind the current shape. Fall back to Read / Grep / Glob only for what the index can't serve. Verify each finding against the real code; flag false positives. Use TodoWrite for non-trivial steps.",
  cursor:
    "Work on the file referenced below. The findings below were detected by a static analyzer — treat them as leads, not ground truth. Use @file and @codebase to read the file, its callers, its tests, and neighboring modules before editing. Verify each finding against the real code; skip and call out any false positives.",
};

/**
 * Closing instruction, tailored per flavor. The MCP flavor steers the agent
 * to the repowise tools it already has instead of repeating the exploration
 * repowise did at index time; every other flavor keeps the read-first wording.
 */
type CloserKind = "refactor" | "coverage" | "security" | "hotspot" | "file-health";

const CLOSER_CONFIG: Record<
  CloserKind,
  { mcpSecond: (f: string) => string; mcpInto: string; verb: string; readFirst: string }
> = {
  refactor: {
    mcpSecond: (f) =>
      `\`get_risk(['${f}'])\` for the blast radius, co-change partners, and test gaps`,
    mcpInto: "functions below",
    verb: "propose a fix",
    readFirst:
      "Start by reading the file end-to-end, then explore its callers, tests, and any related helpers. The findings below describe symptoms — the actual root cause may live elsewhere. Don't propose a fix until you've grounded each one in the real code.",
  },
  coverage: {
    mcpSecond: (f) =>
      `\`get_context(['${f}'], include=['callers'])\` to see who exercises it`,
    mcpInto: "functions you'll test",
    verb: "write a test",
    readFirst:
      "Start by reading the file end-to-end, then explore its callers, the existing tests directory, and any sibling files that test similar code. The coverage numbers below come from a static report — verify them by looking at the real test files and the real source. Don't write a test before you've seen the code it's exercising and the project's existing test conventions.",
  },
  security: {
    mcpSecond: (f) =>
      `\`get_risk(['${f}'])\` to see who depends on this code before you touch it`,
    mcpInto: "flagged lines",
    verb: "change anything",
    readFirst:
      "Start by reading the file and the exact lines flagged, then trace how the value flows in and out. The scanner matches patterns — confirm this is actually exploitable in context before you change anything. If it's a false positive (test fixture, sample data, already-sanitized), say so and stop.",
  },
  hotspot: {
    mcpSecond: (f) =>
      `\`get_risk(['${f}'])\` for the co-change partners and test gaps that make this file risky to touch`,
    mcpInto: "most-churned functions",
    verb: "propose changes",
    readFirst:
      "Start by reading the file end-to-end, then look at what it co-changes with and how well it's tested. High churn is a symptom — the goal is to make this file safer and cheaper to change, not to rewrite it. Don't propose changes until you understand why it churns.",
  },
  "file-health": {
    mcpSecond: (f) =>
      `\`get_health(['${f}'])\` for the scored findings behind the numbers below, then \`get_risk(['${f}'])\` for blast radius, co-change partners and test gaps`,
    mcpInto: "functions the findings name",
    verb: "change anything",
    readFirst:
      "Start by reading the file end-to-end, then its callers, its tests, and whatever it changes alongside. The report above spans several independent signals, and they do not all point at the same fix. Work out which ones share a root cause before you touch anything.",
  },
};

/**
 * Closing instruction, tailored per surface. The MCP flavor steers the agent to
 * the repowise tools it already has instead of repeating the exploration
 * repowise did at index time; every other flavor keeps the read-first wording.
 */
export function explorationCloser(
  flavor: AiPromptFlavor,
  filePath: string,
  kind: CloserKind,
): string {
  const cfg = CLOSER_CONFIG[kind];
  if (flavor === "claude-code-mcp") {
    return `Start with \`get_context(['${filePath}'])\` for the skeleton and ${cfg.mcpSecond(
      filePath,
    )}, then \`get_symbol\` into the specific ${cfg.mcpInto}. repowise already indexed this repo — lean on it before falling back to Read/Grep. Don't ${cfg.verb} until you've grounded each finding in the actual code.`;
  }
  return cfg.readFirst;
}

export function bulletList(items: (string | null | undefined | false)[]): string {
  return items.filter(Boolean).map((s) => `- ${s}`).join("\n");
}
