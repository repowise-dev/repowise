"use client";

/**
 * What the map's marks mean, plus the relation filter: one button in the
 * canvas's control cluster that opens a popover.
 *
 * A popover keeps the key one click (or Tab, Enter) away and leaves the full
 * height to the map; the button shows a dot while a filter is on, so a
 * thinned-out map never looks like a bug. The key explains the marks a card
 * shows without naming them: the coloured number in its top-right corner (the
 * health score) and the role icons in its footer or beside a file's name.
 *
 * The filter selects relation verbs (imports, calls, and the rest of the
 * label vocabulary), so "what calls what, not what imports what" is a
 * question the map answers. No verbs selected means every verb draws, rather
 * than an empty canvas: a filter whose resting state hides everything trains
 * people not to touch it.
 */

import type { ReactNode } from "react";
import { Ban, Flame, type LucideIcon, Play, SlidersHorizontal } from "lucide-react";
import { FilterChip } from "@repowise-dev/ui/health";
import { Popover, PopoverContent, PopoverTrigger } from "@repowise-dev/ui/ui/popover";
import { techName, type VerbCount, type ZoomContainer, toggleVerb } from "@repowise-dev/ui/zoom";
import {
  FAIR_MIN,
  GOOD_MIN,
  HEALTH_BAND_LABEL,
  NEEDS_WORK_MIN,
} from "@repowise-dev/types/health";
import { useTranslations } from "next-intl";
import { ZOOM_GLASS, ZOOM_ICON_BUTTON } from "./zoom-controls";
import { containerIcon } from "./c4-icons";
import { useContainerKindLabel } from "./zoom-detail-c4";
import { formatNumber } from "@repowise-dev/ui/lib/format";

interface ZoomMapKeyProps {
  /** What the page is, said once here rather than above the canvas. */
  about: string;
  /** Every verb present in the loaded map, descending by count. */
  verbs: VerbCount[];
  /** Files placed in the tree, and files curation left in no layer. The second
   *  is the map's missing denominator: without it, a map short one file in
   *  seven reads as complete. */
  totalFiles: number;
  unclaimedFiles: number;
  /** Null = every relation draws; otherwise only these verbs do. */
  selected: ReadonlySet<string> | null;
  onSelectedChange: (verbs: ReadonlySet<string> | null) => void;
  /** Deployables on the map, largest first; picking one dims the cards outside it. */
  containers: readonly ZoomContainer[];
  container: string | null;
  onContainerChange: (id: string | null) => void;
  /** The map draws dashed external-system cards, so the key says what they are. */
  hasExternals: boolean;
  /** The at-rest links line (`ZoomLinksLegend`), under the marks. */
  linksLegend?: ReactNode;
}

function Dot({ className }: { className: string }) {
  return <span aria-hidden className={`inline-block h-2 w-2 shrink-0 rounded-full ${className}`} />;
}

/** One role icon in the canvas's ink for it, with its name. */
function RoleIcon({ icon: Icon, className, label }: { icon: LucideIcon; className: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      <Icon aria-hidden className={`h-3 w-3 shrink-0 ${className}`} />
      {label}
    </span>
  );
}

export function ZoomMapKey({ about, linksLegend, ...body }: ZoomMapKeyProps) {
  const t = useTranslations("zoom");
  const filtered = body.selected !== null || body.container !== null;

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={filtered ? t("mapKey.buttonFiltered") : t("mapKey.button")}
          title={t("mapKey.button")}
          className={`pointer-events-auto relative shrink-0 ${ZOOM_GLASS} ${ZOOM_ICON_BUTTON}`}
        >
          <SlidersHorizontal className="h-4 w-4" aria-hidden />
          {filtered && (
            <span
              aria-hidden
              className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-[var(--color-accent-primary)]"
            />
          )}
        </button>
      </PopoverTrigger>
      <PopoverContent side="top" align="start" className="w-[22rem] max-w-[calc(100vw-2rem)] space-y-3">
        <p className="text-[13px] leading-relaxed text-[var(--color-text-primary)]">{about}</p>
        <MapKeyBody {...body} />
        {linksLegend}
        <ContainerFilter
          containers={body.containers}
          container={body.container}
          onContainerChange={body.onContainerChange}
        />
      </PopoverContent>
    </Popover>
  );
}

/**
 * The deployables (C4 containers) as chips: picking one dims every card with
 * no files in it, so "what is the API made of" is one click. A layer that
 * spans several stays lit for each of them.
 */
function ContainerFilter({
  containers,
  container,
  onContainerChange,
}: Pick<ZoomMapKeyProps, "containers" | "container" | "onContainerChange">) {
  const t = useTranslations("zoom");
  const kindLabel = useContainerKindLabel();
  if (containers.length < 2) return null;
  return (
    <div className="flex flex-col gap-2 border-t border-[var(--color-border-default)] pt-3">
      <p className="text-[13px] leading-relaxed text-[var(--color-text-secondary)]">
        <span className="text-[var(--color-text-primary)]">{t("mapKey.containers")}</span>{" "}
        {t("mapKey.containersHint")}
      </p>
      <div className="flex flex-wrap items-center gap-1.5">
        {containers.map((c) => {
          const Icon = containerIcon(c.kind);
          return (
            <FilterChip
              key={c.id}
              active={container === c.id}
              onClick={() => onContainerChange(container === c.id ? null : c.id)}
            >
              <span className="inline-flex items-center gap-1" title={kindLabel(c.kind)}>
                <Icon aria-hidden className="h-3 w-3 shrink-0" />
                {c.name}
                {c.technologies[0] && (
                  <span className="text-[var(--color-text-tertiary)]">{techName(c.technologies[0])}</span>
                )}
              </span>
            </FilterChip>
          );
        })}
      </div>
    </div>
  );
}

function MapKeyBody({
  verbs,
  totalFiles,
  unclaimedFiles,
  selected,
  onSelectedChange,
  hasExternals,
}: Omit<ZoomMapKeyProps, "about" | "linksLegend" | "containers" | "container" | "onContainerChange">) {
  const t = useTranslations("zoom");
  const filtered = selected !== null;
  return (
    <>
      <div className="flex flex-col gap-2 border-t border-[var(--color-border-default)] pt-3">
        <p className="min-w-0 text-[13px] leading-relaxed text-[var(--color-text-secondary)]">
          {filtered ? (
            t.rich("mapKey.showingFiltered", {
              list: [...selected].join(", "),
              verbs: (chunks) => (
                <span className="text-[var(--color-text-primary)]">{chunks}</span>
              ),
            })
          ) : (
            t("mapKey.hoverHint")
          )}
          {unclaimedFiles > 0 &&
            /* Silence when nothing is missing: "0 not shown" is noise. */
            ` ${t("mapKey.unclaimed", {
              shown: formatNumber(unclaimedFiles),
              total: formatNumber(totalFiles + unclaimedFiles),
            })}`}
        </p>
        {verbs.length > 1 && (
          /* Counts sit on the chips so you can tell whether one is worth a
             click before you spend one. Already in the loaded map. */
          <div className="flex flex-wrap items-center gap-1.5">
            {verbs.map(({ verb, count }) => (
              <FilterChip
                key={verb}
                active={selected?.has(verb) ?? false}
                onClick={() => onSelectedChange(toggleVerb(selected, verb))}
              >
                {verb}{" "}
                <span className="font-mono tabular-nums text-[var(--color-text-tertiary)]">
                  {formatNumber(count)}
                </span>
              </FilterChip>
            ))}
            {filtered && (
              <button
                type="button"
                onClick={() => onSelectedChange(null)}
                className="rounded-md px-2 py-1 text-xs text-[var(--color-text-secondary)] underline-offset-2 transition-colors hover:text-[var(--color-text-primary)] hover:underline"
              >
                {t("mapKey.clear")}
              </button>
            )}
          </div>
        )}
      </div>

      {/* The card's marks. The corner number keeps the traffic light because
          the colours carry a band; the role icons use the canvas's inks
          (`theme.ts`). On-flow has no mark: nearly every file is on a flow. */}
      <dl className="flex flex-col gap-1.5 border-t border-[var(--color-border-default)] pt-3 text-[12px] text-[var(--color-text-tertiary)]">
        <div className="flex flex-col gap-1">
          <dt className="sr-only">{t("mapKey.roleIcons")}</dt>
          <dd className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[var(--color-text-secondary)]">
            <RoleIcon icon={Flame} className="text-[var(--color-risk-high)]" label={t("mapKey.hotspot")} />
            <RoleIcon
              icon={Play}
              className="fill-current text-[var(--color-accent-secondary)]"
              label={t("mapKey.entryPoint")}
            />
            <RoleIcon icon={Ban} className="text-[var(--color-text-muted)]" label={t("mapKey.deadCode")} />
          </dd>
          <dd>{t("mapKey.roleIconsHelp")}</dd>
        </div>
        <div className="flex items-center gap-1.5">
          <Dot className="bg-[var(--color-success)]" />
          <Dot className="bg-[var(--color-caution)]" />
          <Dot className="bg-[var(--color-warning)]" />
          <Dot className="bg-[var(--color-error)]" />
          <dt className="sr-only">{t("mapKey.cornerScore")}</dt>
          <dd>
            {t("mapKey.healthBand", {
              good: HEALTH_BAND_LABEL.good,
              goodMin: GOOD_MIN.toFixed(1),
              fair: HEALTH_BAND_LABEL.fair,
              fairMin: FAIR_MIN.toFixed(1),
              needsWork: HEALTH_BAND_LABEL.needs_work,
              needsWorkMin: NEEDS_WORK_MIN.toFixed(1),
              atRisk: HEALTH_BAND_LABEL.at_risk,
            })}
          </dd>
        </div>
        {hasExternals && (
          <div className="flex items-center gap-1.5">
            <span
              aria-hidden
              className="inline-block h-2.5 w-4 shrink-0 rounded-[3px] border border-dashed border-[var(--color-text-muted)]"
            />
            <dt className="sr-only">{t("mapKey.dashedCard")}</dt>
            <dd>{t("mapKey.externalsHelp")}</dd>
          </div>
        )}
      </dl>
    </>
  );
}
