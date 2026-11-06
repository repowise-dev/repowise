/**
 * The agent prompt for two services: review every route between them, both
 * ways, from the rows the routes panel loaded. Same shape and preamble as the
 * other System Map prompts (`system-map-ai-prompt.ts`). Pure string builder.
 */

import type { ServiceLinkEntry } from "@repowise-dev/types/workspace";
import { bulletList, joinSections, numbered, preamble, type AiPromptFlavor } from "../../health/ai-prompts/shared";
import { SYSTEM_MAP_PREAMBLE, capped, matchedBy } from "./system-map-ai-prompt";
import { routeTitle } from "./routes-between";

export interface BuildPairPromptOptions {
  a: { id: string; name: string };
  b: { id: string; name: string };
  /** The lede sentences the panel shows, from the graph the map drew. */
  facts: string[];
  /** Rows loaded so far, every kind listed. */
  rows: ServiceLinkEntry[];
  /** Rows the listed kinds hold on the server; above `rows.length` when not all pages were loaded. */
  total: number;
  flavor?: AiPromptFlavor;
}

function rowLine(row: ServiceLinkEntry, aName: string, bName: string): string {
  const way = row.direction === "a_to_b" ? `${aName} -> ${bName}` : row.direction === "b_to_a" ? `${bName} -> ${aName}` : "co-change";
  const l = row.link;
  if (l) {
    return `- [${row.kind}, ${way}] \`${l.contract_id}\`: consumer \`${l.consumer_repo}:${l.consumer_file}\`, provider \`${l.provider_repo}:${l.provider_file}\`${matchedBy(l.strategy)}`;
  }
  const d = row.package_dep;
  if (d) return `- [package, ${way}] \`${routeTitle(row)}\` declared in \`${d.source_repo}:${d.source_manifest}\``;
  const c = row.co_change;
  if (c) return `- [co-change] \`${c.source_repo}:${c.source_file}\` with \`${c.target_repo}:${c.target_file}\`, ${c.frequency} times`;
  return `- [${row.kind}, ${way}] ${routeTitle(row)}`;
}

export function buildPairAiPrompt(o: BuildPairPromptOptions): string {
  const flavor = o.flavor ?? "generic";
  const { a, b } = o;
  const unlisted = o.total - o.rows.length;
  const routes =
    o.rows.length === 0
      ? "No route rows were loaded; list the contracts between the two services before you start."
      : `${capped(o.rows, (r) => rowLine(r, a.name, b.name), "routes")}${
          unlisted > 0 ? `\n- ${unlisted} more routes exist that were not loaded here.` : ""
        }`;
  return joinSections([
    preamble(flavor, SYSTEM_MAP_PREAMBLE),
    `\n## Contract between ${a.name} (\`${a.id}\`) and ${b.name} (\`${b.id}\`)`,
    `\n${bulletList(o.facts)}`,
    `\n## Routes\n\n${routes}`,
    `\n## Task\n\nReview the contract between ${a.name} and ${b.name}. Confirm each route above in code on both sides, flag any that is a false match, and describe what each service expects of the other. Do not edit code.`,
    `\n## What I expect back\n\n${numbered([
      "Each confirmed route as consumer file and symbol, provider file and symbol.",
      "Any route that does not hold, with the file you checked.",
      "Routes either side uses that are missing above, if you find any.",
      "What a breaking change on each side would look like, and the tests that would catch it.",
    ])}`,
    flavor === "claude-code-mcp"
      ? `\nUse \`search_codebase\` for each route in both repositories and \`get_context\` on the files above; \`get_blast_radius(target="${b.id}")\` shows what else rides on ${b.name}.`
      : "\nOpen both sides of each route before you write anything.",
  ]);
}
