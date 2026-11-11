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

/** A value in the page's words: "unset" and "(none)" come from the labels. */
export function displayValue(value: unknown, labels: SettingsRegistryLabels): string {
  return formatSettingValue(value, { unset: labels.unset, none: labels.none });
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

/**
 * Where the value came from (only when set somewhere: the default is the quiet
 * state), the suggested value, and how it differs from the default.
 */
function RowMarks({
  setting,
  suggestion,
  labels,
}: {
  setting: RegistrySetting;
  suggestion: RegistrySuggestion | undefined;
  labels: SettingsRegistryLabels;
}) {
  const marks: React.ReactNode[] = [];
  if (setting.source !== "default") {
    marks.push(
      <span
        key="source"
        className="font-mono text-[10px] uppercase tracking-[0.08em] text-[var(--color-text-secondary)]"
      >
        {labels.sources[setting.source] ?? setting.source}
      </span>,
    );
  }
  if (suggestion) {
    marks.push(
      <span key="suggested" className="text-[var(--color-text-secondary)]">
        {labels.suggested(displayValue(suggestion.suggested_value, labels))}
      </span>,
    );
  }
  if (setting.changed) {
    marks.push(
      <span key="changed" className="text-[var(--color-text-secondary)]">
        {labels.changed} · {labels.defaultValue(displayValue(setting.default, labels))}
      </span>,
    );
  } else if (setting.default_note) {
    marks.push(<span key="note">{setting.default_note}</span>);
  }
  if (setting.source === "env" && setting.scope !== "env" && setting.env) {
    marks.push(<span key="env">{labels.envOverride(setting.env.name)}</span>);
  }
  if (marks.length === 0) return null;
  return <span className="mt-1 flex flex-wrap items-center gap-x-3">{marks}</span>;
}

/** What the setting does, then anything that needs saying about its value. */
function RowHint({
  setting,
  suggestion,
  readOnly,
  link,
  labels,
}: {
  setting: RegistrySetting;
  suggestion: RegistrySuggestion | undefined;
  readOnly: string | null;
  link: RegistryLink | null | undefined;
  labels: SettingsRegistryLabels;
}) {
  return (
    <>
      {setting.description}
      <RowMarks setting={setting} suggestion={suggestion} labels={labels} />
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
  );
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

  // Only the newest write's answer reaches the page, so two quick writes that
  // answer out of order cannot leave the older value showing.
  const write = async (run: () => Promise<RegistryChange>) => {
    let change: RegistryChange | null = null;
    const result = await track(
      async () => {
        change = await run();
      },
      (e) => (e instanceof Error && e.message ? e.message : labels.saveFailed),
    );
    if (result === "saved" && change) onChanged(change);
    return result;
  };

  return (
    <SettingsRow
      className={cn("group/row", suggestion && "-mx-3 rounded-md bg-[var(--color-selection-wash)] px-3")}
      {...(setting.type === "mapping" ? {} : { htmlFor: id })}
      label={<span className="break-all font-mono text-[13px]">{setting.key}</span>}
      saveState={state}
      saveError={error}
      hint={
        <RowHint
          setting={setting}
          suggestion={suggestion}
          readOnly={readOnly}
          link={link}
          labels={labels}
        />
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

/** A field's text as the setting's value; null for a number that does not read as one. */
function parseDraft(text: string, type: RegistrySetting["type"]): unknown {
  if (type === "int" || type === "float") return parseNumber(text, type);
  if (type === "list") {
    return text
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
  }
  return text;
}

/** The input attributes a number, text or list field takes. */
function fieldProps(setting: RegistrySetting, labels: SettingsRegistryLabels) {
  const { type } = setting;
  if (type === "int" || type === "float") {
    return {
      type: "number",
      inputMode: type === "int" ? ("numeric" as const) : ("decimal" as const),
      step: type === "int" ? 1 : "any",
      min: setting.minimum ?? undefined,
      max: setting.maximum ?? undefined,
      placeholder: labels.unset,
      className: "w-32 font-mono",
    };
  }
  return {
    type: "text",
    placeholder: type === "list" ? labels.listPlaceholder : labels.unset,
    className: "w-full font-mono sm:w-64",
  };
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
        {displayValue(value, labels)}
      </code>
    );
  }
  return (
    <DraftInput id={id} setting={setting} disabled={disabled} labels={labels} onCommit={onCommit} />
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
  const saved = setting.type === "list" ? listText(setting.value) : scalarText(setting.value);
  const [draft, setDraft] = React.useState(saved);
  React.useEffect(() => setDraft(saved), [saved]);

  async function commit() {
    if (draft === saved) return;
    // A number that does not read as one goes back to the saved value; the
    // field's type and bounds already say what it takes.
    const next = parseDraft(draft, setting.type);
    if (next === null || (await onCommit(next)) === "failed") setDraft(saved);
  }

  return (
    <Input
      id={id}
      {...fieldProps(setting, labels)}
      value={draft}
      disabled={disabled}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => void commit()}
      onKeyDown={(e) => {
        if (e.key === "Enter") void commit();
        if (e.key === "Escape") setDraft(saved);
      }}
    />
  );
}
