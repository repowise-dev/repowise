"use client";

/**
 * Routes between two services, in the rail at the Between level: a lede from
 * the graph the map drew, then every route the host's loader returns, grouped
 * by kind and direction, paged per kind, with a filter box. Kinds follow the
 * map's active families; rows in a hidden family are counted and offered,
 * not listed. Verbs: the Contracts page for either repository and a prompt
 * for an agent to review the pair. Presentation only; the host loads rows.
 */

import { useMemo, useState, type ElementType } from "react";
import type { SystemGraph } from "@repowise-dev/types/workspace";
import { AiPromptButton } from "../../health/ai-prompt-button";
import { AiPromptModal } from "../../health/ai-prompt-modal";
import type { AiPromptFlavor } from "../../health/ai-prompt-builder";
import { useDebounce } from "../../hooks/use-debounce";
import { Skeleton } from "../../ui/skeleton";
import type { BetweenResult } from "./between";
import { EDGE_FAMILY_COPY, type EdgeFamily } from "./edge-families";
import { edgeKindStyle } from "./edge-kinds";
import {
  directionLabel,
  hiddenRoutesSentence,
  routeKey,
  routesLede,
  rowsByDirection,
  type LoadLinksBetween,
  type RouteKindSection,
} from "./routes-between";
import { SystemMapRailFrame, type ContractRef } from "./system-map-drawer";
import { MICRO_LABEL } from "./system-map-filters";
import { buildPairAiPrompt } from "./system-map-pair-prompt";
import { RouteRow } from "./system-map-route-row";
import { plural } from "./system-map-model";
import { useRoutesBetween, type KindRows, type RoutesBetweenState } from "./use-routes-between";
import { formatNumber } from "../../lib/format";

export interface SystemMapRoutesPanelProps {
  graph: Pick<SystemGraph, "nodes">;
  between: BetweenResult;
  families: ReadonlySet<EdgeFamily>;
  loadLinksBetween?: LoadLinksBetween | undefined;
  /** The filter box's starting text, e.g. the route a search pick named. */
  initialFilter?: string | undefined;
  contractHref?: ((ref: ContractRef) => string) | undefined;
  /** The Contracts page filtered to one repository. */
  contractsHref?: ((repo: string) => string) | undefined;
  LinkComponent?: ElementType | undefined;
  /** Draw a hidden family, so its rows list here and its links on the canvas. */
  onShowFamily?: ((family: EdgeFamily) => void) | undefined;
  onClose: () => void;
}

const QUIET = "text-xs text-[var(--color-text-secondary)]";
const BUTTON =
  "rounded-md border border-[var(--color-border-hover)] px-2.5 py-1 text-xs font-medium text-[var(--color-text-primary)] hover:bg-[var(--color-bg-elevated)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]";

export function SystemMapRoutesPanel(props: SystemMapRoutesPanelProps) {
  const { graph, between, families } = props;
  const [filter, setFilter] = useState(props.initialFilter ?? "");
  const q = useDebounce(filter.trim(), 250);
  const state = useRoutesBetween({ load: props.loadLinksBetween, a: between.a, b: between.b, families, q });
  const [prompting, setPrompting] = useState(false);

  const nodeOf = (id: string) => graph.nodes.find((n) => n.id === id);
  const name = (id: string) => nodeOf(id)?.name ?? id;
  const lede = useMemo(
    () => routesLede(between, (id) => graph.nodes.find((n) => n.id === id)?.name ?? id),
    [between, graph],
  );
  const a = { id: between.a, name: name(between.a) };
  const b = { id: between.b, name: name(between.b) };
  const repos = [...new Set([nodeOf(a.id)?.repo, nodeOf(b.id)?.repo].filter((r): r is string => Boolean(r)))];

  const buildPrompt = () => {
    const rows = (state.plan?.sections ?? []).flatMap((s) => state.byKind[s.kind]?.rows ?? []);
    return (flavor: AiPromptFlavor) =>
      buildPairAiPrompt({ a, b, facts: lede, rows, total: state.plan?.shown ?? rows.length, flavor });
  };

  return (
    <>
      <SystemMapRailFrame eyebrow="Routes between" title={`${a.name} and ${b.name}`} onClose={props.onClose}>
        <div className="flex flex-col gap-1.5">
          {lede.map((s) => (
            <p key={s} className="text-[15px] leading-relaxed text-[var(--color-text-primary)] [text-wrap:pretty]">
              {s}
            </p>
          ))}
        </div>
        {state.stale && (
          <p className={QUIET} role="note">
            The map and the contracts were read at different commits, so some routes may be missing until the next update.
          </p>
        )}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <AiPromptButton onClick={() => setPrompting(true)} />
          {props.contractsHref &&
            repos.map((repo) => {
              const A = props.LinkComponent ?? "a";
              return (
                <A key={repo} href={props.contractsHref!(repo)} className="text-xs font-medium text-[var(--color-accent-primary)] hover:underline">
                  Open in Contracts: {repo} →
                </A>
              );
            })}
        </div>
        <input
          type="search"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter routes, files or packages"
          aria-label="Filter routes"
          className="h-8 w-full rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-inset)] px-2.5 text-xs text-[var(--color-text-primary)] placeholder:text-[var(--color-text-secondary)] focus:outline-none focus:ring-2 focus:ring-[var(--color-focus-ring)]"
        />
        <RoutesBody state={state} q={q} a={a.name} b={b.name} {...props} />
      </SystemMapRailFrame>
      <AiPromptModal
        open={prompting}
        onOpenChange={setPrompting}
        getPrompt={prompting ? buildPrompt() : null}
        title="Review the contract between two services"
        description={`A ready-to-paste prompt that has your agent confirm every route between ${a.name} and ${b.name} in code.`}
      />
    </>
  );
}

type BodyProps = SystemMapRoutesPanelProps & { state: RoutesBetweenState; q: string; a: string; b: string };

function RoutesBody({ state, q, a, b, onShowFamily, ...rest }: BodyProps) {
  if (state.unavailable) return <p className={QUIET}>Route detail is not available here.</p>;
  if (state.loading) return <RowSkeletons />;
  if (state.error || !state.plan) return <p className={QUIET}>The routes could not be loaded.</p>;
  const { plan } = state;
  const hidden = hiddenRoutesSentence(plan.hidden, EDGE_FAMILY_COPY);
  return (
    <div className="flex flex-col gap-5">
      {plan.shown === 0 && (
        <p className={QUIET}>{q ? `No routes match "${q}".` : `No routes between ${a} and ${b} in the families shown.`}</p>
      )}
      {plan.sections.map((s) => (
        <KindSection key={s.kind} section={s} rows={state.byKind[s.kind]} a={a} b={b} onMore={() => state.loadMore(s.kind)} {...rest} />
      ))}
      {hidden && (
        <div className="flex flex-wrap items-center gap-2">
          <p className={QUIET}>{hidden}</p>
          {onShowFamily &&
            plan.hidden.map((h) => (
              <button key={h.family} type="button" className={BUTTON} onClick={() => onShowFamily(h.family)}>
                Show {EDGE_FAMILY_COPY[h.family].label}
              </button>
            ))}
        </div>
      )}
    </div>
  );
}

function RowSkeletons() {
  return (
    <div className="flex flex-col gap-3" aria-busy="true" aria-label="Loading routes">
      {[0, 1, 2].map((i) => (
        <Skeleton key={i} className="h-[52px] w-full" />
      ))}
    </div>
  );
}

interface KindSectionProps extends Omit<SystemMapRoutesPanelProps, "graph" | "between" | "families" | "onClose"> {
  section: RouteKindSection;
  rows: KindRows | undefined;
  a: string;
  b: string;
  onMore: () => void;
}

function KindSection({ section, rows, a, b, onMore, contractHref, LinkComponent }: KindSectionProps) {
  const style = edgeKindStyle(section.kind);
  const Icon = style.icon;
  const loaded = rows?.rows ?? [];
  return (
    <section className="flex flex-col gap-1" aria-label={`${style.label} routes`}>
      <h3 className={`${MICRO_LABEL} inline-flex items-center gap-1.5`}>
        <Icon size={12} aria-hidden />
        {style.label} · {formatNumber(section.total)}
      </h3>
      {loaded.length === 0 && rows?.loading !== false ? (
        <RowSkeletons />
      ) : (
        rowsByDirection(loaded).map((g) => (
          <div key={g.direction} className="flex flex-col">
            <h4 className="text-xs text-[var(--color-text-secondary)]">
              {directionLabel(g.direction, a, b)} · {formatNumber(section.byDirection[g.direction] ?? g.rows.length)}
            </h4>
            <ul className="m-0 flex list-none flex-col divide-y divide-[var(--color-border-default)] p-0">
              {g.rows.map((r, i) => (
                // The position keeps two rows with the same evidence apart.
                <RouteRow key={`${routeKey(r)}#${i}`} row={r} contractHref={contractHref} LinkComponent={LinkComponent} />
              ))}
            </ul>
          </div>
        ))
      )}
      <PageFooter shown={loaded.length} total={section.total} rows={rows} onMore={onMore} />
    </section>
  );
}

function PageFooter({ shown, total, rows, onMore }: { shown: number; total: number; rows: KindRows | undefined; onMore: () => void }) {
  if (rows?.error) {
    return (
      <div className="flex items-center justify-between gap-3">
        <p className={QUIET}>This page of routes could not be loaded.</p>
        <button type="button" className={BUTTON} onClick={onMore}>
          Retry
        </button>
      </div>
    );
  }
  if (shown === 0 || shown >= total) return null;
  return (
    <div className="flex items-center justify-between gap-3">
      <p className={QUIET}>{`Showing ${formatNumber(shown)} of ${plural(total, "route", "routes")}`}</p>
      <button type="button" className={BUTTON} onClick={onMore} disabled={rows?.loading}>
        {rows?.loading ? "Loading…" : "Load more"}
      </button>
    </div>
  );
}
