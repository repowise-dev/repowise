"use client";

/**
 * The C4 section of the detail sheet: what the selected box is built with,
 * which deployable(s) it belongs to, and which external systems its code
 * reaches, with counts. The page indexes the map's C4 data once
 * (`useZoomC4`) and provides it through `ZoomC4Provider`, so the sheet mounts
 * this with the node alone.
 */

import { createContext, useContext, useMemo } from "react";
import { useTranslations } from "next-intl";
import {
  containerKindLabel,
  containerMembers,
  externalKindLabel,
  externalName,
  externalsReached,
  indexExternals,
  techName,
  type ExternalIndex,
  type ExternalReach,
  type ZoomContainer,
  type ZoomMap,
  type ZoomNode,
} from "@repowise-dev/ui/zoom";
import { CONTAINER_KINDS, containerIcon, EXTERNAL_KINDS, externalIcon } from "./c4-icons";

export interface ZoomC4Data {
  externals: ExternalIndex;
  containers: ReadonlyMap<string, ZoomContainer>;
}

const ZoomC4Context = createContext<ZoomC4Data | null>(null);

export const ZoomC4Provider = ZoomC4Context.Provider;

const NO_CONTAINERS: ZoomContainer[] = [];

/**
 * The page's C4 state, indexed once per map: the sheet's data, the containers
 * largest first for the map key, and the node ids of the picked container
 * (one set per pick, so the canvas compares it by identity).
 */
export function useZoomC4(map: ZoomMap | undefined, container: string | null) {
  const c4 = useMemo<ZoomC4Data | null>(() => {
    if (!map) return null;
    const containers = new Map((map.containers ?? NO_CONTAINERS).map((c) => [c.id, c] as const));
    return { externals: indexExternals(map), containers };
  }, [map]);
  const containers = useMemo(
    () => [...(map?.containers ?? NO_CONTAINERS)].sort((a, b) => b.file_count - a.file_count),
    [map],
  );
  // A pick the current map no longer has (a refetch) dims nothing.
  const highlightIds = useMemo(
    () =>
      map && container && containers.some((c) => c.id === container)
        ? containerMembers(map.nodes, container)
        : null,
    [map, container, containers],
  );
  return { c4, containers, highlightIds, hasExternals: (map?.externals?.length ?? 0) > 0 };
}

/** A container kind's label, translated where the kind is a known one. */
export function useContainerKindLabel(): (kind: string) => string {
  const t = useTranslations("zoom.c4");
  return (kind) => (CONTAINER_KINDS.has(kind) ? t(`kind.${kind}`) : containerKindLabel(kind));
}

function Micro({ children }: { children: React.ReactNode }) {
  return (
    <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
      {children}
    </span>
  );
}

const ROW = "flex min-w-0 items-center gap-2 text-[13px]";
const ICON = "h-3.5 w-3.5 shrink-0 text-[var(--color-text-tertiary)]";

function Technologies({ names }: { names: readonly string[] }) {
  const t = useTranslations("zoom.c4");
  return (
    <div>
      <Micro>{t("technologies")}</Micro>
      <div className="mt-1.5 flex flex-wrap gap-1">
        {names.map((name) => (
          <span
            key={name}
            className="rounded-full border border-[var(--color-border-default)] px-2 py-0.5 text-[11px] font-medium text-[var(--color-text-secondary)]"
          >
            {techName(name)}
          </span>
        ))}
      </div>
    </div>
  );
}

function Containers({ list }: { list: readonly ZoomContainer[] }) {
  const t = useTranslations("zoom.c4");
  const kindLabel = useContainerKindLabel();
  return (
    <div>
      <Micro>{list.length === 1 ? t("container") : t("containers", { count: list.length })}</Micro>
      <ul className="mt-1.5 flex flex-col gap-1">
        {list.map((c) => {
          const Icon = containerIcon(c.kind);
          return (
            <li key={c.id} className={ROW}>
              <Icon aria-hidden className={ICON} />
              <span className="truncate text-[var(--color-text-primary)]" title={c.path || c.name}>
                {c.name}
              </span>
              <span className="ml-auto shrink-0 text-[12px] text-[var(--color-text-tertiary)]">
                {kindLabel(c.kind)}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function Externals({ reached }: { reached: readonly ExternalReach[] }) {
  const t = useTranslations("zoom.c4");
  const kind = (k: string) => (EXTERNAL_KINDS.has(k) ? t(`external.${k}`) : externalKindLabel(k));
  return (
    <div>
      <Micro>{t("externals")}</Micro>
      <ul className="mt-1.5 flex flex-col gap-1">
        {reached.map(({ external, edgeCount }) => {
          const Icon = externalIcon(external.kind);
          return (
            <li key={external.id} className={ROW}>
              <Icon aria-hidden className={ICON} />
              <span className="truncate text-[var(--color-text-primary)]">{externalName(external)}</span>
              <span className="text-[12px] text-[var(--color-text-tertiary)]">{kind(external.kind)}</span>
              <span className="ml-auto shrink-0 text-[12px] tabular-nums text-[var(--color-text-secondary)]">
                {t("imports", { count: edgeCount })}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function ZoomDetailC4({ node }: { node: ZoomNode }) {
  const c4 = useContext(ZoomC4Context);
  if (!c4) return null;
  // A file's language already has its own row in the sheet.
  const tech = node.technologies ?? [];
  const containers = (node.container_ids ?? [])
    .map((id) => c4.containers.get(id))
    .filter((c): c is ZoomContainer => c !== undefined);
  const reached = externalsReached(c4.externals, node);
  if (tech.length + containers.length + reached.length === 0) return null;

  return (
    <div className="mt-3 flex flex-col gap-3 border-t border-[var(--color-border-default)] pt-3">
      {tech.length > 0 && <Technologies names={tech} />}
      {containers.length > 0 && <Containers list={containers} />}
      {reached.length > 0 && <Externals reached={reached} />}
    </div>
  );
}
