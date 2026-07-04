/**
 * Settings panel: a friendly, grouped surface over the `repowise.*` workspace
 * settings, opened from the sidebar Home launcher. Every control writes through
 * one host RPC (`updateSetting`) which validates and persists to the workspace,
 * so the change reaches the same live features the native Settings editor would
 * drive. The "Editor signals" group up top is the reason this panel exists:
 * one place to quiet the squiggles, gutter, and explorer badges.
 */

import { useCallback, useEffect, useState } from "react";
import { ExternalLink, RotateCcw, Settings2 } from "lucide-react";
import type {
  SettingKey,
  SettingsValues,
  SettingValue,
} from "../../../../src/shared/webviewMessages";
import type { ViewProps } from "../../runtime/mount";
import type { WebviewHost } from "../../runtime/rpc";

interface Option {
  value: string;
  label: string;
}

type Row =
  | { key: SettingKey; label: string; description: string; kind: "toggle"; needs?: SettingKey }
  | { key: SettingKey; label: string; description: string; kind: "select"; options: Option[]; needs?: SettingKey }
  | { key: SettingKey; label: string; description: string; kind: "multiselect"; options: Option[]; needs?: SettingKey }
  | { key: SettingKey; label: string; description: string; kind: "number"; min: number; max: number; step: number; needs?: SettingKey }
  | { key: SettingKey; label: string; description: string; kind: "port"; needs?: SettingKey }
  | { key: SettingKey; label: string; description: string; kind: "text"; placeholder: string; needs?: SettingKey };

interface Group {
  title: string;
  blurb: string;
  rows: Row[];
}

/** Hand-curated grouping over the package.json contribution keys. */
const GROUPS: Group[] = [
  {
    title: "Editor signals",
    blurb:
      "What Repowise paints into your editor and file explorer. Turn any of these off to quiet the surface.",
    rows: [
      {
        key: "diagnostics.enabled",
        label: "Problems panel findings",
        description:
          "Show health findings for visible files in the Problems panel (the squiggles under flagged code). Off by default to keep the panel quiet; the gutter, explorer badges, and hovers surface findings either way.",
        kind: "toggle",
      },
      {
        key: "diagnostics.minSeverity",
        label: "Minimum severity",
        description:
          "Lowest finding severity surfaced in the Problems panel. Lower-severity findings stay visible in the gutter and tree views.",
        kind: "select",
        needs: "diagnostics.enabled",
        options: [
          { value: "critical", label: "Critical" },
          { value: "high", label: "High" },
          { value: "medium", label: "Medium" },
          { value: "low", label: "Low" },
        ],
      },
      {
        key: "diagnostics.dimensions",
        label: "Problem dimensions",
        description: "Which health dimensions appear in the Problems panel.",
        kind: "multiselect",
        needs: "diagnostics.enabled",
        options: [
          { value: "defect", label: "Defect" },
          { value: "maintainability", label: "Maintainability" },
          { value: "performance", label: "Performance" },
        ],
      },
      {
        key: "gutterHeat.enabled",
        label: "Gutter heat",
        description: "Shade the gutter next to lines with health findings in visible editors.",
        kind: "toggle",
      },
      {
        key: "fileDecorations.enabled",
        label: "Explorer badges",
        description: "Badge the worst-health files in the file explorer.",
        kind: "toggle",
      },
      {
        key: "fileDecorations.maxScore",
        label: "Badge threshold",
        description: "Badge files whose defect score is at or below this value (0 to 10).",
        kind: "number",
        needs: "fileDecorations.enabled",
        min: 0,
        max: 10,
        step: 0.5,
      },
      {
        key: "codeLens.enabled",
        label: "Refactoring lenses",
        description:
          "Show refactoring plan lenses above symbols with a detected refactoring opportunity.",
        kind: "toggle",
      },
      {
        key: "hover.enabled",
        label: "Hover cards",
        description: "Show file health, ownership, and decision context on hover.",
        kind: "toggle",
      },
      {
        key: "hover.symbolDetail",
        label: "Symbol details on hover",
        description:
          "Enrich symbol hovers with caller/callee counts, ownership, and governing decisions. Fetched only when you hover, then cached.",
        kind: "toggle",
        needs: "hover.enabled",
      },
    ],
  },
  {
    title: "Change intelligence",
    blurb: "Quiet hints drawn from how this repository's files change together.",
    rows: [
      {
        key: "changeIntel.cochangeNudge",
        label: "Co-change nudge",
        description:
          "Show a quiet status-bar hint when files you are editing usually change together with a file you have not touched. Advisory and dismissible; never interrupts.",
        kind: "toggle",
      },
      {
        key: "changeIntel.cochangeMinScore",
        label: "Minimum co-change count",
        description:
          "Minimum historical co-change count before a related file is surfaced by the nudge. Higher values mean fewer, stronger hints.",
        kind: "number",
        needs: "changeIntel.cochangeNudge",
        min: 0,
        max: 100,
        step: 1,
      },
    ],
  },
  {
    title: "Agent integration",
    blurb: "How Repowise shares its index and plans with your AI agent.",
    rows: [
      {
        key: "agentHandoff.enabled",
        label: "Agent handoff actions",
        description:
          "Offer lightbulb code actions that hand a detected refactoring plan to your AI agent, alongside the copy-to-clipboard action.",
        kind: "toggle",
      },
      {
        key: "agentTools.enabled",
        label: "Chat tools",
        description:
          "Expose Repowise index lookups (search, health, plans, risk, symbols, docs) as native tools your editor's AI chat can call.",
        kind: "toggle",
      },
    ],
  },
  {
    title: "Server",
    blurb: "How the extension connects to the local Repowise server.",
    rows: [
      {
        key: "server.autoStart",
        label: "Auto-start server",
        description:
          "Whether to start the local Repowise server automatically when a Repowise index is present.",
        kind: "select",
        options: [
          { value: "ask", label: "Ask first" },
          { value: "always", label: "Always" },
          { value: "never", label: "Never" },
        ],
      },
      {
        key: "server.port",
        label: "Server port",
        description:
          "Port the local server listens on. Leave empty to discover it from the running server's lockfile.",
        kind: "port",
      },
      {
        key: "cliPath",
        label: "CLI path",
        description:
          "Absolute path to the repowise CLI executable. Leave empty to use 'repowise' from your PATH.",
        kind: "text",
        placeholder: "repowise",
      },
    ],
  },
  {
    title: "Branch risk",
    blurb: "Defaults for branch risk scoring.",
    rows: [
      {
        key: "risk.baseBranch",
        label: "Base branch",
        description:
          "Base branch for branch risk scoring. Leave empty to use the repository's default branch.",
        kind: "text",
        placeholder: "main",
      },
    ],
  },
];

export function App({ host, refreshToken }: ViewProps<"settings">) {
  const [values, setValues] = useState<SettingsValues | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    host.api
      .getSettings()
      .then((v) => {
        setValues(v);
        setError(null);
      })
      .catch(() => setError("Could not load settings."));
  }, [host]);

  useEffect(() => load(), [load, refreshToken]);

  const commit = useCallback(
    (key: SettingKey, value: SettingValue) => {
      // Optimistic: the control reflects the new value immediately; the host
      // echoes the canonical map back, and a rejected write reverts.
      setValues((prev) => (prev ? { ...prev, [key]: value } : prev));
      host.api
        .updateSetting(key, value)
        .then((fresh) => {
          setValues(fresh);
          setError(null);
        })
        .catch((e: unknown) => {
          setError(e instanceof Error ? e.message : "Could not save that setting.");
          load();
        });
    },
    [host, load],
  );

  if (!values) {
    return (
      <div className="flex h-full items-center justify-center text-[var(--color-text-tertiary)]">
        {error ?? "Loading settings…"}
      </div>
    );
  }

  return (
    <div className="mx-auto min-h-full max-w-3xl space-y-6 bg-[var(--color-bg-root)] px-6 py-6">
      <header className="flex items-center gap-2.5">
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]">
          <Settings2 className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <h1 className="text-base font-semibold text-[var(--color-text-primary)]">
            Repowise Settings
          </h1>
          <p className="text-xs text-[var(--color-text-tertiary)]">
            Saved to this workspace. Changes apply immediately.
          </p>
        </div>
        <button
          type="button"
          onClick={() => host.openNativeSettings()}
          title="Open these settings in the VS Code Settings editor (search, sync, per-scope overrides)"
          className="flex shrink-0 items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] px-2.5 py-1.5 text-[11px] font-medium text-[var(--color-text-secondary)] transition-colors hover:border-[var(--color-border-hover)] hover:text-[var(--color-text-primary)]"
        >
          <ExternalLink className="h-3.5 w-3.5" />
          Open in Settings editor
        </button>
      </header>

      {error ? (
        <p
          role="alert"
          className="rounded-lg border border-[var(--color-error)] bg-[var(--color-bg-surface)] px-3 py-2 text-xs text-[var(--color-error)]"
        >
          {error}
        </p>
      ) : null}

      {GROUPS.map((group) => (
        <section
          key={group.title}
          className="rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)]"
        >
          <div className="border-b border-[var(--color-border-default)] px-4 py-3">
            <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">
              {group.title}
            </h2>
            <p className="mt-0.5 text-[11px] text-[var(--color-text-tertiary)]">{group.blurb}</p>
          </div>
          <div className="divide-y divide-[var(--color-border-default)]">
            {group.rows.map((row) => (
              <SettingRow
                key={row.key}
                row={row}
                values={values}
                disabled={row.needs != null && values[row.needs] === false}
                onCommit={commit}
              />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

function SettingRow({
  row,
  values,
  disabled,
  onCommit,
}: {
  row: Row;
  values: SettingsValues;
  disabled: boolean;
  onCommit: (key: SettingKey, value: SettingValue) => void;
}) {
  const value = values[row.key];
  const controlId = `set-${row.key.replace(/\./g, "-")}`;
  // Rows gated by a parent toggle are indented under it with an elbow connector,
  // so a dimmed child reads as "controlled by the switch above", not broken.
  const indented = row.needs != null;
  return (
    <div
      className={
        (disabled ? "opacity-50 " : "") +
        "flex items-start gap-3 py-3 pr-4 " +
        (indented ? "pl-8" : "pl-4")
      }
    >
      {indented ? (
        <span
          aria-hidden
          className="mt-1 h-3 w-3 shrink-0 rounded-bl border-b border-l border-[var(--color-border-default)]"
        />
      ) : null}
      <div className="min-w-0 flex-1">
        <label htmlFor={controlId} className="block text-[13px] font-medium text-[var(--color-text-primary)]">
          {row.label}
        </label>
        <p className="mt-0.5 text-[11px] leading-relaxed text-[var(--color-text-tertiary)]">
          {row.description}
        </p>
      </div>
      <div className="shrink-0 pt-0.5">
        <Control
          id={controlId}
          row={row}
          value={value}
          disabled={disabled}
          onCommit={(v) => onCommit(row.key, v)}
        />
      </div>
    </div>
  );
}

function Control({
  id,
  row,
  value,
  disabled,
  onCommit,
}: {
  id: string;
  row: Row;
  value: SettingValue;
  disabled: boolean;
  onCommit: (value: SettingValue) => void;
}) {
  switch (row.kind) {
    case "toggle":
      return <Toggle id={id} checked={value === true} disabled={disabled} onChange={onCommit} />;
    case "select":
      return (
        <Select
          id={id}
          value={typeof value === "string" ? value : ""}
          options={row.options}
          disabled={disabled}
          onChange={onCommit}
        />
      );
    case "multiselect":
      return (
        <MultiSelect
          value={Array.isArray(value) ? value : []}
          options={row.options}
          disabled={disabled}
          onChange={onCommit}
        />
      );
    case "number":
      return (
        <NumberInput
          id={id}
          value={typeof value === "number" ? value : row.min}
          min={row.min}
          max={row.max}
          step={row.step}
          disabled={disabled}
          onCommit={onCommit}
        />
      );
    case "port":
      return (
        <PortInput
          id={id}
          value={typeof value === "number" ? value : null}
          disabled={disabled}
          onCommit={onCommit}
        />
      );
    case "text":
      return (
        <TextInput
          id={id}
          value={typeof value === "string" ? value : ""}
          placeholder={row.placeholder}
          disabled={disabled}
          onCommit={onCommit}
        />
      );
  }
}

function Toggle({
  id,
  checked,
  disabled,
  onChange,
}: {
  id: string;
  checked: boolean;
  disabled: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <button
      id={id}
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={
        // The off state carries a border and a filled knob so the switch stays
        // visible on a light card, where bg-inset alone is nearly the surface color.
        "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border transition-colors disabled:cursor-not-allowed " +
        (checked
          ? "border-[var(--color-accent-primary)] bg-[var(--color-accent-primary)]"
          : "border-[var(--color-border-default)] bg-[var(--color-bg-inset)]")
      }
    >
      <span
        className={
          "inline-block h-4 w-4 transform rounded-full shadow-sm transition-transform " +
          (checked ? "translate-x-4 bg-white" : "translate-x-0.5 bg-[var(--color-text-tertiary)]")
        }
      />
    </button>
  );
}

const CONTROL_CLASS =
  "rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-inset)] px-2 py-1 text-[12px] text-[var(--color-text-primary)] focus:border-[var(--color-accent-primary)] focus:outline-none disabled:cursor-not-allowed";

function Select({
  id,
  value,
  options,
  disabled,
  onChange,
}: {
  id: string;
  value: string;
  options: Option[];
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <select
      id={id}
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
      className={CONTROL_CLASS}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

function MultiSelect({
  value,
  options,
  disabled,
  onChange,
}: {
  value: string[];
  options: Option[];
  disabled: boolean;
  onChange: (value: string[]) => void;
}) {
  const toggle = (opt: string) => {
    const next = value.includes(opt) ? value.filter((v) => v !== opt) : [...value, opt];
    onChange(next);
  };
  return (
    <div className="flex flex-wrap justify-end gap-1.5">
      {options.map((o) => {
        const on = value.includes(o.value);
        return (
          <button
            key={o.value}
            type="button"
            disabled={disabled}
            aria-pressed={on}
            onClick={() => toggle(o.value)}
            className={
              "rounded-full border px-2 py-0.5 text-[11px] transition-colors disabled:cursor-not-allowed " +
              (on
                ? "border-[var(--color-accent-primary)] bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]"
                : "border-[var(--color-border-default)] text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]")
            }
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

function NumberInput({
  id,
  value,
  min,
  max,
  step,
  disabled,
  onCommit,
}: {
  id: string;
  value: number;
  min: number;
  max: number;
  step: number;
  disabled: boolean;
  onCommit: (value: number) => void;
}) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => setDraft(String(value)), [value]);
  const commit = () => {
    const n = Number(draft);
    if (!Number.isFinite(n)) return setDraft(String(value));
    const clamped = Math.min(max, Math.max(min, n));
    if (clamped !== value) onCommit(clamped);
    setDraft(String(clamped));
  };
  return (
    <input
      id={id}
      type="number"
      inputMode="decimal"
      value={draft}
      min={min}
      max={max}
      step={step}
      disabled={disabled}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
      className={CONTROL_CLASS + " w-20 text-right"}
    />
  );
}

function PortInput({
  id,
  value,
  disabled,
  onCommit,
}: {
  id: string;
  value: number | null;
  disabled: boolean;
  onCommit: (value: number | null) => void;
}) {
  const [draft, setDraft] = useState(value == null ? "" : String(value));
  useEffect(() => setDraft(value == null ? "" : String(value)), [value]);
  const commit = () => {
    const trimmed = draft.trim();
    if (trimmed === "") {
      if (value !== null) onCommit(null);
      return;
    }
    const n = Number(trimmed);
    if (!Number.isInteger(n) || n < 1 || n > 65535) {
      setDraft(value == null ? "" : String(value));
      return;
    }
    if (n !== value) onCommit(n);
  };
  return (
    <input
      id={id}
      type="text"
      inputMode="numeric"
      placeholder="auto"
      value={draft}
      disabled={disabled}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
      className={CONTROL_CLASS + " w-20 text-right"}
    />
  );
}

function TextInput({
  id,
  value,
  placeholder,
  disabled,
  onCommit,
}: {
  id: string;
  value: string;
  placeholder: string;
  disabled: boolean;
  onCommit: (value: string) => void;
}) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  const commit = () => {
    if (draft !== value) onCommit(draft);
  };
  return (
    <span className="flex items-center gap-1.5">
      <input
        id={id}
        type="text"
        value={draft}
        placeholder={placeholder}
        disabled={disabled}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
        className={CONTROL_CLASS + " w-44"}
      />
      {value !== "" ? (
        <button
          type="button"
          title="Clear"
          disabled={disabled}
          onClick={() => onCommit("")}
          className="text-[var(--color-text-tertiary)] transition-colors hover:text-[var(--color-text-primary)] disabled:cursor-not-allowed"
        >
          <RotateCcw className="h-3.5 w-3.5" />
        </button>
      ) : null}
    </span>
  );
}
