"use client";

import * as React from "react";
import * as HoverCardPrimitive from "@radix-ui/react-hover-card";
import { cn } from "../../lib/cn";
import type { EntityMeta, EntityRef } from "./types";
import { ENTITY_KIND_LABEL } from "./routes";

interface EntityHoverCardProps {
  entity: EntityRef;
  meta?: EntityMeta | undefined;
  children: React.ReactNode;
  /** Render the card open immediately (tests / previews). */
  defaultOpen?: boolean | undefined;
}

/**
 * Wraps any element with a hover preview card for the given entity.
 * Card content is purely presentational — the caller supplies whatever
 * metadata is already in scope. Missing fields gracefully degrade.
 */
export function EntityHoverCard({ entity, meta, children, defaultOpen }: EntityHoverCardProps) {
  return (
    <HoverCardPrimitive.Root openDelay={250} closeDelay={120} defaultOpen={defaultOpen ?? false}>
      <HoverCardPrimitive.Trigger asChild>{children}</HoverCardPrimitive.Trigger>
      <HoverCardPrimitive.Portal>
        <HoverCardPrimitive.Content
          align="start"
          sideOffset={6}
          className={cn(
            "z-50 w-72 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]",
            "p-3 text-xs shadow-lg outline-none",
            "data-[state=open]:animate-in data-[state=closed]:animate-out",
          )}
        >
          <HoverCardBody entity={entity} meta={meta} />
        </HoverCardPrimitive.Content>
      </HoverCardPrimitive.Portal>
    </HoverCardPrimitive.Root>
  );
}

function HoverCardBody({ entity, meta }: { entity: EntityRef; meta?: EntityMeta | undefined }) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
          {ENTITY_KIND_LABEL[entity.kind]}
        </span>
      </div>
      <p className="font-mono text-[11px] leading-snug text-[var(--color-text-primary)] break-all">
        {entity.id}
      </p>
      {meta && meta.kind === entity.kind && <MetaSection meta={meta} />}
    </div>
  );
}

function MetaSection({ meta }: { meta: EntityMeta }) {
  switch (meta.kind) {
    case "file":
      return <FileMeta data={meta.data} />;
    case "symbol":
      return <SymbolMeta data={meta.data} />;
    case "decision":
      return <DecisionMeta data={meta.data} />;
    case "owner":
      return <OwnerMeta data={meta.data} />;
    case "commit":
      return <CommitMeta data={meta.data} />;
  }
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  if (value === null || value === undefined || value === "") return null;
  return (
    <div className="flex items-baseline justify-between gap-3">
      <span className="text-[var(--color-text-tertiary)]">{label}</span>
      <span className="text-[var(--color-text-primary)] tabular-nums">{value}</span>
    </div>
  );
}

function FileMeta({ data }: { data?: import("./types").FileEntityMeta | undefined }) {
  if (!data) return null;
  return (
    <div className="space-y-1">
      {data.summary && (
        <p className="text-[11px] leading-snug text-[var(--color-text-secondary)] line-clamp-3">
          {data.summary}
        </p>
      )}
      {data.tags && data.tags.length > 0 && (
        <div className="flex flex-wrap gap-1 pb-1">
          {data.tags.slice(0, 6).map((tag) => (
            <Pill key={tag} tone="info">
              {tag}
            </Pill>
          ))}
        </div>
      )}
      <Row label="Owner" value={data.owner ?? null} />
      <Row
        label="Churn"
        value={
          data.churnPercentile != null
            ? `${Math.round(data.churnPercentile)}th %ile`
            : null
        }
      />
      <Row label="Bus factor" value={data.busFactor ?? null} />
      {data.language && <Row label="Language" value={data.language} />}
      <div className="flex flex-wrap gap-1 pt-1">
        {data.hasDocs === false && <Pill tone="warn">No docs</Pill>}
        {data.hasDeadCode && <Pill tone="error">Dead code</Pill>}
      </div>
    </div>
  );
}

function SymbolMeta({ data }: { data?: import("./types").SymbolEntityMeta | undefined }) {
  if (!data) return null;
  return (
    <div className="space-y-1">
      {data.signature && (
        <p className="font-mono text-[10px] text-[var(--color-text-secondary)] break-all">
          {data.signature}
        </p>
      )}
      <Row label="Complexity" value={data.complexity ?? null} />
      <Row label="Callers" value={data.callerCount ?? null} />
      <Row label="Visibility" value={data.visibility ?? null} />
      {data.isAsync && (
        <div className="pt-1">
          <Pill tone="info">async</Pill>
        </div>
      )}
    </div>
  );
}

function DecisionMeta({ data }: { data?: import("./types").DecisionEntityMeta | undefined }) {
  if (!data) return null;
  const stale = data.stalenessScore;
  return (
    <div className="space-y-1">
      <Row label="Status" value={data.status ?? null} />
      <Row label="Source" value={data.source ?? null} />
      {stale != null && (
        <Row label="Staleness" value={`${Math.round(stale * 100)}%`} />
      )}
    </div>
  );
}

function OwnerMeta({ data }: { data?: import("./types").OwnerEntityMeta | undefined }) {
  if (!data) return null;
  return (
    <div className="space-y-1">
      {data.email && (
        <p className="font-mono text-[10px] text-[var(--color-text-secondary)] break-all">
          {data.email}
        </p>
      )}
      <Row label="Bus-factor files" value={data.busFactorFiles ?? null} />
      {data.topFiles && data.topFiles.length > 0 && (
        <div className="pt-1">
          <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
            Top files
          </p>
          <ul className="mt-0.5 space-y-0.5">
            {data.topFiles.slice(0, 3).map((f) => (
              <li
                key={f}
                className="font-mono text-[10px] text-[var(--color-text-secondary)] truncate"
                title={f}
              >
                {f}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function CommitMeta({ data }: { data?: import("./types").CommitEntityMeta | undefined }) {
  if (!data) return null;
  return (
    <div className="space-y-1">
      <Row label="Author" value={data.author ?? null} />
      <Row label="Date" value={data.date ?? null} />
      {data.message && (
        <p className="text-[11px] text-[var(--color-text-secondary)] line-clamp-2">
          {data.message}
        </p>
      )}
    </div>
  );
}

function Pill({
  children,
  tone,
}: {
  children: React.ReactNode;
  tone: "info" | "warn" | "error";
}) {
  const cls =
    tone === "error"
      ? "border-[var(--color-error)]/40 bg-[var(--color-error)]/10 text-[var(--color-error)]"
      : tone === "warn"
        ? "border-[var(--color-warning)]/40 bg-[var(--color-warning)]/10 text-[var(--color-warning)]"
        : "border-[var(--color-info)]/40 bg-[var(--color-info)]/10 text-[var(--color-info)]";
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-1.5 py-0.5 text-[10px] font-medium",
        cls,
      )}
    >
      {children}
    </span>
  );
}
