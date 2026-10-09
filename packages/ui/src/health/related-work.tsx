"use client";

/**
 * What the other lenses hold for the file in front of the reader.
 *
 * The general form of {@link FindingOpportunityLink}: a drawer in one lens
 * (findings, refactoring, performance) lists what findings, Fix first,
 * refactoring, performance and dead code each say about the same file, and
 * links to where that item lives. The host fetches (`useRelatedWork`) and says
 * where each item goes; this renders. It never repeats the lens it sits in,
 * and renders nothing at all when there is nothing else to say or the host
 * supplied no data, so an absent section is never read as "clean elsewhere".
 */

import * as React from "react";

import type {
  RelatedWorkFile,
  RelatedWorkItem,
  RelatedWorkLens,
  RelatedWorkResponse,
} from "@repowise-dev/types/health";
import { typeMeta } from "../refactoring/meta";
import { biomarkerLabel } from "./biomarker-glossary";
import { TIER_LABEL } from "./fix-first/scope";
import { EFFORT_LABEL } from "./labels";
import { ACTIONABILITY_LABEL, humanizeToken } from "./performance/presentation";
import { SEVERITY_LABEL, type Severity } from "./tokens";

export type RelatedLensName = RelatedWorkItem["lens"];

/** Where one related item lives, or null when it has nowhere to go. */
export type RelatedWorkHref = (item: RelatedWorkItem, filePath: string) => string | null;

/** What a drawer takes to show the section: the data, the links, the router. */
export interface RelatedWorkSlotProps {
  /** The file's entry from `useRelatedWork`. Absent: no section. */
  related?: RelatedWorkFile | null | undefined;
  /** Omit and the items are plain text. */
  relatedWorkHref?: RelatedWorkHref | undefined;
  /** Host navigation, for a surface that routes rather than follows an anchor. */
  onNavigate?: ((href: string) => void) | undefined;
}

/**
 * Fetch what every lens holds for these files, once per set of paths.
 *
 * Same contract as `useFileOpportunity`: `undefined` while not asked, when
 * the host cannot ask, or when the lookup failed, so a caller never renders a
 * claim it has not established. Pass a stable `fetcher`.
 */
export function useRelatedWork(
  fetcher: ((filePaths: string[]) => Promise<RelatedWorkResponse>) | undefined,
  filePaths: readonly (string | null | undefined)[],
): RelatedWorkResponse | undefined {
  const [value, setValue] = React.useState<RelatedWorkResponse | undefined>(undefined);
  // The one key: the paths in order. A new array with the same paths is the
  // same request.
  const key = filePaths.filter((p): p is string => Boolean(p)).join("\n");

  React.useEffect(() => {
    setValue(undefined);
    if (!fetcher || !key) return;
    let live = true;
    void fetcher(key.split("\n"))
      .then((result) => {
        if (live) setValue(result);
      })
      .catch(() => {
        if (live) setValue(undefined);
      });
    return () => {
      live = false;
    };
  }, [fetcher, key]);

  return value;
}

/** Every lens, in the order the server lists them. */
export const RELATED_LENSES: readonly RelatedLensName[] = [
  "findings",
  "fix_first",
  "refactoring",
  "performance",
  "dead_code",
];

export const RELATED_LENS_LABEL: Record<RelatedLensName, string> = {
  findings: "Findings",
  fix_first: "Fix first",
  refactoring: "Refactoring",
  performance: "Performance",
  dead_code: "Dead code",
};

function lookup<K extends string>(table: Record<K, string>, key: string | null | undefined) {
  return key && key in table ? table[key as K] : null;
}

/** An item's label and the plain-word facts beside it. */
function describe(item: RelatedWorkItem): { label: string; facts: string[] } {
  const kind = item.kind ?? "";
  let label: string;
  let facts: (string | null)[];
  switch (item.lens) {
    case "findings":
      label = biomarkerLabel(kind);
      facts = [lookup<Severity>(SEVERITY_LABEL, item.severity), item.symbol ?? null];
      break;
    case "fix_first":
      label = item.title ?? "Ranked fix";
      facts = [
        item.rank != null ? `#${(item.rank + 1).toLocaleString()} in Fix first` : null,
        lookup(TIER_LABEL, item.tier),
      ];
      break;
    case "refactoring": {
      label = typeMeta(kind).label;
      const effort = lookup(EFFORT_LABEL, item.tier);
      facts = [effort ? `${effort} effort` : null];
      break;
    }
    case "performance":
      label = biomarkerLabel(kind);
      facts = [
        item.symbol ? (item.symbol.split("::").pop() ?? item.symbol) : null,
        lookup(ACTIONABILITY_LABEL, item.tier),
      ];
      break;
    case "dead_code":
      label = item.symbol ? `${humanizeToken(kind)}: ${item.symbol}` : humanizeToken(kind);
      facts = [item.tier === "safe_to_delete" ? "Safe to delete" : "Needs review"];
      break;
  }
  if (item.line != null) facts.push(`line ${item.line}`);
  if (item.deprecated) facts.push("deprecated");
  if (item.code_origin) facts.push(humanizeToken(item.code_origin).toLowerCase());
  return { label, facts: facts.filter((v): v is string => Boolean(v)) };
}

export interface RelatedWorkProps extends RelatedWorkSlotProps {
  /** Lenses this surface already shows; their items are never repeated here. */
  exclude?: readonly RelatedLensName[] | undefined;
  /** Heading level, so the section nests under the drawer's own headings. */
  headingLevel?: "h3" | "h4" | undefined;
}

export function RelatedWork({
  related,
  relatedWorkHref,
  onNavigate,
  exclude = [],
  headingLevel = "h3",
}: RelatedWorkProps) {
  const lenses = RELATED_LENSES.filter((name) => !exclude.includes(name))
    .map((name) => [name, related?.lenses?.[name]] as const)
    .filter((entry): entry is readonly [RelatedLensName, RelatedWorkLens] =>
      Boolean(entry[1]?.items?.length),
    );
  if (!related || lenses.length === 0) return null;

  const Heading = headingLevel;
  return (
    <section className="flex flex-col gap-2" aria-label="Elsewhere for this file">
      <Heading className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
        Elsewhere for this file
      </Heading>
      <div className="flex flex-col gap-3">
        {lenses.map(([name, lens]) => {
          const items = lens.items ?? [];
          const total = lens.total ?? items.length;
          return (
            <div key={name} className="flex flex-col gap-1">
              <p className="text-[11px] font-medium text-[var(--color-text-secondary)]">
                {RELATED_LENS_LABEL[name]}{" "}
                <span className="font-normal tabular-nums text-[var(--color-text-tertiary)]">
                  {items.length < total
                    ? `${items.length} of ${total.toLocaleString()}`
                    : total.toLocaleString()}
                </span>
              </p>
              <ul className="flex flex-col">
                {items.map((item) => (
                  <RelatedRow
                    key={`${item.lens}:${item.id}`}
                    item={item}
                    target={relatedWorkHref?.(item, related.file_path) ?? null}
                    onNavigate={onNavigate}
                  />
                ))}
              </ul>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function RelatedRow({
  item,
  target,
  onNavigate,
}: {
  item: RelatedWorkItem;
  target: string | null;
  onNavigate?: ((href: string) => void) | undefined;
}) {
  const { label, facts } = describe(item);
  const text = (
    <>
      <span className="min-w-0 truncate">{label}</span>
      {facts.length > 0 ? (
        <span className="shrink-0 font-mono text-[10px] text-[var(--color-text-tertiary)]">
          {facts.join(" · ")}
        </span>
      ) : null}
    </>
  );
  return (
    <li className="py-0.5 text-xs">
      {target ? (
        <a
          href={target}
          onClick={
            onNavigate
              ? (e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  onNavigate(target);
                }
              : (e) => e.stopPropagation()
          }
          className="flex items-baseline gap-2 rounded text-[var(--color-accent-primary)] underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--color-accent-primary)]"
        >
          {text}
        </a>
      ) : (
        <span className="flex items-baseline gap-2 text-[var(--color-text-primary)]">{text}</span>
      )}
    </li>
  );
}
