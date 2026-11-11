"use client";

import * as React from "react";
import { Lock, RotateCcw } from "lucide-react";
import { cn } from "../lib/cn";
import { FIELD_CLASS, Input } from "../ui/input";
import { Switch } from "../ui/switch";
import { RowPromptAction } from "../health/row-prompt-action";
import { SettingsRow, useSaveState } from "./settings-primitives";
import { formatSettingValue } from "./setting-prompt";
import type { SettingsRegistryLabels } from "./settings-registry-labels";
import type {
  RegistryChange,
  RegistryLink,
  RegistrySetting,
  RegistrySuggestion,
} from "./settings-registry-types";

/** Why the control is disabled, or null when this page may change the setting. */
function readOnlyReason(setting: RegistrySetting, labels: SettingsRegistryLabels): string | null {
  if (setting.locked) return setting.locked_reason || labels.lockedReason;
  if (setting.writable !== false) return null;
  if (setting.scope === "env") return labels.envOnly(setting.env?.name ?? setting.key);
  return labels.userSetting(setting.key);
}

function SourceMark({ source, labels }: { source: string; labels: SettingsRegistryLabels }) {
  // The default is the quiet state; only a value set somewhere gets a mark.
  if (source === "default") return null;
  return (
    <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-[var(--color-text-secondary)]">
      {labels.sources[source] ?? source}
    </span>
  );
}

export interface SettingsRegistryRowProps {
  setting: RegistrySetting;
  labels: SettingsRegistryLabels;
  suggestion?: RegistrySuggestion | undefined;
  link?: RegistryLink | null;
  onSet: (key: string, value: unknown) => Promise<RegistryChange>;
  onReset: (key: string) => Promise<RegistryChange>;
  onPrompt: (setting: RegistrySetting) => void;
  /** Applies the write's result to the page's state. */
  onChanged: (change: RegistryChange) => void;
}

/** One setting: name and help on the left; its control, reset and prompt on the right. */
export function SettingsRegistryRow({
  setting,
  labels,
  suggestion,
  link,
  onSet,
  onReset,
  onPrompt,
  onChanged,
}: SettingsRegistryRowProps) {
  const id = React.useId();
  const { state, error, track } = useSaveState();
  const readOnly = readOnlyReason(setting, labels);
  // Reset only clears what the file holds; an environment value is not the page's to undo.
  const canReset = !readOnly && setting.source !== "default" && setting.source !== "env";

  const write = (run: () => Promise<RegistryChange>) =>
    track(
      async () => onChanged(await run()),
      (e) => (e instanceof Error && e.message ? e.message : labels.saveFailed),
    );

  const meta: React.ReactNode[] = [];
  if (setting.changed) {
    meta.push(
      <span key="changed" className="text-[var(--color-text-secondary)]">
        {labels.changed} · {labels.defaultValue(formatSettingValue(setting.default))}
      </span>,
    );
  } else if (setting.default_note) {
    meta.push(<span key="note">{setting.default_note}</span>);
  }
  if (setting.source === "env" && setting.scope !== "env" && setting.env) {
    meta.push(<span key="env">{labels.envOverride(setting.env.name)}</span>);
  }

  return (
    <SettingsRow
      className={cn("group/row", suggestion && "bg-[var(--color-selection-wash)] px-3 -mx-3 rounded-md")}
      {...(setting.type === "mapping" ? {} : { htmlFor: id })}
      label={
        <span className="inline-flex flex-wrap items-center gap-2">
          <span className="break-all font-mono text-[13px]">{setting.key}</span>
          <SourceMark source={setting.source} labels={labels} />
          {suggestion && (
            <span className="text-xs font-normal text-[var(--color-text-secondary)]">
              {labels.suggested(formatSettingValue(suggestion.suggested_value))}
            </span>
          )}
        </span>
      }
      saveState={state}
      saveError={error}
      hint={
        <>
          {setting.description}
          {meta.length > 0 && <span className="mt-1 flex flex-wrap gap-x-3">{meta}</span>}
          {setting.problem && (
            <span role="note" className="mt-1 block text-[var(--color-warning)]">
              {setting.problem}
            </span>
          )}
          {readOnly && (
            <span className="mt-1 flex items-center gap-1">
              {setting.locked && <Lock aria-hidden className="h-3 w-3" />}
              {readOnly}
            </span>
          )}
          {link && (
            <a
              href={link.href}
              className="mt-1 block font-medium text-[var(--color-text-primary)] underline decoration-[var(--color-border-hover)] underline-offset-[3px] hover:decoration-[var(--color-text-primary)]"
            >
              {link.label}
            </a>
          )}
        </>
      }
    >
      <div className="flex flex-wrap items-center gap-2 sm:justify-end">
        <SettingControl
          id={id}
          setting={setting}
          disabled={!!readOnly}
          labels={labels}
          onCommit={(value) => write(() => onSet(setting.key, value))}
        />
        {canReset && (
          <button
            type="button"
            onClick={() => void write(() => onReset(setting.key))}
            aria-label={labels.resetAria(setting.key)}
            className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-[var(--color-text-tertiary)] transition-colors hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
          >
            <RotateCcw aria-hidden className="h-3 w-3" />
            {labels.reset}
          </button>
        )}
        <RowPromptAction
          label={labels.copyPrompt}
          subject={setting.key}
          onClick={() => onPrompt(setting)}
        />
      </div>
    </SettingsRow>
  );
}

/** Text a list setting is edited as. */
function listText(value: unknown): string {
  return Array.isArray(value) ? value.map(String).join(", ") : "";
}

function scalarText(value: unknown): string {
  return value === null || value === undefined ? "" : String(value);
}

function parseNumber(text: string, type: "int" | "float"): number | null {
  if (!text.trim()) return null;
  const n = Number(text);
  if (!Number.isFinite(n)) return null;
  return type === "int" && !Number.isInteger(n) ? null : n;
}

/** The control a setting's type calls for. Text fields write on blur or Enter. */
function SettingControl({
  id,
  setting,
  disabled,
  labels,
  onCommit,
}: {
  id: string;
  setting: RegistrySetting;
  disabled: boolean;
  labels: SettingsRegistryLabels;
  onCommit: (value: unknown) => Promise<"saved" | "failed" | "stale">;
}) {
  const { type, value } = setting;

  if (type === "bool") {
    return (
      <Switch
        id={id}
        checked={value === true}
        disabled={disabled}
        onCheckedChange={(checked) => void onCommit(checked)}
      />
    );
  }
  if (type === "str" && setting.choices?.length) {
    const current = scalarText(value);
    return (
      <select
        id={id}
        value={current}
        disabled={disabled}
        onChange={(e) => void onCommit(e.target.value)}
        className={cn(FIELD_CLASS, "h-9 min-w-[10rem] px-2 text-sm")}
      >
        {!setting.choices.includes(current) && <option value="">{labels.unset}</option>}
        {setting.choices.map((choice) => (
          <option key={choice} value={choice}>
            {choice}
          </option>
        ))}
      </select>
    );
  }
  if (type === "mapping") {
    return (
      <code className="max-w-[18rem] overflow-x-auto whitespace-pre rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-inset)] px-2 py-1 font-mono text-xs text-[var(--color-text-secondary)]">
        {formatSettingValue(value)}
      </code>
    );
  }
  return (
    <DraftInput
      id={id}
      setting={setting}
      disabled={disabled}
      labels={labels}
      onCommit={onCommit}
    />
  );
}

/** A number, text or list field: edits stay local until blur or Enter, and roll back on failure. */
function DraftInput({
  id,
  setting,
  disabled,
  labels,
  onCommit,
}: {
  id: string;
  setting: RegistrySetting;
  disabled: boolean;
  labels: SettingsRegistryLabels;
  onCommit: (value: unknown) => Promise<"saved" | "failed" | "stale">;
}) {
  const { type, value } = setting;
  const saved = type === "list" ? listText(value) : scalarText(value);
  const [draft, setDraft] = React.useState(saved);
  React.useEffect(() => setDraft(saved), [saved]);

  const parse = (text: string): unknown => {
    if (type === "int" || type === "float") return parseNumber(text, type);
    if (type === "list") {
      return text
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean);
    }
    return text;
  };

  async function commit() {
    if (draft === saved) return;
    const next = parse(draft);
    if (next === null) {
      setDraft(saved);
      return;
    }
    if ((await onCommit(next)) === "failed") setDraft(saved);
  }

  const numeric = type === "int" || type === "float";
  return (
    <Input
      id={id}
      type={numeric ? "number" : "text"}
      inputMode={numeric ? (type === "int" ? "numeric" : "decimal") : undefined}
      step={type === "int" ? 1 : numeric ? "any" : undefined}
      min={setting.minimum ?? undefined}
      max={setting.maximum ?? undefined}
      value={draft}
      disabled={disabled}
      placeholder={type === "list" ? labels.listPlaceholder : labels.unset}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => void commit()}
      onKeyDown={(e) => {
        if (e.key === "Enter") void commit();
        if (e.key === "Escape") setDraft(saved);
      }}
      className={cn(numeric ? "w-32" : "w-full sm:w-64", "font-mono")}
    />
  );
}
