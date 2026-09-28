"use client";

/**
 * The map's section-header controls: which edge kinds are drawn, and whether
 * services or whole repositories are the nodes. Only kinds present in the
 * graph are offered, each with its count, so the row never shows a dead
 * filter. Pure controlled components; state lives in the map.
 */

import { useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import type { SystemEdgeKind } from "@repowise-dev/types";
import { EDGE_KIND_ORDER, SYSTEM_EDGE_KINDS } from "./edge-kinds";

export const MICRO_LABEL =
  "font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]";

export interface SegmentOption<T extends string> {
  value: T;
  label: string;
  /** Figure after the label, e.g. a finding count. */
  count?: string | undefined;
  /** Set to disable the option; the reason is its tooltip and accessible description. */
  disabledReason?: string | undefined;
  hint?: string | undefined;
}

/**
 * One axis, one control: a radiogroup of mutually exclusive options. A
 * disabled option stays visible with its reason rather than vanishing, so the
 * reader learns the lens exists and why it has nothing to show.
 */
export function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: SegmentOption<T>[];
  onChange: (value: T) => void;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const [reason, setReason] = useState<string | null>(null);
  // The roving tab stop: the active option, or the first enabled one when the
  // active option is disabled, so the group never drops out of the tab order.
  const activeIndex = options.findIndex((o) => o.value === value && !o.disabledReason);
  const tabStop = activeIndex >= 0 ? activeIndex : Math.max(options.findIndex((o) => !o.disabledReason), 0);

  // Arrows walk every option, disabled ones included, so a keyboard reader can
  // reach a disabled lens and hear why; only enabled ones are selected.
  const onKeyDown = (e: ReactKeyboardEvent) => {
    const i = refs.current.findIndex((el) => el === document.activeElement);
    const from = i >= 0 ? i : tabStop;
    let next: number;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") next = (from + 1) % options.length;
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = (from - 1 + options.length) % options.length;
    else return;
    e.preventDefault();
    refs.current[next]?.focus();
    const target = options[next];
    if (target && !target.disabledReason) onChange(target.value);
  };

  return (
    <div className="flex min-w-0 flex-col gap-1">
      <div
        role="radiogroup"
        aria-label={label}
        onKeyDown={onKeyDown}
        className="inline-flex max-w-full flex-wrap self-start rounded-md border border-[var(--color-border-default)] p-0.5"
      >
        {options.map((o, i) => {
          const active = o.value === value && !o.disabledReason;
          const reasonId = o.disabledReason ? `sm-reason-${label}-${o.value}`.replace(/\s+/g, "-") : undefined;
          const show = o.disabledReason ? () => setReason(o.disabledReason ?? null) : undefined;
          return (
            <button
              key={o.value}
              ref={(el) => {
                refs.current[i] = el;
              }}
              type="button"
              role="radio"
              aria-checked={active}
              aria-disabled={o.disabledReason ? true : undefined}
              aria-describedby={reasonId}
              tabIndex={i === tabStop ? 0 : -1}
              title={o.disabledReason ?? o.hint}
              onClick={() => (o.disabledReason ? show?.() : onChange(o.value))}
              onMouseEnter={show}
              onFocus={show}
              onMouseLeave={o.disabledReason ? () => setReason(null) : undefined}
              onBlur={o.disabledReason ? () => setReason(null) : undefined}
              className={`inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)] ${
                active
                  ? "bg-[var(--color-bg-elevated)] font-semibold text-[var(--color-text-primary)]"
                  : o.disabledReason
                    ? "cursor-not-allowed font-medium text-[var(--color-text-tertiary)]"
                    : "font-medium text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
              }`}
            >
              {o.label}
              {o.count !== undefined && (
                <span className="font-mono text-[10px] tabular-nums text-[var(--color-text-tertiary)]">{o.count}</span>
              )}
              {reasonId && (
                <span id={reasonId} className="sr-only">
                  {o.disabledReason}
                </span>
              )}
            </button>
          );
        })}
      </div>
      {reason && <p className="text-xs text-[var(--color-text-tertiary)]">{reason}</p>}
    </div>
  );
}

export interface SystemMapFiltersProps {
  /** Edges per kind in the drawn shape (before the kind filter); only these kinds are offered. */
  kindCounts: ReadonlyMap<SystemEdgeKind, number>;
  visibleKinds: ReadonlySet<SystemEdgeKind>;
  onToggleKind: (kind: SystemEdgeKind) => void;
  /** Whether the repo view would differ from the service view; the switch hides otherwise. */
  canCollapse: boolean;
  collapsed: boolean;
  onCollapsedChange: (collapsed: boolean) => void;
}

export function SystemMapFilters({
  kindCounts,
  visibleKinds,
  onToggleKind,
  canCollapse,
  collapsed,
  onCollapsedChange,
}: SystemMapFiltersProps) {
  const kinds = EDGE_KIND_ORDER.filter((k) => (kindCounts.get(k) ?? 0) > 0);

  return (
    <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-2">
      {kinds.length > 0 && (
        <div className="flex min-w-0 flex-wrap items-center gap-1.5" role="group" aria-label="Edge kinds drawn">
          <span className={`${MICRO_LABEL} mr-1`}>Edges</span>
          {kinds.map((kind) => {
            const s = SYSTEM_EDGE_KINDS[kind];
            const Icon = s.icon;
            const active = visibleKinds.has(kind);
            return (
              <button
                key={kind}
                type="button"
                onClick={() => onToggleKind(kind)}
                aria-pressed={active}
                title={`Toggle ${s.label} edges`}
                className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)] ${
                  active
                    ? "border-[var(--color-border-hover)] bg-[var(--color-bg-surface)] text-[var(--color-text-primary)]"
                    : "border-dashed border-[var(--color-border-default)] text-[var(--color-text-tertiary)] line-through"
                }`}
              >
                <Icon size={12} aria-hidden />
                {s.label}
                <span className="font-mono text-[10px] tabular-nums text-[var(--color-text-tertiary)] no-underline">
                  {kindCounts.get(kind)}
                </span>
              </button>
            );
          })}
        </div>
      )}
      {canCollapse && (
        <Segmented
          label="Nodes"
          value={collapsed ? "repos" : "services"}
          options={[
            { value: "services", label: "Services", hint: "One node per detected service" },
            { value: "repos", label: "Repositories", hint: "Group services into one node per repository" },
          ]}
          onChange={(v) => onCollapsedChange(v === "repos")}
        />
      )}
    </div>
  );
}
