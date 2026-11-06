"use client";

import { useEffect, useId, useState } from "react";
import { ArrowUpRight } from "lucide-react";
import { Sheet, SheetContent, SheetTitle } from "../ui/sheet";
import { Spinner } from "../ui/spinner";
import { cn } from "../lib/cn";
import {
  ProviderKeyEntry,
  type ProviderOption,
  type ProviderSettingsLabels,
} from "./provider-settings";
import { PASTE_SOURCE_ID, type ModelSource, type ModelSourceDetection } from "./model-sources";

export interface ModelUpgradeSheetLabels {
  eyebrow: string;
  title: string;
  description: string;
  close: string;
  sourcesLabel: string;
  cliSource: (name: string) => string;
  keySource: (name: string) => string;
  localSource: (name: string) => string;
  pasteSource: string;
  hostedSource: string;
  hostedNote: string;
  hostedAction: string;
  detected: Record<ModelSourceDetection, string>;
  pasteProvider: string;
  pages: (count: number) => string;
  counting: string;
  signedInNote: (name: string) => string;
  costNote: (cost: string) => string;
  pricing: string;
  keyNote: string;
  localNote: string;
  pasteNote: string;
  write: string;
  sameAs: string;
}

export const DEFAULT_MODEL_UPGRADE_SHEET_LABELS: ModelUpgradeSheetLabels = {
  eyebrow: "Add a model",
  title: "Write this documentation with a model",
  description:
    "Pages that explain how and why, chat in this dashboard, and written answers for your agent.",
  close: "Close",
  sourcesLabel: "Where the model comes from",
  cliSource: (name) => `${name} sign-in`,
  keySource: (name) => `${name} key`,
  localSource: (name) => `Local model (${name})`,
  pasteSource: "Paste a key",
  hostedSource: "Hosted repowise",
  hostedNote: "model included, no key",
  hostedAction: "See plans",
  detected: { signed_in: "Found", env: "In env", saved: "Saved" },
  pasteProvider: "Provider",
  pages: (count) => `${count} ${count === 1 ? "page" : "pages"} to write`,
  counting: "Counting pages",
  signedInNote: (name) => `Uses your ${name} sign-in.`,
  costNote: (cost) => `About ${cost} with this key, shown before anything runs.`,
  pricing: "Pricing with this key",
  keyNote: "The price shows before anything runs.",
  localNote: "Runs on your machine. Start Ollama first.",
  pasteNote: "With a key, the price shows here first.",
  write: "Write the docs",
  sameAs: "Same as",
};

export interface ModelUpgradeEstimate {
  /** Pages the run writes; null until counted. */
  pages: number | null;
  /** Formatted price for the selected key source; null when unpriced. */
  costText: string | null;
  loading: boolean;
}

export interface ModelUpgradeSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Rows in display order (see `orderModelSources`). */
  sources: ModelSource[];
  /** The source list is still loading. */
  loading?: boolean;
  selectedId: string;
  onSelect: (id: string) => void;
  /** Providers "Paste a key" offers. */
  keyProviders: ProviderOption[];
  /** Saves a pasted key; reject to keep the draft. */
  onAddKey: (providerId: string, apiKey: string) => Promise<void> | void;
  estimate: ModelUpgradeEstimate;
  onWrite: () => void;
  writing?: boolean;
  /** Where the hosted row links to (opens in a new tab). */
  hostedHref: string;
  labels?: Partial<ModelUpgradeSheetLabels>;
  /** Wording for the pasted-key field. */
  keyLabels?: Partial<ProviderSettingsLabels>;
}

/**
 * One sheet that adds a model to a repo indexed without one: what a model
 * adds, where it can come from on this machine, what the run costs, and the
 * action that writes the docs. Data and callbacks only; the host sets the
 * provider and runs generation. Plum (`--color-model`) is its only accent.
 */
export function ModelUpgradeSheet({
  open,
  onOpenChange,
  labels: labelOverrides,
  ...props
}: ModelUpgradeSheetProps) {
  const labels = { ...DEFAULT_MODEL_UPGRADE_SHEET_LABELS, ...labelOverrides };
  const selected = props.sources.find((s) => s.id === props.selectedId);
  const canWrite = Boolean(selected?.providerId) && !props.writing && !props.loading;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        closeLabel={labels.close}
        aria-describedby={undefined}
        // Full width on a phone, a side sheet from `sm` up.
        className="w-full gap-3 overflow-y-auto p-4 sm:w-[380px] sm:p-5"
      >
        <SheetHeading labels={labels} />
        <SourceList {...props} labels={labels} />
        <EstimateBlock selected={selected} estimate={props.estimate} labels={labels} />
        <button
          type="button"
          onClick={props.onWrite}
          disabled={!canWrite}
          className={cn(
            "inline-flex h-9 items-center justify-center gap-2 rounded-md px-4 text-sm font-semibold transition-colors",
            "bg-[var(--color-model)] text-[var(--color-text-on-model)] hover:bg-[var(--color-model-hover)]",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-model)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--color-bg-surface)]",
            "disabled:pointer-events-none disabled:opacity-50",
          )}
        >
          {props.writing && <Spinner size="sm" />}
          {labels.write}
        </button>
        <p className="text-[11px] text-[var(--color-text-tertiary)]">
          {labels.sameAs} <span className="font-mono">repowise generate</span>
        </p>
      </SheetContent>
    </Sheet>
  );
}

function SheetHeading({ labels }: { labels: ModelUpgradeSheetLabels }) {
  return (
    <div className="space-y-1.5 pr-8">
      <p className="text-[10px] font-semibold uppercase tracking-wider text-[var(--color-model)]">
        {labels.eyebrow}
      </p>
      <SheetTitle className="text-[15px] font-semibold leading-snug">{labels.title}</SheetTitle>
      <p className="text-xs leading-relaxed text-[var(--color-text-secondary)]">
        {labels.description}
      </p>
    </div>
  );
}

type SourceListProps = Omit<ModelUpgradeSheetProps, "open" | "onOpenChange" | "labels"> & {
  labels: ModelUpgradeSheetLabels;
};

function SourceList({ labels, ...props }: SourceListProps) {
  const name = useId();
  if (props.loading) {
    return (
      <div className="flex justify-center py-6">
        <Spinner size="sm" />
      </div>
    );
  }
  const choices = props.sources.filter((s) => s.kind !== "hosted");
  const hosted = props.sources.some((s) => s.kind === "hosted");
  return (
    <div className="flex flex-col gap-1.5">
      <div role="radiogroup" aria-label={labels.sourcesLabel} className="flex flex-col gap-1.5">
        {choices.map((source) => (
          <SourceRow
            key={source.id}
            name={name}
            source={source}
            checked={source.id === props.selectedId}
            onSelect={props.onSelect}
            labels={labels}
          />
        ))}
      </div>
      {props.selectedId === PASTE_SOURCE_ID && (
        <PasteKey
          providers={props.keyProviders}
          onAddKey={props.onAddKey}
          labels={labels}
          {...(props.keyLabels ? { keyLabels: props.keyLabels } : {})}
        />
      )}
      {hosted && <HostedRow href={props.hostedHref} labels={labels} />}
    </div>
  );
}

const ROW =
  "grid grid-cols-[14px_minmax(0,1fr)_auto] items-center gap-2.5 rounded-md border px-3 py-2 text-[13px] transition-colors";

function sourceLabel(source: ModelSource, labels: ModelUpgradeSheetLabels): string {
  if (source.kind === "cli") return labels.cliSource(source.name);
  if (source.kind === "key") return labels.keySource(source.name);
  if (source.kind === "local") return labels.localSource(source.name);
  return labels.pasteSource;
}

function SourceRow({
  name,
  source,
  checked,
  onSelect,
  labels,
}: {
  name: string;
  source: ModelSource;
  checked: boolean;
  onSelect: (id: string) => void;
  labels: ModelUpgradeSheetLabels;
}) {
  return (
    <label
      className={cn(
        ROW,
        "cursor-pointer bg-[var(--color-bg-elevated)] has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-[var(--color-model)]",
        checked
          ? "border-[var(--color-model)]"
          : "border-[var(--color-border-default)] hover:border-[var(--color-border-hover)]",
      )}
    >
      <input
        type="radio"
        name={name}
        value={source.id}
        checked={checked}
        onChange={() => onSelect(source.id)}
        className="sr-only"
      />
      <span
        aria-hidden
        className={cn(
          "h-3 w-3 rounded-full",
          checked
            ? "border-4 border-[var(--color-model)]"
            : "border-[1.5px] border-[var(--color-border-hover)]",
        )}
      />
      <span className="min-w-0 truncate text-[var(--color-text-primary)]">
        {sourceLabel(source, labels)}
      </span>
      {source.detected ? (
        <span className="text-[10px] font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)]">
          {labels.detected[source.detected]}
        </span>
      ) : (
        <span aria-hidden />
      )}
    </label>
  );
}

function HostedRow({ href, labels }: { href: string; labels: ModelUpgradeSheetLabels }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={cn(
        ROW,
        "border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] hover:border-[var(--color-border-hover)]",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-model)]",
      )}
    >
      <ArrowUpRight aria-hidden className="h-3.5 w-3.5 text-[var(--color-text-tertiary)]" />
      <span className="min-w-0 text-[var(--color-text-primary)]">
        {labels.hostedSource}{" "}
        <span className="text-[var(--color-text-tertiary)]">{labels.hostedNote}</span>
      </span>
      <span className="text-[11px] font-medium text-[var(--color-model)]">{labels.hostedAction}</span>
    </a>
  );
}

function PasteKey({
  providers,
  onAddKey,
  labels,
  keyLabels,
}: {
  providers: ProviderOption[];
  onAddKey: ModelUpgradeSheetProps["onAddKey"];
  labels: ModelUpgradeSheetLabels;
  keyLabels?: Partial<ProviderSettingsLabels>;
}) {
  const selectId = useId();
  const [providerId, setProviderId] = useState(providers[0]?.id ?? "");
  useEffect(() => {
    if (!providers.some((p) => p.id === providerId)) setProviderId(providers[0]?.id ?? "");
  }, [providers, providerId]);
  const provider = providers.find((p) => p.id === providerId);
  if (!provider) return null;
  return (
    <div className="rounded-md border border-[var(--color-border-default)] p-3">
      <label
        htmlFor={selectId}
        className="mb-1.5 block text-[11px] font-medium text-[var(--color-text-secondary)]"
      >
        {labels.pasteProvider}
      </label>
      <select
        id={selectId}
        value={providerId}
        onChange={(e) => setProviderId(e.target.value)}
        className="h-8 w-full rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-inset)] px-2 text-xs text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-model)]"
      >
        {providers.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>
      <ProviderKeyEntry
        key={provider.id}
        provider={provider}
        onAddKey={onAddKey}
        {...(keyLabels ? { labels: keyLabels } : {})}
      />
    </div>
  );
}

function estimateNote(
  selected: ModelSource | undefined,
  estimate: ModelUpgradeEstimate,
  labels: ModelUpgradeSheetLabels,
): string | null {
  switch (selected?.kind) {
    case "cli":
      return labels.signedInNote(selected.name);
    case "key":
      if (estimate.costText) return labels.costNote(estimate.costText);
      return estimate.loading ? labels.pricing : labels.keyNote;
    case "local":
      return labels.localNote;
    case "paste":
      return labels.pasteNote;
    default:
      return null;
  }
}

function EstimateBlock({
  selected,
  estimate,
  labels,
}: {
  selected: ModelSource | undefined;
  estimate: ModelUpgradeEstimate;
  labels: ModelUpgradeSheetLabels;
}) {
  const note = estimateNote(selected, estimate, labels);
  const count =
    estimate.pages != null ? labels.pages(estimate.pages) : estimate.loading ? labels.counting : null;
  return (
    <div
      aria-live="polite"
      className="space-y-1 border-t border-[var(--color-border-default)] pt-3 text-xs text-[var(--color-text-secondary)]"
    >
      {count && (
        <p className="font-mono text-[11.5px] text-[var(--color-text-primary)]">{count}</p>
      )}
      {note && <p>{note}</p>}
    </div>
  );
}
