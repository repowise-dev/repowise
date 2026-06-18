"use client";

import * as React from "react";
import { cn } from "../lib/cn";
import { scoreBadgeClass } from "./tokens";
import type { CodeHealthMapFile } from "./code-health-map";

/** Labelled <select> used across the Triage queue filters. */
export function FilterSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <label className="inline-flex items-center gap-1.5 text-xs text-[var(--color-text-tertiary)]">
      <span className="uppercase tracking-wider">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="text-xs px-2 py-1 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] text-[var(--color-text-primary)]"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

/** Toggleable filter chip (Hotspots / Untested / Failing). */
export function FilterChip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "text-xs rounded-md px-2 py-1 border transition-colors",
        active
          ? "bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)] border-[var(--color-accent-primary)]/50"
          : "border-[var(--color-border-default)] text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] hover:border-[var(--color-border-strong)]",
      )}
    >
      {children}
    </button>
  );
}

/** Two-state segmented toggle (Queue / All files). */
export function ViewToggle<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
}) {
  return (
    <div className="flex rounded-md border border-[var(--color-border-default)] overflow-hidden text-xs">
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          onClick={() => onChange(opt.value)}
          className={cn(
            "px-3 py-1.5 font-medium transition-colors",
            value === opt.value
              ? "bg-[var(--color-accent-primary)] text-[var(--color-text-inverse)]"
              : "bg-transparent text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)]",
          )}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

/** The inspected file (last bubble hovered in the map) shown in the rail. */
export function FileSpotlight({
  file,
  onOpen,
}: {
  file: CodeHealthMapFile | null;
  onOpen: (path: string) => void;
}) {
  if (!file) {
    return (
      <div className="rounded-lg border border-dashed border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-3 text-xs text-[var(--color-text-tertiary)]">
        Hover a bubble to inspect a file; click it to open the full breakdown.
      </div>
    );
  }
  const name = file.file_path.split("/").pop() ?? file.file_path;
  return (
    <div className="space-y-2 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-3">
      <div className="flex items-center gap-2">
        <span
          className={cn(
            "inline-flex items-center justify-center rounded px-1.5 py-0.5 text-xs font-semibold",
            scoreBadgeClass(file.score),
          )}
        >
          {file.score.toFixed(1)}
        </span>
        <span
          className="truncate text-sm font-medium text-[var(--color-text-primary)]"
          title={file.file_path}
        >
          {name}
        </span>
      </div>
      <div
        className="truncate font-mono text-xs text-[var(--color-text-tertiary)]"
        title={file.file_path}
      >
        {file.file_path}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-[var(--color-text-secondary)]">
        <span>{file.nloc.toLocaleString()} NLOC</span>
        {file.line_coverage_pct != null ? (
          <span>{Math.round(file.line_coverage_pct)}% coverage</span>
        ) : null}
        <span>{file.has_test_file ? "has tests" : "untested"}</span>
        {file.module ? <span className="truncate">{file.module}</span> : null}
      </div>
      <button
        type="button"
        onClick={() => onOpen(file.file_path)}
        className="w-full rounded-md border border-[var(--color-border-default)] px-2 py-1.5 text-xs font-medium text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]"
      >
        Open details
      </button>
    </div>
  );
}
