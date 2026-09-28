/**
 * The pure reading of one contract: its name, its one-line summary, the
 * extractor detail it carries and the shape a reader recovered for it.
 *
 * These were local to the detail page and are now shared with the drawer, so
 * the two surfaces name a contract the same way. No `"use client"`: the detail
 * page is a server component and calls straight into them.
 */

import { contractTypeLabel } from "./contract-type-label";
import type { ContractSchema, SchemaField } from "@repowise-dev/types/workspace";

/**
 * One detected contract, as both the workspace list and the detail endpoint
 * return it. Declared structurally here so `packages/ui` stays free of the
 * API client, matching how the link entry is already carried.
 */
export interface ContractEntry {
  contract_id: string;
  contract_type: string;
  role: string;
  repo: string;
  file_path: string;
  symbol_name: string;
  confidence: number;
  service: string | null;
  line: number | null;
  symbol_id: string | null;
  meta: Record<string, unknown>;
}

/** The readable name of a contract, falling back to its id. */
export function contractHeading(contract: ContractEntry): string {
  const meta = contract.meta ?? {};
  const method = typeof meta.method === "string" ? meta.method : null;
  const path = typeof meta.path === "string" ? meta.path : null;
  const table = typeof meta.table === "string" ? meta.table : null;
  if (method && path) return `${method} ${path}`;
  if (table) return table;
  if (contract.contract_type === "code" && contract.symbol_name) return contract.symbol_name;
  return contract.contract_id;
}

/** One sentence saying what this record is, before any of the tables. */
export function contractLede(contract: ContractEntry): string {
  const isProvider = contract.role === "provider";
  const pkg = typeof contract.meta?.package === "string" ? contract.meta.package : null;
  switch (contract.contract_type) {
    case "http":
      return isProvider
        ? `${contract.repo} serves this route.`
        : `${contract.repo} calls this route.`;
    case "data":
      return isProvider
        ? `${contract.repo} defines this table.`
        : `${contract.repo} reads or writes this table.`;
    case "code":
      return isProvider
        ? `${contract.repo} exports this from ${pkg ?? "a package"}.`
        : `${contract.repo} imports this from ${pkg ?? "a package"}.`;
    default:
      return isProvider
        ? `${contract.repo} declares this ${contractTypeLabel(contract.contract_type)} contract.`
        : `${contract.repo} consumes this ${contractTypeLabel(contract.contract_type)} contract.`;
  }
}

/** `meta` keys in the order they read, across every contract type. */
const META_ORDER = [
  "extraction_layer",
  "framework",
  "client",
  "handler",
  "method",
  "path",
  "table",
  "verb",
  "package",
  "ecosystem",
  "host",
  "external",
  "base_token",
  "base_stripped",
];

const META_LABELS: Record<string, string> = {
  extraction_layer: "Layer",
  framework: "Framework",
  client: "Client",
  handler: "Handler",
  method: "Method",
  path: "Path",
  table: "Table",
  verb: "Verb",
  package: "Package",
  ecosystem: "Ecosystem",
  host: "Host",
  external: "External",
  base_token: "Base token",
  base_stripped: "Base stripped",
};

export function contractMetaLabel(key: string): string {
  return META_LABELS[key] ?? key.replace(/_/g, " ");
}

/**
 * `meta` as ordered, printable pairs. Keys vary by contract type and an
 * extractor is free to add one, so anything unrecognised is kept and printed
 * under its own name rather than dropped.
 */
export function contractMetaEntries(meta: Record<string, unknown>): [string, string][] {
  const known = new Set(META_ORDER);
  const present = (k: string) => meta?.[k] !== undefined && meta[k] !== null;
  const keys = [
    ...META_ORDER.filter(present),
    ...Object.keys(meta ?? {}).filter((k) => !known.has(k) && present(k)),
  ];
  return keys.map((k) => [k, String(meta[k])]);
}

/** One `meta` value as a string, or null when the extractor recorded none. */
export function contractMetaString(
  meta: Record<string, unknown> | undefined,
  key: string,
): string | null {
  const value = meta?.[key];
  return typeof value === "string" && value !== "" ? value : null;
}

/**
 * Narrow the loosely-typed `contract_schema` off the wire.
 *
 * It arrives as a bare object because the endpoint passes the artifact block
 * straight through, so the shape is checked here rather than assumed: a
 * workspace indexed by an older build can carry a block without the arrays.
 */
export function asContractSchema(raw: Record<string, unknown> | null): ContractSchema | null {
  if (!raw) return null;
  const text = (key: string) => (typeof raw[key] === "string" ? raw[key] : undefined);
  const state = (key: string): ContractSchema["request_state"] => {
    const value = raw[key];
    return value === "complete" ||
      value === "partial" ||
      value === "unsupported" ||
      value === "unresolved"
      ? value
      : undefined;
  };
  const schema: ContractSchema = {
    source: typeof raw.source === "string" ? raw.source : "unknown",
    request_fields: Array.isArray(raw.request_fields) ? (raw.request_fields as SchemaField[]) : [],
    response_fields: Array.isArray(raw.response_fields)
      ? (raw.response_fields as SchemaField[])
      : [],
  };
  const sourceVersion = text("source_version");
  const comparisonKey = text("comparison_key");
  const requestState = state("request_state");
  const responseState = state("response_state");
  const requestMediaType = text("request_media_type");
  const responseMediaType = text("response_media_type");
  const responseStatusCode = text("response_status_code");
  if (sourceVersion !== undefined) schema.source_version = sourceVersion;
  if (comparisonKey !== undefined) schema.comparison_key = comparisonKey;
  if (typeof raw.comparison_ready === "boolean") schema.comparison_ready = raw.comparison_ready;
  if (requestState !== undefined) schema.request_state = requestState;
  if (responseState !== undefined) schema.response_state = responseState;
  if (requestMediaType !== undefined) schema.request_media_type = requestMediaType;
  if (responseMediaType !== undefined) schema.response_media_type = responseMediaType;
  if (responseStatusCode !== undefined) schema.response_status_code = responseStatusCode;
  if (Array.isArray(raw.issues)) {
    schema.issues = raw.issues as NonNullable<ContractSchema["issues"]>;
  }
  return schema;
}

export interface SchemaFieldRow {
  field: SchemaField;
  path: string;
}

/** Flatten a bounded recursive schema for the existing list/table surfaces. */
export function flattenSchemaFields(fields: SchemaField[]): SchemaFieldRow[] {
  const rows: SchemaFieldRow[] = [];
  const visit = (field: SchemaField, parent: string) => {
    let segment = field.name;
    if (segment === "$body") segment = "body";
    else if (segment === "$response") segment = "response";
    else if (segment === "$items") segment = "[]";
    else if (field.location && field.location !== "body") {
      segment = `${field.location}:${segment}`;
    }
    const path = segment === "[]" ? `${parent}[]` : parent ? `${parent}.${segment}` : segment;
    rows.push({ field, path });
    for (const child of field.children ?? []) visit(child, path);
    if (field.items) visit(field.items, path);
  };
  for (const field of fields) visit(field, "");
  return rows;
}

export function schemaFieldConstraints(field: SchemaField): string {
  const values = [field.required ? "Required" : "Optional"];
  if (field.nullable === true) values.push("Nullable");
  if (field.enum_values?.length) values.push(`Enum: ${field.enum_values.join(", ")}`);
  if (field.repeated) values.push("Repeated");
  return values.join(" · ");
}

/**
 * The matched links this contract sits on, read from a page-wide link list.
 *
 * A link is keyed by both sides, and one contract id can be declared in
 * several files, so the file has to match too: without it a provider would
 * claim the callers of every other declaration sharing its id.
 */
export function linksForContract<
  T extends {
    contract_id: string;
    consumer_contract_id?: string | null;
    provider_repo: string;
    provider_file: string;
    consumer_repo: string;
    consumer_file: string;
  },
>(contract: ContractEntry, links: T[]): T[] {
  const isProvider = contract.role === "provider";
  // A consumer that reached its provider under another name (a queue bound to
  // an exchange) carries its own id on the link, not the provider's.
  return links.filter((l) =>
    isProvider
      ? l.contract_id === contract.contract_id &&
        l.provider_repo === contract.repo &&
        l.provider_file === contract.file_path
      : (l.consumer_contract_id ?? l.contract_id) === contract.contract_id &&
        l.consumer_repo === contract.repo &&
        l.consumer_file === contract.file_path,
  );
}

// ---------------------------------------------------------------------------
// Identity parsed off the id, and the words each state is read in. Shared by
// the list rows, the drawer, the detail page and the agent prompts, so every
// surface names one contract and one state the same way.
// ---------------------------------------------------------------------------

/** A contract id split into the parts a reader scans for. */
export interface ContractIdParts {
  /** The id's leading type segment: `http`, `data`, `code`, `grpc`... */
  kind: string;
  /** HTTP verb, when the id carries one. `*` is kept as extraction wrote it. */
  method: string | null;
  /** Path, table, symbol or rpc: the part a reader recognises. */
  label: string;
  /** The package a `code` contract is exported from. */
  qualifier: string | null;
}

/**
 * Parse `http::GET::/path`, `data::table`, `code::pkg::symbol` and friends.
 * Only rows that carry nothing but the id (links, diagnostics) need this; a
 * full entry reads its `meta` instead through `contractHeading`.
 */
export function parseContractId(id: string): ContractIdParts {
  const parts = id.split("::");
  const kind = parts[0] ?? "";
  if (parts.length < 2) return { kind: "", method: null, label: id, qualifier: null };
  if (kind === "http" && parts.length >= 3) {
    return { kind, method: parts[1] ?? null, label: parts.slice(2).join("::"), qualifier: null };
  }
  if (kind === "code" && parts.length >= 3) {
    return {
      kind,
      method: null,
      label: parts.slice(2).join("::"),
      qualifier: parts[1] ?? null,
    };
  }
  return { kind, method: null, label: parts.slice(1).join("::"), qualifier: null };
}

/** Why a consumer matched no provider, in words, with what to do about it. */
export interface UnmatchedReasonCopy {
  /** Heading for one consumer in this state. */
  title: string;
  /** Short label for counts: "54 no provider found". */
  short: string;
  /** One line for a group of consumers in this state. */
  meaning: string;
  /** Whether a reader has something to fix, which orders the groups. */
  actionable: boolean;
}

export const UNMATCHED_REASONS: Record<string, UnmatchedReasonCopy> = {
  no_provider: {
    title: "No provider found",
    short: "no provider found",
    meaning:
      "Nothing in the workspace declares these routes: a typo, a removed endpoint, a service outside the workspace, or a framework extraction does not read.",
    actionable: true,
  },
  unlinked: {
    title: "No link formed",
    short: "unlinked",
    meaning:
      "A matching declaration exists in another service but no link was formed, which points at the matcher rather than the code.",
    actionable: true,
  },
  internal_only: {
    title: "Not a cross-repo link",
    short: "internal to one service",
    meaning:
      "The only matching declaration is in the same repository and service, so the call never crosses a boundary. Nothing to fix.",
    actionable: false,
  },
  external_host: {
    title: "Outside this workspace",
    short: "external host",
    meaning:
      "Calls to a literal third-party host, left out of matching on purpose. Nothing to fix unless the host is one of your own services.",
    actionable: false,
  },
};

const UNKNOWN_REASON: UnmatchedReasonCopy = {
  title: "No provider found",
  short: "no reason recorded",
  meaning:
    "These calls matched no declaration and no reason was recorded. Reasons come from the system graph, so rebuild it to get one.",
  actionable: true,
};

export function unmatchedReasonCopy(reason: string | null | undefined): UnmatchedReasonCopy {
  return (reason && UNMATCHED_REASONS[reason]) || UNKNOWN_REASON;
}

/** Actionable reasons first, then by size. */
export function sortUnmatchedReasons<T extends { reason: string; count: number }>(
  groups: T[],
): T[] {
  return groups
    .slice()
    .sort(
      (a, b) =>
        Number(unmatchedReasonCopy(b.reason).actionable) -
          Number(unmatchedReasonCopy(a.reason).actionable) || b.count - a.count,
    );
}

/** One consumer's unmatched state as a paragraph, with the concrete next step. */
export function unmatchedConsumerProse(reason: string | null, contract: ContractEntry): string {
  const host = contractMetaString(contract.meta, "host");
  switch (reason) {
    case "external_host":
      return `This call goes to ${host ?? "a third-party host"}, which is not a service in this workspace. Calls to a literal external host are left out of matching on purpose, so there is nothing to link. If ${host ?? "that host"} is one of your own services, add its repository to the workspace.`;
    case "internal_only":
      return "The only declarations matching this call live in the same repository and the same service as the call itself, so it never crosses a boundary. Intra-service calls are left out of the link set on purpose: a link is a claim that two services depend on each other.";
    case "no_provider":
      return `Nothing in this workspace declares ${contractHeading(contract)}. Check for a typo in the path, an endpoint that was renamed or removed, a service outside these repositories, or a route declared in a form extraction does not recognise.`;
    case "unlinked":
      return "A declaration with this id exists in another service, but no link was formed between the two. That is rare, and it points at a gap in the matcher rather than at the code.";
    default:
      return "This call matched no declaration, and no reason was recorded for it. Reasons come from the system graph, so a workspace that has not built one reports the count without the explanation.";
  }
}

/**
 * A provider nothing calls. The expected state for most exported code, so it
 * reads as a sentence and never as a warning.
 */
export function unlinkedProviderProse(contract: ContractEntry): string {
  // A pair is skipped when the repository *and* the service both match, so
  // the excluded set is not "everything in this repo" unless this declaration
  // also sits outside a service.
  const excluded = contract.service
    ? `a call from inside ${contract.repo}/${contract.service}`
    : `a call from elsewhere in ${contract.repo} that also sits outside any service`;
  return `Nothing in this workspace resolves to this contract. It may be called from outside the workspace, or by code extraction cannot follow, and ${excluded} is excluded by construction. Neither reading makes it dead code.`;
}

/** How many call sites resolve to a provider, and from where. */
export function providerLinkedProse(
  links: { consumer_repo: string }[],
  repo: string,
): string {
  const n = links.length;
  const one = n === 1;
  const repos = new Set(links.map((l) => l.consumer_repo));
  const head = `${n} call ${one ? "site resolves" : "sites resolve"} to this contract`;
  if (repos.size === 1 && repos.has(repo)) {
    return `${head}, ${one ? "from" : "all from"} a different service inside ${repo}.`;
  }
  return `${head} across ${repos.size} ${repos.size === 1 ? "repository" : "repositories"}.`;
}

/**
 * What a link's confidence is. It is the lower of the two sides' extraction
 * confidence, so it names how each side was found, not how well they match.
 */
export const LINK_CONFIDENCE_NOTE =
  "A link's confidence is the lower of its two sides' extraction confidence, so it says how each side was found, not how well they match. For example, exports and imports read from the symbol index score 90%, routes, tables and client calls 75 to 85%, SQL found in strings 70%, and calls through a helper recognised only by its name 65%.";

/** Providers with no caller, for one repository and type. */
export interface OrphanGroup<T> {
  repo: string;
  contractType: string;
  /** Every provider in the group. */
  count: number;
  /** The first `keep` of them: enough for a prompt, not the whole list. */
  rows: T[];
}

/**
 * Group providers with no caller by repository and type, largest first.
 *
 * Runs on the server so the page ships counts and a prompt's worth of rows per
 * group, not every provider: on a large workspace the full list is most of the
 * payload and the page only ever shows a summary of it.
 */
export function groupOrphanProviders<T extends { repo: string; contract_type: string }>(
  orphans: T[],
  keep: number,
): OrphanGroup<T>[] {
  const byKey = new Map<string, OrphanGroup<T>>();
  for (const o of orphans) {
    const key = `${o.repo}\u0000${o.contract_type}`;
    let group = byKey.get(key);
    if (!group) {
      group = { repo: o.repo, contractType: o.contract_type, count: 0, rows: [] };
      byKey.set(key, group);
    }
    group.count += 1;
    if (group.rows.length < keep) group.rows.push(o);
  }
  return [...byKey.values()].sort((a, b) => b.count - a.count || a.repo.localeCompare(b.repo));
}
