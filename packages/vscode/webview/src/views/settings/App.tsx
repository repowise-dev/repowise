/**
 * Settings panel: a friendly, grouped surface over the `repowise.*` workspace
 * settings, opened from the sidebar Home launcher. Every control writes through
 * one host RPC (`updateSetting`) which validates and persists to the workspace,
 * so the change reaches the same live features the native Settings editor would
 * drive. The "Editor signals" group up top is the reason this panel exists:
 * one place to quiet the squiggles, gutter, and explorer badges.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ExternalLink, RotateCcw, Settings2 } from "lucide-react";
import {
  SaveIndicator,
  SettingsRow,
  SettingsRows,
  type SaveState,
} from "@repowise-dev/ui/settings/settings-primitives";
import { OverviewSection } from "@repowise-dev/ui/overview";
import { Input } from "@repowise-dev/ui/ui/input";
import { Switch } from "@repowise-dev/ui/ui/switch";
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
  const [saveState, setSaveState] = useState<SaveState>("idle");

  /** Monotonic stamp per write. A response that is no longer the newest is
   *  dropped: this panel is exactly where someone flips four switches in two
   *  seconds, and the host echoes the whole canonical map back, so an older
   *  reply landing last silently un-does the newer one. */
  const seq = useRef(0);
  const savedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (savedTimer.current) clearTimeout(savedTimer.current);
    },
    [],
  );

  const load = useCallback(() => {
    host.api
      .getSettings()
      .then((v) => {
        setValues(v);
        setError(null);
      })
      .catch(() => {
        setError("Could not load settings.");
        // The indicator is the only thing that speaks on this surface, so a
        // failed read has to reach it too, not just a failed write.
        setSaveState("error");
      });
  }, [host]);

  useEffect(() => load(), [load, refreshToken]);

  const commit = useCallback(
    (key: SettingKey, value: SettingValue) => {
      // Optimistic: the control reflects the new value immediately; the host
      // echoes the canonical map back, and a rejected write reverts.
      setValues((prev) => (prev ? { ...prev, [key]: value } : prev));
      const stamp = ++seq.current;
      if (savedTimer.current) clearTimeout(savedTimer.current);
      setSaveState("saving");
      host.api
        .updateSetting(key, value)
        .then((fresh) => {
          if (stamp !== seq.current) return;
          setValues(fresh);
          setError(null);
          setSaveState("saved");
          savedTimer.current = setTimeout(() => setSaveState("idle"), 2000);
        })
        .catch((e: unknown) => {
          if (stamp !== seq.current) return;
          setError(e instanceof Error ? e.message : "Could not save that setting.");
          setSaveState("error");
          // Roll the control back to what the server actually has. Re-reads
          // values only: `load()` would clear the error it was called to
          // explain, leaving the indicator on the generic fallback and losing
          // the host's real reason ("port 7777 already in use").
          host.api
            .getSettings()
            .then(setValues)
            .catch(() => undefined);
        });
    },
    [host],
  );

  if (!values) {
    return (
      <div className="flex h-full items-center justify-center text-[var(--color-text-tertiary)]">
        {error ?? "Loading settings…"}
      </div>
    );
  }

  return (
    <div className="mx-auto flex min-h-full max-w-3xl flex-col gap-6 bg-[var(--color-bg-root)] px-6 py-6 sm:gap-8">
      <header className="flex items-center gap-2.5">
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]">
          <Settings2 className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <h1 className="text-[22px] font-semibold tracking-tight text-[var(--color-text-primary)]">
            Repowise Settings
          </h1>
          <p className="text-xs text-[var(--color-text-tertiary)]">
            Saved to this workspace. Changes apply immediately.
          </p>
        </div>
        {/* The one save affordance: nothing at rest, a spinner in flight,
            "Saved" for two seconds, the error until it is fixed. It is
            `aria-live="polite"`, which is right for "Saved" and wrong for a
            rejected write, so the failure keeps the assertive announcement the
            banner this replaced had. */}
        <SaveIndicator state={saveState} error={error} className="shrink-0" />
        <span role="alert" className="sr-only">
          {saveState === "error" ? (error ?? "Could not save") : ""}
        </span>
        <button
          type="button"
          onClick={() => host.openNativeSettings()}
          title="Open these settings in the VS Code Settings editor (search, sync, per-scope overrides)"
          className="flex shrink-0 items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] px-2.5 py-1.5 text-xs font-medium text-[var(--color-text-secondary)] transition-colors hover:border-[var(--color-border-hover)] hover:text-[var(--color-text-primary)]"
        >
          <ExternalLink className="h-3.5 w-3.5" />
          Open in Settings editor
        </button>
      </header>

      {GROUPS.map((group) => (
        <OverviewSection key={group.title} title={group.title} description={group.blurb}>
          <SettingsRows>
            {group.rows.map((row) => (
              <SettingRow
                key={row.key}
                row={row}
                values={values}
                disabled={row.needs != null && values[row.needs] === false}
                onCommit={commit}
              />
            ))}
          </SettingsRows>
        </OverviewSection>
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
  // Rows gated by a parent toggle are indented under it behind a left
  // hairline, so a dimmed child reads as "controlled by the switch above"
  // rather than as broken. It holds at every width: in a narrow editor column
  // an indent that only starts at `sm` leaves the gated row looking broken at
  // exactly the size where that reading matters most.
  const indented = row.needs != null;
  return (
    <SettingsRow
      label={row.label}
      htmlFor={controlId}
      hint={row.description}
      className={
        (disabled ? "opacity-50 " : "") +
        (indented ? "border-l border-[var(--color-border-default)] pl-4" : "")
      }
    >
      <div className="flex sm:justify-end">
        <Control
          id={controlId}
          row={row}
          value={value}
          disabled={disabled}
          onCommit={(v) => onCommit(row.key, v)}
        />
      </div>
    </SettingsRow>
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
      return (
        // The shared switch draws its off state as a white thumb on
        // `bg-elevated` with a transparent border, which on the light ramp is
        // cream on cream. Give the off state a real border so the control is
        // visible at rest — and so `.hc` can reach it, since a transparent
        // border cannot pick up `--vscode-contrastBorder`.
        <Switch
          id={id}
          checked={value === true}
          disabled={disabled}
          onCheckedChange={onCommit}
          className="border data-[state=unchecked]:border-[var(--color-border-hover)] data-[state=unchecked]:bg-[var(--color-bg-inset)]"
        />
      );
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

/**
 * The native `<select>` is kept where the shared Radix `Select` is not.
 *
 * Everything else here is now the shared control. This one stays because it
 * buys nothing: it themes through the same tokens either way, and the Radix
 * version renders through a portal, which is a real behaviour to take on inside
 * a webview in exchange for no visual difference at these three call sites.
 */
const CONTROL_CLASS =
  "h-9 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 text-xs text-[var(--color-text-primary)] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--color-accent-primary)] disabled:cursor-not-allowed disabled:opacity-50";

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
              "rounded-full border px-2 py-0.5 text-xs transition-colors disabled:cursor-not-allowed " +
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
    <Input
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
      className="w-24 text-right"
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
    <Input
      id={id}
      type="text"
      inputMode="numeric"
      placeholder="auto"
      value={draft}
      disabled={disabled}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
      className="w-24 text-right"
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
      <Input
        id={id}
        type="text"
        value={draft}
        placeholder={placeholder}
        disabled={disabled}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
        className="w-44"
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
