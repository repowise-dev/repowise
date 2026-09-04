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
  return {
    source: typeof raw.source === "string" ? raw.source : "unknown",
    request_fields: Array.isArray(raw.request_fields) ? (raw.request_fields as SchemaField[]) : [],
    response_fields: Array.isArray(raw.response_fields)
      ? (raw.response_fields as SchemaField[])
      : [],
  };
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
    provider_repo: string;
    provider_file: string;
    consumer_repo: string;
    consumer_file: string;
  },
>(contract: ContractEntry, links: T[]): T[] {
  const isProvider = contract.role === "provider";
  return links.filter(
    (l) =>
      l.contract_id === contract.contract_id &&
      (isProvider
        ? l.provider_repo === contract.repo && l.provider_file === contract.file_path
        : l.consumer_repo === contract.repo && l.consumer_file === contract.file_path),
  );
}
