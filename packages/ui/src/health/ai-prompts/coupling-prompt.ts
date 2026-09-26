import { bulletList, FLAVOR_PREAMBLE, type AiPromptFlavor } from "./shared";

// ─────────────────────────────────────────────────────────────────────
// Coupling decouple prompt (per co-change pair)
// ─────────────────────────────────────────────────────────────────────

export interface CouplingPromptEdge {
  source: string;
  target: string;
  strength?: number | null;
  last_co_change?: string | null;
  /** Commits that touched both files, undecayed. */
  support?: number | null;
  /** Share of `source`'s own commits that also touched `target`. */
  confidence_ab?: number | null;
  /** The same share from `target`'s side. */
  confidence_ba?: number | null;
  /** `corroborated` / `unexplained` / `not_applicable`, or absent on an older index. */
  structural?: string | null;
}

/** What the page already knows about one end of the pair. */
export interface CouplingPromptNode {
  module?: string | null;
  score?: number | null;
  nloc?: number | null;
}

export interface BuildCouplingPromptOptions {
  edge: CouplingPromptEdge;
  flavor?: AiPromptFlavor;
  repoName?: string;
  /** Per-file facts keyed by path; either end may be missing. */
  nodes?: Record<string, CouplingPromptNode>;
}

/** The dependency-graph verdict, spelled out. `null` when the index has none. */
function structuralLine(structural: string | null | undefined): string | null {
  switch (structural) {
    case "unexplained":
      return "Dependency graph: **nothing connects them** — no import, type use, framework wiring, or read. This is the finding: they move together with no structural reason to.";
    case "corroborated":
      return "Dependency graph: a dependency already connects them, so the co-change is at least partly explained. Judge whether the dependency is the *right* one before treating this as accidental.";
    case "not_applicable":
      return "Dependency graph: at least one side was never parsed (a lockfile, changelog, config, or doc), so there was no edge to look for. The coupling is real but is probably release plumbing, not a code-structure problem.";
    default:
      return null;
  }
}

/** One end's module / health / size, as a bullet, when anything is known. */
function fileFacts(path: string, label: string, node: CouplingPromptNode | undefined): string {
  const facts: string[] = [];
  if (node?.module) facts.push(`module \`${node.module}\``);
  if (typeof node?.score === "number") facts.push(`health ${node.score.toFixed(1)}/10`);
  if (typeof node?.nloc === "number" && node.nloc > 0) facts.push(`${node.nloc} lines`);
  return facts.length
    ? `File ${label}: \`${path}\` (${facts.join(", ")})`
    : `File ${label}: \`${path}\``;
}

export function buildCouplingAiPrompt({
  edge,
  flavor = "generic",
  repoName,
  nodes,
}: BuildCouplingPromptOptions): string {
  const repoLine = repoName ? ` (\`${repoName}\`)` : "";
  const last = edge.last_co_change
    ? new Date(edge.last_co_change).toISOString().slice(0, 10)
    : null;
  const pctOf = (v: number | null | undefined) =>
    typeof v === "number" ? `${Math.round(v * 100)}%` : null;
  const abPct = pctOf(edge.confidence_ab);
  const baPct = pctOf(edge.confidence_ba);

  const constraintList = [
    "**Diagnose before decoupling.** Read both files and the commits that touched them together. The coupling may be legitimate (two halves of one feature) or accidental (a leaky abstraction, a shared constant, copy-paste). Name which it is before acting.",
    "If it's accidental, fix the cause: extract the shared concept into one owner, invert the dependency, or introduce a stable interface — don't just move code around.",
    "If it's legitimate and unavoidable, say so and stop. Forcing a split that the domain doesn't support makes things worse.",
    "Preserve behavior. This is a structural change, not a feature change.",
    "Add or update tests so the new boundary is exercised and the old hidden contract can't silently regress.",
  ];

  const completionContract = [
    "1. A verdict: is this coupling accidental or legitimate, and what's the underlying shared concern?",
    "2. If accidental — a concrete decoupling plan (extract / invert / interface), smallest-risk first.",
    "3. The first change, scoped and behavior-preserving, with its tests.",
    "4. If legitimate — the reason to leave it, and any lighter-touch improvement (docs, a shared module) worth doing instead.",
  ];

  const closer =
    flavor === "claude-code-mcp"
      ? `Call \`get_risk(['${edge.source}'])\` and \`get_context(['${edge.source}', '${edge.target}'])\` to see what else each file pulls in before you plan the split — repowise already mapped the dependency graph and the co-change history. Don't restructure until you know why they move together.`
      : "Start by reading both files and `git log` for the commits that changed them together. The co-change count is a symptom; find the shared concern driving it before you restructure anything.";

  return [
    FLAVOR_PREAMBLE[flavor],
    "",
    `## Hidden coupling to untangle${repoLine}`,
    "",
    bulletList([
      fileFacts(edge.source, "A", nodes?.[edge.source]),
      fileFacts(edge.target, "B", nodes?.[edge.target]),
      edge.support
        ? `Shared commits: **${edge.support}** (undecayed count of commits that touched both)`
        : null,
      // The asymmetry is the content: a file that never changes alone is a
      // different finding from two that both change often.
      abPct && baPct
        ? `Directional confidence: ${abPct} of A's own commits also touched B; ${baPct} of B's also touched A. The larger share is the stronger claim; the smaller one says how independent that side still is.`
        : (abPct ?? baPct)
          ? `Directional confidence: ${abPct ? `${abPct} of A's own commits also touched B` : `${baPct} of B's own commits also touched A`} (the other side's commit total is unknown).`
          : null,
      structuralLine(edge.structural),
      edge.strength != null
        ? `Coupling strength: ${edge.strength} (recency-weighted, not a percentage and not a verified dependency)`
        : null,
      last ? `Last changed together: ${last}` : null,
      "Source: repowise co-change analysis (git history — treat as a lead).",
    ]),
    "",
    "## Hard constraints",
    "",
    bulletList(constraintList),
    "",
    "## What I expect back",
    "",
    completionContract.join("\n"),
    "",
    closer,
  ]
    .filter((s) => s !== "")
    .join("\n");
}
