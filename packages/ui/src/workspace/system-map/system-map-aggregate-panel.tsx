"use client";

/**
 * The rail for an aggregate: a group (where it came from, its services with
 * their health, its links, and the verb to open it) or a band (its sentence
 * and the service pairs behind it, each one a way to that pair's strongest
 * relationship). Presentation only; the map passes the drawn data.
 */

import type { SystemEdge, SystemNode } from "@repowise-dev/types";
import { bandSentence, groupSourceLabel, healthCounts, healthSummaryText, kindMixText } from "./aggregate-facts";
import { summarizeLinks } from "./bands";
import type { DrawnBand, DrawnGroupNode, LinkSummary } from "./level-types";
import { SystemMapRailFrame } from "./system-map-drawer";
import { MICRO_LABEL } from "./system-map-filters";
import { plural } from "./system-map-model";
import { SystemMapHealthMark } from "./system-map-health-mark";
import type { RepoHealth, SystemMapSelection } from "./types";

const ROW =
  "flex w-full min-w-0 items-baseline justify-between gap-3 rounded px-2 py-1.5 text-left hover:bg-[var(--color-bg-elevated)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]";

interface Lookups {
  nodesById: ReadonlyMap<string, SystemNode>;
  edgesById: ReadonlyMap<string, SystemEdge>;
  healthByRepo?: ReadonlyMap<string, RepoHealth> | undefined;
  onSelect: (selection: SystemMapSelection) => void;
  onClose: () => void;
}

export function GroupPanel({
  group,
  onOpen,
  nodesById,
  healthByRepo,
  onSelect,
  onClose,
}: Lookups & { group: DrawnGroupNode; onOpen: () => void }) {
  const score = (id: string) => {
    const repo = nodesById.get(id)?.repo;
    const h = repo ? healthByRepo?.get(repo) : undefined;
    return h ? h.score / 10 : null;
  };
  const source = groupSourceLabel(group.groupId, group.source, group.serviceIds.length);
  return (
    <SystemMapRailFrame eyebrow="Group" title={group.label} onClose={onClose}>
      <p className="text-xs text-[var(--color-text-secondary)]">
        {plural(group.serviceIds.length, "service", "services")} in {plural(group.repoCount, "repository", "repositories")}
        {source ? `, grouped by ${source}` : ""}. {healthSummaryText(healthCounts(group.serviceIds.map(score)))}.{" "}
        <span className="font-mono tabular-nums">
          in {group.linksIn} · out {group.linksOut} · {group.linksInside} inside
        </span>{" "}
        links in the families drawn.
      </p>
      {!group.open && (
        <button
          type="button"
          onClick={onOpen}
          className="self-start text-xs font-medium text-[var(--color-accent-primary)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
        >
          Open this group on the map
        </button>
      )}
      <section className="flex flex-col gap-1">
        <h3 className={MICRO_LABEL}>Services</h3>
        <ul className="flex flex-col">
          {group.serviceIds.map((id) => (
            <li key={id}>
              <button type="button" className={ROW} onClick={() => onSelect({ type: "node", id })}>
                <span className="min-w-0 truncate text-xs text-[var(--color-text-primary)]">{nodesById.get(id)?.name ?? id}</span>
                <SystemMapHealthMark score={score(id)} />
              </button>
            </li>
          ))}
        </ul>
      </section>
    </SystemMapRailFrame>
  );
}

interface Pair {
  source: string;
  target: string;
  summary: LinkSummary;
  strongest: string;
}

/** The service pairs behind a band, most links first. */
export function bandPairs(band: DrawnBand, edgesById: ReadonlyMap<string, SystemEdge>): Pair[] {
  const byPair = new Map<string, SystemEdge[]>();
  for (const id of band.summary.edgeIds) {
    const e = edgesById.get(id);
    if (!e) continue;
    const key = `${e.source}>${e.target}`;
    const list = byPair.get(key);
    if (list) list.push(e);
    else byPair.set(key, [e]);
  }
  return [...byPair.values()]
    .map((edges) => {
      const strongest = [...edges].sort((a, b) => b.weight - a.weight || (a.id < b.id ? -1 : 1))[0]!;
      return { source: strongest.source, target: strongest.target, summary: summarizeLinks(edges), strongest: strongest.id };
    })
    .sort((a, b) => b.summary.links - a.summary.links || (a.source + a.target < b.source + b.target ? -1 : 1));
}

export function BandPanel({
  band,
  sourceLabel,
  targetLabel,
  nodesById,
  edgesById,
  onSelect,
  onClose,
}: Lookups & { band: DrawnBand; sourceLabel: string; targetLabel: string }) {
  const pairs = bandPairs(band, edgesById);
  const name = (id: string) => nodesById.get(id)?.name ?? id;
  return (
    <SystemMapRailFrame eyebrow="Band" title={`${sourceLabel} → ${targetLabel}`} onClose={onClose}>
      <p className="text-xs text-[var(--color-text-secondary)]">{bandSentence(sourceLabel, targetLabel, band.summary)}</p>
      <section className="flex flex-col gap-1">
        <h3 className={MICRO_LABEL}>{plural(pairs.length, "service pair", "service pairs")}</h3>
        <ul className="flex flex-col">
          {pairs.map((p) => (
            <li key={`${p.source}>${p.target}`}>
              <button type="button" className={ROW} onClick={() => onSelect({ type: "edge", id: p.strongest })}>
                <span className="min-w-0 text-xs text-[var(--color-text-primary)] [overflow-wrap:anywhere]">
                  {name(p.source)} → {name(p.target)}
                </span>
                <span className="shrink-0 font-mono text-[10px] tabular-nums text-[var(--color-text-secondary)]">
                  {p.summary.links} · {kindMixText(p.summary.kinds)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </section>
    </SystemMapRailFrame>
  );
}
