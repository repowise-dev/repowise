"use client";

/**
 * The Knowledge Graph's `C4 diagram` view: a classic C4 drawing of the system
 * at three levels (context, containers, components), framed to fit. It mounts
 * the shared `<C4Diagram>` (React Flow + ELK) and adds what the web page owns:
 * the data, the container picker for the component level, the key with the
 * weaker-links switch, and the node colours mapped onto the design tokens.
 */

import { useCallback, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { C4Diagram, type C4Level } from "@repowise-dev/ui/c4";
import type { ZoomContainer } from "@repowise-dev/ui/zoom";
import { getC4Mermaid } from "@/lib/api/c4";
import { useC4Components, useC4Context } from "@/lib/hooks/use-c4";
import { strongLinks, withContainerKinds } from "./c4-links";

/**
 * The node tones on the design tokens, through the `--c4-<tone>-<part>` seam
 * the shared node shell reads (its literal palette is the fallback). Persons
 * are solid ink, containers paper with an ink border, libraries the same with
 * a quiet border, externals muted.
 */
const C4_TONES = {
  "--c4-person-bg": "var(--color-text-primary)",
  "--c4-person-border": "var(--color-text-primary)",
  "--c4-person-band": "var(--color-text-primary)",
  "--c4-person-band-text": "var(--color-text-inverse)",
  "--c4-person-text": "var(--color-text-inverse)",
  "--c4-system-bg": "var(--color-zoom-card-fill)",
  "--c4-system-border": "var(--color-text-primary)",
  "--c4-system-band": "var(--color-text-primary)",
  "--c4-system-band-text": "var(--color-text-inverse)",
  "--c4-system-text": "var(--color-text-primary)",
  "--c4-container-bg": "var(--color-zoom-card-fill)",
  "--c4-container-border": "var(--color-text-primary)",
  "--c4-container-band": "var(--color-text-primary)",
  "--c4-container-band-text": "var(--color-text-inverse)",
  "--c4-container-text": "var(--color-text-primary)",
  "--c4-library-bg": "var(--color-zoom-card-fill)",
  "--c4-library-border": "var(--color-border-hover)",
  "--c4-library-band": "var(--color-bg-inset)",
  "--c4-library-band-text": "var(--color-text-secondary)",
  "--c4-library-text": "var(--color-text-primary)",
  "--c4-component-bg": "var(--color-zoom-card-fill)",
  "--c4-component-border": "var(--color-text-secondary)",
  "--c4-component-band": "var(--color-text-secondary)",
  "--c4-component-band-text": "var(--color-text-inverse)",
  "--c4-component-text": "var(--color-text-primary)",
  "--c4-external-bg": "var(--color-bg-inset)",
  "--c4-external-border": "var(--color-border-default)",
  "--c4-external-band": "var(--color-border-subtle)",
  "--c4-external-band-text": "var(--color-text-tertiary)",
  "--c4-external-text": "var(--color-text-secondary)",
} as React.CSSProperties;

export function C4View({
  repoId,
  systemName,
  containers,
}: {
  repoId: string;
  /** Fallback name for the system until the context view names it. */
  systemName: string;
  /** The zoom map's containers, for their kind and technologies. */
  containers: readonly ZoomContainer[] | undefined;
}) {
  const [level, setLevel] = useState<C4Level>(2);
  const [picked, setPicked] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  // Containers for the picker come from L2, largest first; the component level
  // opens on the largest one below the repository root (the root workspace
  // holds everything else's files, so it says least) until one is picked.
  const { l1, l2: l2Res } = useC4Context(repoId);
  const l2 = useMemo(
    () => (l2Res.data ? withContainerKinds(l2Res.data, containers) : null),
    [l2Res.data, containers],
  );
  const options = useMemo(
    () => [...(l2?.containers ?? [])].sort((a, b) => b.file_count - a.file_count),
    [l2],
  );
  const containerId = picked ?? (options.find((c) => c.path !== "") ?? options[0])?.id ?? null;
  const l3 = useC4Components(repoId, level === 3 ? containerId : null);

  const shownL2 = useMemo(() => (l2 ? (showAll ? { view: l2, hidden: 0 } : strongLinks(l2)) : null), [l2, showAll]);
  const shownL3 = useMemo(
    () => (l3.data ? (showAll ? { view: l3.data, hidden: 0 } : strongLinks(l3.data)) : null),
    [l3.data, showAll],
  );
  const hidden = level === 2 ? shownL2?.hidden : level === 3 ? shownL3?.hidden : undefined;
  // "Show all" stays offered once on, so it can be turned back off.
  const filterable = level !== 1 && (showAll || (hidden ?? 0) > 0);

  const current = level === 1 ? l1 : level === 2 ? l2Res : l3;

  const drillInto = useCallback((id: string) => {
    setPicked(id);
    setLevel(3);
  }, []);
  const drillOut = useCallback(() => setLevel((l) => (l === 3 ? 2 : l)), []);
  const fetchMermaid = useCallback(
    () => getC4Mermaid(repoId, level, level === 3 ? containerId : null),
    [repoId, level, containerId],
  );

  return (
    <div style={C4_TONES} className="h-full w-full">
      <C4Diagram
        level={level}
        activeContainerId={containerId}
        systemName={l1.data?.system.name ?? systemName}
        l1View={l1.data ?? null}
        l2View={shownL2?.view ?? null}
        l3View={shownL3?.view ?? null}
        loading={current.isLoading}
        error={(current.error as Error | undefined) ?? null}
        fetchMermaid={fetchMermaid}
        onLevelChange={setLevel}
        onDrillInto={drillInto}
        onDrillOut={drillOut}
        toolbarExtra={
          level === 3 && options.length > 0 ? (
            <ContainerPicker
              value={containerId}
              options={options}
              onChange={setPicked}
            />
          ) : null
        }
        legend={
          <C4Key
            hidden={filterable ? (hidden ?? 0) : null}
            showAll={showAll}
            onShowAllChange={setShowAll}
          />
        }
      />
    </div>
  );
}

function ContainerPicker({
  value,
  options,
  onChange,
}: {
  value: string | null;
  options: readonly { id: string; name: string }[];
  onChange: (id: string) => void;
}) {
  const t = useTranslations("views.knowledgeGraph.c4");
  return (
    <label className="inline-flex items-center gap-2 text-xs text-[var(--color-text-secondary)]">
      {t("containerPicker")}
      <select
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-md border border-[var(--color-field-border)] bg-[var(--color-field-bg)] px-2 py-1 text-xs text-[var(--color-text-primary)] hover:border-[var(--color-field-border-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
      >
        {options.map((c) => (
          <option key={c.id} value={c.id}>
            {c.name}
          </option>
        ))}
      </select>
    </label>
  );
}

const SWATCH = "inline-block h-3 w-[18px] shrink-0 rounded-[3px]";

/** The key: one swatch per box kind, drawn from the same variables as the nodes. */
function C4Key({
  hidden,
  showAll,
  onShowAllChange,
}: {
  /** Weaker links left off at this level; null when there is nothing to switch. */
  hidden: number | null;
  showAll: boolean;
  onShowAllChange: (next: boolean) => void;
}) {
  const t = useTranslations("views.knowledgeGraph.c4");
  const items: { label: string; hint?: string; style: React.CSSProperties }[] = [
    { label: t("key.person"), style: { background: "var(--c4-person-bg)" } },
    {
      label: t("key.container"),
      hint: t("key.containerHint"),
      style: { background: "var(--c4-container-bg)", border: "1.5px solid var(--c4-container-border)" },
    },
    {
      label: t("key.library"),
      hint: t("key.libraryHint"),
      style: { background: "var(--c4-library-bg)", border: "1.5px solid var(--c4-library-border)" },
    },
    {
      label: t("key.external"),
      style: { background: "var(--c4-external-bg)", border: "1.5px solid var(--c4-external-border)" },
    },
    { label: t("key.boundary"), style: { border: "1.5px dashed var(--color-diagram-cluster-border)" } },
  ];
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-[var(--color-text-secondary)]">
      {items.map((it) => (
        <span key={it.label} title={it.hint} className="inline-flex items-center gap-1.5">
          <span aria-hidden className={SWATCH} style={it.style} />
          {it.label}
        </span>
      ))}
      {hidden !== null && (
        <span className="inline-flex items-center gap-1.5 text-[var(--color-text-tertiary)]">
          {!showAll && t("hiddenLinks", { count: hidden })}
          <button
            type="button"
            onClick={() => onShowAllChange(!showAll)}
            className="font-medium text-[var(--color-text-secondary)] underline-offset-2 hover:text-[var(--color-text-primary)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
          >
            {showAll ? t("hideWeak") : t("showAll")}
          </button>
        </span>
      )}
    </div>
  );
}
