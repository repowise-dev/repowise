/**
 * Agent prompts for cross-repo contracts: one contract, a group of client
 * calls that resolve to no provider, and a group of providers nothing calls.
 *
 * Same structure as the health builders (role, evidence, tasks, constraints,
 * what to hand back) so an agent can act without asking first. The evidence is
 * the extraction's reading, so every prompt tells the agent to verify it in the
 * code before changing anything, and the absent-caller prompts say plainly that
 * "no caller here" is not "dead".
 */

import {
  bulletList,
  closingSections,
  joinSections,
  numbered,
  preamble,
  verifySection,
  WORKSPACE_ROLE,
  type AiPromptFlavor,
  type PreambleParts,
} from "../health/ai-prompts/shared";
import {
  contractHeading,
  contractMetaEntries,
  contractMetaLabel,
  unmatchedReasonCopy,
  type ContractEntry,
} from "./contract-facts";
import { contractTypeLabel } from "./contract-type-label";

/** Rows listed in a group prompt before the rest are summarised as a count. */
export const MAX_PROMPT_ROWS = 40;

const PREAMBLE_PARTS: PreambleParts = {
  role: WORKSPACE_ROLE,
  source: "The contract evidence below was extracted statically by repowise.",
  open: "the files it names in every repository involved",
  mcpTail:
    "Pull what repowise already computed before re-reading by hand: `search_codebase` to find a route, table or symbol by name, `get_context([...])` for a file's skeleton, `get_symbol` for one body, `get_risk([...])` before editing, `get_why(...)` for the decision behind the current shape, and `get_blast_radius` for which services a change reaches.",
};

function where(repo: string, file: string, line?: number | null): string {
  return `\`${repo}\` \`${file}${line != null ? `:${line}` : ""}\``;
}

function more(hidden: number, noun: string): string {
  return hidden > 0 ? `...and ${hidden} more ${noun} not listed. Handle these after the above.` : "";
}

// ---------------------------------------------------------------------------
// One contract
// ---------------------------------------------------------------------------

/** The other side of one link, as the drawer has it. */
export interface ContractPromptLink {
  match_type: string;
  confidence: number;
  provider_repo: string;
  provider_file: string;
  provider_symbol: string;
  consumer_repo: string;
  consumer_file: string;
  consumer_symbol: string;
}

export interface BuildContractPromptOptions {
  contract: ContractEntry;
  links: ContractPromptLink[];
  /** Why a consumer matched nothing; null for providers and linked consumers. */
  unmatchedReason?: string | null | undefined;
  /** Findings from the latest breaking-change report on this contract. */
  breakingChanges?: { severity: string; kind: string; detail: string }[];
  flavor?: AiPromptFlavor;
}

function contractIdentity(contract: ContractEntry): string {
  const found = contractMetaEntries(contract.meta).map(
    ([k, v]) => `${contractMetaLabel(k)}: \`${v}\``,
  );
  return bulletList([
    `Contract: \`${contract.contract_id}\` (${contractTypeLabel(contract.contract_type)}, ${contract.role === "provider" ? "provider" : "consumer"})`,
    `Where: ${where(contract.repo, contract.file_path, contract.line)}${contract.service ? `, service \`${contract.service}\`` : ""}`,
    contract.symbol_name ? `Symbol: \`${contract.symbol_name}\`` : null,
    `Extraction confidence: ${Math.round(contract.confidence * 100)}%`,
    found.length > 0 ? `How it was found: ${found.join(", ")}` : null,
  ]);
}

/** The other side of each link: the callers of a provider, the providers of a call. */
function counterpartLines(links: ContractPromptLink[], isProvider: boolean): string[] {
  return links.map((l) =>
    isProvider
      ? `${where(l.consumer_repo, l.consumer_file)} \`${l.consumer_symbol}\` (${l.match_type} match, ${Math.round(l.confidence * 100)}%)`
      : `${where(l.provider_repo, l.provider_file)} \`${l.provider_symbol}\` (${l.match_type} match, ${Math.round(l.confidence * 100)}%)`,
  );
}

interface ContractState {
  state: string;
  tasks: string[];
}

function linkedState(n: number, isProvider: boolean): ContractState {
  if (isProvider) {
    return {
      state: `${n} call ${n === 1 ? "site resolves" : "sites resolve"} to this contract:`,
      tasks: [
        "Confirm each caller above really calls this contract (method, path or name, and payload).",
        "Before changing the contract's shape or behaviour, list what each caller sends and expects back, and say which change would break which caller.",
        "Name the tests in the calling repositories that exercise these calls, or say that none do.",
      ],
    };
  }
  return {
    state: `This call resolves to ${n === 1 ? "this provider" : `these ${n} providers`}:`,
    tasks: [
      "Confirm the provider above is the endpoint this call is meant to reach.",
      "Compare what the call sends and expects with what the provider accepts and returns; report any mismatch in fields, types, status codes or auth.",
    ],
  };
}

const UNCALLED_PROVIDER: ContractState = {
  state:
    "Nothing in this workspace resolves to this contract. That is not proof it is unused: it may be called from outside the workspace, from the same service (excluded from matching), or by code written in a form extraction cannot follow (a dynamic URL, a generated client, reflection).",
  tasks: [
    "Search every repository for callers, including dynamic ones: string-built URLs, generated clients, config-driven routes, other packages importing it.",
    "Check whether it is public API or consumed by something outside this workspace (a mobile app, a partner, a scheduled job).",
    "Only if no caller exists and it is not public: propose deprecating or removing it, with the evidence. Do not delete anything in this pass.",
  ],
};

/** What to do about a call that matched no provider, by the matcher's reason. */
function unmatchedTasks(reason: string | null, host: string | null): string[] {
  if (reason === "external_host") {
    return [
      `Confirm ${host ? `\`${host}\`` : "the host"} is a third party and not one of this workspace's own services behind a literal URL.`,
      "If it is your own service, say which repository serves it so it can be added to the workspace; otherwise there is nothing to fix.",
    ];
  }
  if (reason === "internal_only") {
    return [
      "Confirm the call is served from the same service that makes it. If so there is nothing to fix; say so and stop.",
    ];
  }
  return [
    "Find the endpoint this call intends. Search the providers for the same path with a different prefix, version, method or spelling.",
    "If it was renamed, fix the call. If it was removed, say whether the call is dead and can go. If a service outside the workspace serves it, name it.",
    "If a provider exists but is declared in a form extraction did not read (an unusual framework, a mounted router, a generated route), name the file and the pattern.",
  ];
}

function unmatchedState(contract: ContractEntry, reason: string | null): ContractState {
  const copy = unmatchedReasonCopy(reason);
  const host = typeof contract.meta?.host === "string" ? contract.meta.host : null;
  return {
    state: `This call matched no provider. Recorded reason: \`${reason ?? "none"}\` (${copy.title}). ${copy.meaning}${host ? ` Host: \`${host}\`.` : ""}`,
    tasks: unmatchedTasks(reason, host),
  };
}

function contractState(
  contract: ContractEntry,
  links: ContractPromptLink[],
  reason: string | null,
): ContractState {
  const isProvider = contract.role === "provider";
  if (links.length > 0) return linkedState(links.length, isProvider);
  return isProvider ? UNCALLED_PROVIDER : unmatchedState(contract, reason);
}

function breakingSection(changes: { severity: string; kind: string; detail: string }[]): string {
  if (changes.length === 0) return "";
  return joinSections([
    "## Breaking changes in the latest update",
    "",
    bulletList(changes.map((b) => `${b.severity}: \`${b.kind}\`${b.detail ? `: ${b.detail}` : ""}`)),
  ]);
}

export function buildContractAiPrompt({
  contract,
  links,
  unmatchedReason = null,
  breakingChanges = [],
  flavor = "generic",
}: BuildContractPromptOptions): string {
  const isProvider = contract.role === "provider";
  const { state, tasks } = contractState(contract, links, unmatchedReason);
  const counterpartFiles = links.map((l) => (isProvider ? l.consumer_file : l.provider_file));

  return joinSections([
    preamble(flavor, PREAMBLE_PARTS),
    "",
    `## Contract: ${contractHeading(contract)}`,
    "",
    contractIdentity(contract),
    "",
    "## State",
    "",
    state,
    bulletList(counterpartLines(links, isProvider)),
    breakingSection(breakingChanges),
    "",
    "## What to do",
    "",
    numbered(tasks),
    "",
    verifySection(null, [...new Set([contract.file_path, ...counterpartFiles])]),
    "",
    ...closingSections(
      [
        "Verify in the code before acting. The evidence above is static extraction and can be wrong.",
        "Keep the public contract stable unless the task explicitly calls for changing it.",
        "Do not delete code on the strength of a missing caller alone.",
      ],
      [
        "1. What you verified, with file and line for each claim.",
        "2. The change you made or propose, or why none is needed.",
        "3. Anything the extraction got wrong.",
      ],
    ),
    flavor === "claude-code-mcp"
      ? `Start with \`search_codebase\` for \`${contract.contract_id}\` to find every side of it, then \`get_context(['${contract.file_path}'])\` in \`${contract.repo}\` and \`get_risk\` before you edit. \`get_blast_radius\` shows which services a change reaches.`
      : "",
  ]);
}

// ---------------------------------------------------------------------------
// Unmatched consumers, one reason at a time
// ---------------------------------------------------------------------------

export interface UnmatchedConsumerPromptRow {
  repo: string;
  file_path: string;
  contract_id: string;
  contract_type: string;
}

export interface BuildUnmatchedConsumersPromptOptions {
  reason: string;
  rows: UnmatchedConsumerPromptRow[];
  flavor?: AiPromptFlavor;
}

export function buildUnmatchedConsumersAiPrompt({
  reason,
  rows,
  flavor = "generic",
}: BuildUnmatchedConsumersPromptOptions): string {
  const copy = unmatchedReasonCopy(reason);
  const shown = rows.slice(0, MAX_PROMPT_ROWS);
  const n = rows.length;
  const calls = `${n} client ${n === 1 ? "call" : "calls"}`;

  const tasks =
    reason === "external_host"
      ? [
          "For each call, confirm the host is a third party and not one of this workspace's own services behind a literal URL.",
          "List any that are your own services, with the repository that serves them. For the rest there is nothing to fix.",
        ]
      : reason === "internal_only"
        ? [
            "Spot-check that each call is served from the same service that makes it. Report any that are not; the rest need nothing.",
          ]
        : [
            "For each call, find the intended endpoint: search the providers for the same path with a different prefix, version, method or spelling.",
            "Classify each as renamed (fix the call), removed (the call is dead and can go), served outside the workspace (name the service), or declared in a form extraction did not read (name the file and pattern).",
            "Group calls that share a cause; one mount prefix or base URL often explains many.",
          ];

  return joinSections([
    preamble(flavor, PREAMBLE_PARTS),
    "",
    `## ${calls} that ${n === 1 ? "resolves" : "resolve"} to no provider: ${copy.title.toLowerCase()}`,
    "",
    bulletList([
      `Reason recorded by the matcher: \`${reason}\`. ${copy.meaning}`,
      "Source: repowise workspace contract extraction and matching.",
    ]),
    "",
    "## Calls",
    "",
    shown
      .map(
        (r, i) =>
          `${i + 1}. \`${r.contract_id}\` in ${where(r.repo, r.file_path)} (${contractTypeLabel(r.contract_type)})`,
      )
      .join("\n"),
    more(n - shown.length, n - shown.length === 1 ? "call" : "calls"),
    "",
    "## What to do",
    "",
    numbered(tasks),
    "",
    verifySection(null, []),
    "",
    ...closingSections(
      [
        "Verify each call in the code before acting. A path built from variables may not be what extraction recorded.",
        "Do not delete a call unless you have confirmed the endpoint it targets no longer exists anywhere.",
      ],
      [
        "A table with one row per call: the contract id, the verdict (renamed, removed, external, undetected provider, false positive), the evidence (file and line), and the fix you made or propose.",
      ],
    ),
    flavor === "claude-code-mcp"
      ? "Use `search_codebase` with the path's distinctive segments to find near-miss providers before reading files by hand."
      : "",
  ]);
}

// ---------------------------------------------------------------------------
// Providers nothing calls, one repository and type at a time
// ---------------------------------------------------------------------------

/** "3 HTTP routes", "1 table": the providers of one type, as a reader counts them. */
export function providerNoun(contractType: string, n: number): string {
  const one = n === 1;
  switch (contractType) {
    case "http":
      return one ? "HTTP route" : "HTTP routes";
    case "data":
      return one ? "table" : "tables";
    case "code":
      return one ? "exported symbol" : "exported symbols";
    default:
      return `${contractTypeLabel(contractType)} ${one ? "contract" : "contracts"}`;
  }
}

export interface OrphanProviderPromptRow {
  repo: string;
  file_path: string;
  contract_id: string;
  contract_type: string;
}

export interface BuildOrphanProvidersPromptOptions {
  repo: string;
  contractType: string;
  rows: OrphanProviderPromptRow[];
  /** Providers in the group when `rows` is a sample of them. */
  total?: number;
  flavor?: AiPromptFlavor;
}

export function buildOrphanProvidersAiPrompt({
  repo,
  contractType,
  rows,
  total,
  flavor = "generic",
}: BuildOrphanProvidersPromptOptions): string {
  const shown = rows.slice(0, MAX_PROMPT_ROWS);
  const n = Math.max(total ?? 0, rows.length);
  const noun = providerNoun(contractType, n);

  return joinSections([
    preamble(flavor, PREAMBLE_PARTS),
    "",
    `## ${n} ${noun} in \`${repo}\` with no caller in this workspace`,
    "",
    bulletList([
      "No consumer in any repository of this workspace was resolved to these providers.",
      "That is not proof they are unused. A caller may live outside the workspace, in the same service (excluded from matching), or in code extraction cannot follow: string-built URLs, generated clients, raw SQL assembled at runtime, dynamic imports.",
    ]),
    "",
    "## Providers",
    "",
    shown
      .map((r, i) => `${i + 1}. \`${r.contract_id}\` in \`${r.file_path}\``)
      .join("\n"),
    more(n - shown.length, n - shown.length === 1 ? "provider" : "providers"),
    "",
    "## What to do",
    "",
    numbered([
      "For each provider, search every repository for callers, including the dynamic forms above.",
      "Mark each as used (name the caller and why extraction missed it), public or external (name the consumer), or unused (no caller anywhere).",
      "For the unused ones, propose deprecation or removal with the evidence. Do not delete anything in this pass.",
      "If many were missed for one reason (a client wrapper, a base URL, an ORM pattern), name the pattern so extraction can learn it.",
    ]),
    "",
    verifySection(null, []),
    "",
    ...closingSections(
      [
        "Never remove a provider on the strength of this list alone.",
        "Treat anything public, versioned or documented as used until shown otherwise.",
      ],
      [
        "A table with one row per provider: the contract id, the verdict (used, external, unused), the evidence, and the proposed action.",
      ],
    ),
    flavor === "claude-code-mcp"
      ? `Start with \`get_dead_code\` and \`get_context\` on \`${repo}\` to see what the index already knows about these files, and \`get_why\` before proposing to remove anything.`
      : "",
  ]);
}
