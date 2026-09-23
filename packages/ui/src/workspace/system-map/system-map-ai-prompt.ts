/**
 * AI-agent prompts for the System Map: one service, one relationship, one
 * dependency cycle, one blast radius. Pure string builders in the shape of
 * `health/ai-prompt-builder.ts` (preamble, evidence, tasks, constraints, what
 * to hand back), so the agent can start without asking what we meant.
 *
 * Every figure comes from the system graph the map drew. The prompts say so,
 * and tell the agent to confirm each relationship in code before acting on it.
 */

import type { AiPromptFlavor } from "../../health/ai-prompt-builder";

/** Evidence rows listed in full before the prompt says how many it left out. */
export const SYSTEM_MAP_PROMPT_MAX_ROWS = 25;

const PREAMBLE: Record<AiPromptFlavor, string> = {
  generic:
    "You are a senior engineer working across the repositories of one workspace. The facts below come from repowise's system graph, which is built from code and git history. Treat them as leads, not ground truth: open the files, confirm each relationship in code, and say plainly when one is wrong.",
  "claude-code":
    "You are Claude Code working across the repositories of one workspace. The facts below come from repowise's system graph, which is built from code and git history. Treat them as leads: use Read, Grep and Glob in each repository to confirm every relationship before you rely on it, and say plainly when one is wrong. Use TodoWrite for multi-step work.",
  "claude-code-mcp":
    "You are Claude Code working across the repositories of one workspace, indexed by repowise and exposed through its MCP tools. The facts below come from repowise's system graph. Pull what repowise already computed before reading files by hand: `get_blast_radius` for cross-repo impact, `search_codebase` to find a route, table or symbol by name, `get_context([...])` for a file's skeleton, `get_symbol(\"file::Name\")` for one body, `get_risk([...])` before editing, and `get_why(...)` for the decision behind the current shape. Confirm each relationship in code and say plainly when one is wrong.",
  cursor:
    "Work across the repositories of this workspace. The facts below come from repowise's system graph, which is built from code and git history. Treat them as leads: use @file and @codebase to confirm each relationship in code before acting on it, and say plainly when one is wrong.",
};

function bullets(items: (string | null | undefined | false)[]): string {
  return items.filter(Boolean).map((s) => `- ${s}`).join("\n");
}

function numbered(items: string[]): string {
  return items.map((s, i) => `${i + 1}. ${s}`).join("\n");
}

function countsLine(counts: Record<string, number>): string {
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  return entries.length === 0 ? "none" : entries.map(([t, n]) => `${n} ${t}`).join(", ");
}

function capped<T>(rows: T[], render: (row: T) => string, noun: string): string {
  const shown = rows.slice(0, SYSTEM_MAP_PROMPT_MAX_ROWS).map(render).join("\n");
  const hidden = rows.length - SYSTEM_MAP_PROMPT_MAX_ROWS;
  return hidden > 0 ? `${shown}\n- ...and ${hidden} more ${noun} not listed here.` : shown;
}

function assemble(sections: (string | null | false)[]): string {
  return sections.filter((s): s is string => Boolean(s)).join("\n\n");
}

// ---------------------------------------------------------------------------
// Service
// ---------------------------------------------------------------------------

export interface PromptContractRow {
  contract_id: string;
  role: string;
  repo: string;
  file_path: string;
  line?: number | null;
  link_count?: number;
}

export interface PromptNeighbour {
  id: string;
  name: string;
  kind: string;
  weight: number;
  /** Sentence form of the weight, e.g. "180 endpoints called". */
  weight_label: string;
}

export interface BuildServicePromptOptions {
  service: { id: string; name: string; repo: string; service_path: string | null };
  /** 0-10, already in the product's scale. */
  health?: { value: string; label: string } | null;
  role?: { label: string; reaches: number; reachedBy: number } | null;
  provides: Record<string, number>;
  consumes: Record<string, number>;
  /** Top contracts, ordered by how much rides on them. */
  contracts: PromptContractRow[];
  /** Contracts the service declares; above `contracts.length` when trimmed. */
  contractTotal: number;
  dependsOn: PromptNeighbour[];
  dependedOnBy: PromptNeighbour[];
  unmatched: number;
  unmatchedByReason?: Record<string, number>;
  unusedProviders: number;
  /** Each cycle as the service names in traversal order. */
  cycles: string[][];
  flavor?: AiPromptFlavor;
}

function neighbourLine(n: PromptNeighbour): string {
  return `**${n.name}** (\`${n.id}\`) via ${n.kind}, ${n.weight_label}`;
}

function contractLine(c: PromptContractRow): string {
  const where = `${c.repo}:${c.file_path}${c.line ? `:${c.line}` : ""}`;
  const links =
    c.link_count === undefined ? "" : `, ${c.link_count} matched ${c.link_count === 1 ? "link" : "links"}`;
  return `- \`${c.contract_id}\` (${c.role}) at \`${where}\`${links}`;
}

export function buildServiceAiPrompt(o: BuildServicePromptOptions): string {
  const flavor = o.flavor ?? "generic";
  const s = o.service;
  const location = s.service_path ? `${s.repo} / ${s.service_path}` : s.repo;
  const contractsBlock =
    o.contracts.length === 0
      ? "No contracts were extracted for this service."
      : `${o.contracts.slice(0, SYSTEM_MAP_PROMPT_MAX_ROWS).map(contractLine).join("\n")}${
          o.contractTotal > Math.min(o.contracts.length, SYSTEM_MAP_PROMPT_MAX_ROWS)
            ? `\n- ...${o.contractTotal - Math.min(o.contracts.length, SYSTEM_MAP_PROMPT_MAX_ROWS)} more contracts not listed here.`
            : ""
        }`;

  const reasons = o.unmatchedByReason ? countsLine(o.unmatchedByReason) : null;

  return assemble([
    PREAMBLE[flavor],
    `## Service: ${s.name} (\`${s.id}\`)`,
    bullets([
      `Location: \`${location}\``,
      o.health ? `Repository health: ${o.health.value} out of 10 (${o.health.label})` : null,
      o.role
        ? `Architecture role: ${o.role.label}. It reaches ${o.role.reaches} other ${o.role.reaches === 1 ? "service" : "services"} through structural dependencies, and ${o.role.reachedBy} can reach it.`
        : null,
      `Provides: ${countsLine(o.provides)}`,
      `Consumes: ${countsLine(o.consumes)}`,
      o.unmatched > 0
        ? `Consumers that match no provider in the workspace: ${o.unmatched}${reasons ? ` (${reasons})` : ""}`
        : null,
      o.unusedProviders > 0
        ? `Providers no workspace consumer calls: ${o.unusedProviders}. They may still be called from outside the workspace.`
        : null,
    ]),
    o.cycles.length > 0
      ? `## Dependency cycles through this service\n\n${bullets(o.cycles.map((c) => `${c.join(" -> ")} -> ${c[0]}`))}`
      : null,
    `## Depends on\n\n${o.dependsOn.length ? bullets(o.dependsOn.map(neighbourLine)) : "Nothing in the workspace."}`,
    `## Depended on by\n\n${o.dependedOnBy.length ? bullets(o.dependedOnBy.map(neighbourLine)) : "Nothing in the workspace."}`,
    `## Contracts (most linked first)\n\n${contractsBlock}`,
    "## Task\n\nBefore anyone changes this service, map what it exposes and who would feel a change. Do not edit code.",
    `## What I expect back\n\n${numbered([
      "The public surface: each provided contract that has consumers, with the consuming service and file.",
      "Which dependents would break on a change (structural) and which only tend to change alongside it (co-change).",
      "Any relationship above that the code does not support, with the file you checked.",
      "The riskiest contract to change, and the tests in the consuming repositories that would catch a break.",
    ])}`,
    flavor === "claude-code-mcp"
      ? `Start with \`get_blast_radius(target="${s.id}")\`, then \`get_context\` on the provider files listed above and \`get_risk\` on the ones with the most links.`
      : "Start from the provider files listed above, then search each dependent repository for the routes, symbols and tables they name.",
  ]);
}

// ---------------------------------------------------------------------------
// Edge
// ---------------------------------------------------------------------------

export interface PromptEdgeEvidence {
  contract_id: string;
  provider_repo: string;
  provider_file: string;
  consumer_repo: string;
  consumer_file: string;
  match_type?: string;
  confidence?: number;
}

export interface BuildEdgePromptOptions {
  edge: {
    id: string;
    kind: string;
    match_type: string;
    confidence: number;
    weight: number;
    structural: boolean;
  };
  source: { id: string; name: string };
  target: { id: string; name: string };
  /** One sentence: "frontend calls backend over HTTP." */
  sentence: string;
  weightLabel: string;
  /** Resolved links, when the host has them. */
  evidence: PromptEdgeEvidence[];
  /** Contract ids from the edge itself, used when no links were resolved. */
  refs: string[];
  /** Co-change file pairs, for behavioral edges. */
  pairs?: [string, string | null][];
  /** Cycles this edge sits on, as service names. */
  cycles?: string[][];
  flavor?: AiPromptFlavor;
}

export function buildEdgeAiPrompt(o: BuildEdgePromptOptions): string {
  const flavor = o.flavor ?? "generic";
  const e = o.edge;
  const evidence = o.edge.structural
    ? o.evidence.length > 0
      ? capped(
          o.evidence,
          (l) =>
            `- \`${l.contract_id}\`: provider \`${l.provider_repo}:${l.provider_file}\`, consumer \`${l.consumer_repo}:${l.consumer_file}\``,
          "links",
        )
      : o.refs.length > 0
        ? capped(o.refs, (r) => `- \`${r}\``, "contract ids")
        : "No evidence rows were returned for this edge."
    : (o.pairs ?? []).length > 0
      ? capped(o.pairs ?? [], ([a, b]) => `- \`${a}\`${b ? ` with \`${b}\`` : ""}`, "file pairs")
      : "No file pairs were returned for this edge.";

  const task = e.structural
    ? `Verify this dependency exists in code and document it. For each piece of evidence, find the call, import or query in \`${o.source.name}\` and the declaration in \`${o.target.name}\` it resolves to. Flag any link that is a false match.`
    : `Check whether this co-change hides a real dependency. Files that change together without a declared contract often share an undocumented format, a copied type or a coordinated release. Decide which it is for the pairs above.`;

  return assemble([
    PREAMBLE[flavor],
    `## Relationship: ${o.source.name} -> ${o.target.name} (\`${e.id}\`)`,
    bullets([
      o.sentence,
      `Kind: ${e.kind}. Match: ${e.match_type}. Confidence: ${Math.round(e.confidence * 100)}%.`,
      `Weight: ${o.weightLabel}.`,
      e.structural
        ? "Structural: built from a contract or import in code. Changing the provider side can break the consumer."
        : "Behavioral: built from git history. It says the files change together, not that one calls the other.",
    ]),
    o.cycles && o.cycles.length > 0
      ? `## This edge closes a dependency cycle\n\n${bullets(o.cycles.map((c) => `${c.join(" -> ")} -> ${c[0]}`))}`
      : null,
    `## Evidence\n\n${evidence}`,
    `## Task\n\n${task}`,
    `## What I expect back\n\n${numbered(
      e.structural
        ? [
            "Each confirmed link as consumer file and symbol, provider file and symbol.",
            "Any link that does not hold, and why.",
            "A short paragraph for the consumer repository's docs describing this dependency and what a breaking change on the provider side looks like.",
            "Tests on the consumer side that exercise it, or a note that none do.",
          ]
        : [
            "For each file pair: a real shared dependency, a coincidence of workflow, or unknown.",
            "If there is a hidden dependency, the contract that should be declared and where.",
          ],
    )}`,
    flavor === "claude-code-mcp"
      ? `Use \`search_codebase\` for each contract id in both repositories, \`get_context\` on the files above, and \`get_blast_radius(target="${o.target.id}")\` to see what else rides on the provider.`
      : "Open both sides of each piece of evidence before you write anything.",
  ]);
}

// ---------------------------------------------------------------------------
// Cycle
// ---------------------------------------------------------------------------

export interface PromptCycleEdge {
  id: string;
  source: string;
  target: string;
  kind: string;
  weight_label: string;
  /** A few contract ids that make up the edge. */
  refs: string[];
}

export interface BuildCyclePromptOptions {
  /** Service names in traversal order. */
  names: string[];
  ids: string[];
  edges: PromptCycleEdge[];
  flavor?: AiPromptFlavor;
}

export function buildCycleAiPrompt(o: BuildCyclePromptOptions): string {
  const flavor = o.flavor ?? "generic";
  const loop = `${o.names.join(" -> ")} -> ${o.names[0] ?? ""}`;
  const edges = o.edges
    .map((e) => {
      const refs = e.refs.slice(0, 8).map((r) => `\`${r}\``).join(", ");
      const more = e.refs.length > 8 ? ` and ${e.refs.length - 8} more` : "";
      return `- **${e.source} -> ${e.target}** (${e.kind}, ${e.weight_label}, \`${e.id}\`)${refs ? `: ${refs}${more}` : ""}`;
    })
    .join("\n");

  return assemble([
    PREAMBLE[flavor],
    `## Dependency cycle: ${loop}`,
    bullets([
      `Services: ${o.ids.map((id) => `\`${id}\``).join(", ")}`,
      "A cycle means none of these services can change its contract, build or deploy without the others. It counts against the workspace architecture score.",
    ]),
    `## Edges in the cycle\n\n${edges}`,
    "## Task\n\nPropose how to break this cycle. Usually one edge is the odd one out: a callback, a webhook, a shared type or a revalidation hook that points the wrong way. Find it, confirm it in code, and pick a fix: invert it (events or a callback interface), move the shared piece into a package both depend on, or route it through a service already downstream of both.",
    `## Hard constraints\n\n${bullets([
      "Preserve behavior. Keep every public contract stable until its consumers have moved.",
      "Change one edge at a time so each step is reviewable and revertible.",
      "Do not break the cycle by deleting a feature.",
    ])}`,
    `## What I expect back\n\n${numbered([
      "The edge you would cut, with the exact files and symbols that create it.",
      "The fix and why it beats the alternatives.",
      "The change plan across repositories, in merge order.",
      "Tests that would catch the cycle coming back.",
    ])}`,
    flavor === "claude-code-mcp"
      ? `Run \`get_blast_radius\` on each of ${o.ids.map((id) => `\`${id}\``).join(", ")} and \`search_codebase\` for the contract ids above before choosing the edge to cut.`
      : "Locate the code behind each edge before choosing which one to cut.",
  ]);
}

// ---------------------------------------------------------------------------
// Blast radius
// ---------------------------------------------------------------------------

export interface PromptImpacted {
  id: string;
  name: string;
  repo: string;
  distance: number;
  score: number;
  structural: boolean;
  edge_kinds: string[];
}

export interface BuildBlastRadiusPromptOptions {
  target: { id: string; name: string };
  impacted: PromptImpacted[];
  includeBehavioral: boolean;
  flavor?: AiPromptFlavor;
}

export function buildBlastRadiusAiPrompt(o: BuildBlastRadiusPromptOptions): string {
  const flavor = o.flavor ?? "generic";
  const structural = o.impacted.filter((n) => n.structural);
  const behavioral = o.impacted.filter((n) => !n.structural);
  const row = (n: PromptImpacted) =>
    `- **${n.name}** (\`${n.id}\`, repo ${n.repo}): ${n.distance} ${n.distance === 1 ? "hop" : "hops"}, impact ${n.score.toFixed(2)}, via ${n.edge_kinds.join(", ")}`;

  return assemble([
    PREAMBLE[flavor],
    `## Blast radius of ${o.target.name} (\`${o.target.id}\`)`,
    bullets([
      `${o.impacted.length} downstream ${o.impacted.length === 1 ? "service is" : "services are"} reachable: ${structural.length} through structural dependencies, ${behavioral.length} only through co-change.`,
      o.includeBehavioral
        ? "Co-change paths are included at half weight. They mean the files change together, not that one calls the other."
        : "Co-change paths are excluded; every service below is reached through contracts or imports.",
      "Impact is 0 to 1, with distance decay: nearer and structural ranks higher.",
    ]),
    structural.length ? `## Will break (structural)\n\n${capped(structural, row, "services")}` : null,
    behavioral.length ? `## May drift (co-change only)\n\n${capped(behavioral, row, "services")}` : null,
    `## Task\n\nA change to ${o.target.name} is planned. For each structurally impacted service, find the exact contracts it uses from ${o.target.name} and the tests that exercise them. For co-change services, say whether they need a coordinated change or can be left alone.`,
    `## What I expect back\n\n${numbered([
      "Per impacted service: the contracts it depends on, with consumer files.",
      "The tests to run in each repository before merging.",
      "The order to land changes across repositories so nothing breaks in between.",
    ])}`,
    flavor === "claude-code-mcp"
      ? `Confirm with \`get_blast_radius(target="${o.target.id}")\`, then \`search_codebase\` and \`get_context\` in each impacted repository.`
      : "Search each impacted repository for the routes, symbols and tables it takes from the target before planning.",
  ]);
}
