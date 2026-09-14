"use client";

import * as React from "react";
import { DECISION_PRESETS } from "@repowise-dev/types/decisions";
import type {
  DecisionSettings,
  DecisionSettingsUpdate,
  DecisionSourceState,
} from "@repowise-dev/types/decisions";
import { ApiError } from "../shared/api-error";

/**
 * What captures decisions, and whether any of it may call a model.
 *
 * Every row here comes from the source registry the engine resolves, delivered
 * whole by `GET /decisions/settings`. Nothing in this file lists a source, a
 * preset or a status word: a second list would be a second registry, and the
 * one drift this layer has already suffered was exactly that.
 *
 * Autosave, per the form archetype: a switch is one whole-object write and
 * there is nothing to batch. The write is optimistic and rolls back on
 * failure, and the etag the payload carries is passed straight back, so a
 * change made in `.repowise/config.yaml` or by `repowise decision source set`
 * while this page was open is reported as a conflict rather than overwritten.
 *
 * Presentation and pending state only. The host owns the fetch and the write.
 */

export interface DecisionCaptureSettingsProps {
  settings: DecisionSettings | undefined;
  /**
   * Applies a partial policy change. Rejecting rolls the control back; a
   * rejection carrying a 409 should be reported through `conflict`.
   */
  onChange: (update: DecisionSettingsUpdate) => Promise<void>;
  /**
   * Why this surface cannot write, e.g. a read-only snapshot or a repository
   * whose config it does not own. Set it and every control is disabled with
   * the reason beside it, and the CLI route to make the change is shown.
   * Never leave a live-looking switch that explains itself after it is
   * pressed.
   */
  readOnlyReason?: string;
  error?: unknown;
  isLoading?: boolean;
  onRetry?: () => void;
}

const SECTION = "space-y-3 border-t border-[var(--color-border-subtle)] pt-5";
const MICRO_LABEL =
  "font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]";

/**
 * What each preset is for. The names come from the shared vocabulary; only
 * these sentences are local, and a preset with no sentence still renders as a
 * button rather than disappearing.
 */
const PRESET_BLURB: Record<string, string> = {
  default: "The deterministic sources, and no broad model pass.",
  off: "Capture nothing. Decisions you record by hand still work.",
  local_only: "Only what this checkout can see, with no model call at all.",
  balanced: "The deterministic sources plus one broad pass over new sessions.",
  full: "Every source, model stages included.",
};

export function DecisionCaptureSettings({
  settings,
  onChange,
  readOnlyReason,
  error,
  isLoading,
  onRetry,
}: DecisionCaptureSettingsProps) {
  const [pending, setPending] = React.useState<string | null>(null);
  const [conflict, setConflict] = React.useState(false);

  const write = React.useCallback(
    async (key: string, update: DecisionSettingsUpdate) => {
      if (!settings) return;
      setPending(key);
      setConflict(false);
      try {
        // The etag goes with every write, not only the risky-looking ones:
        // this config is also edited by the CLI and by hand, so any write can
        // be the one that lands on top of somebody else's.
        await onChange({ ...update, etag: settings.etag });
      } catch (err) {
        // Handled, not re-thrown. These calls are made from click handlers,
        // which have nowhere to return a promise to, so re-throwing produced
        // an unhandled rejection on every failed write. A conflict gets the
        // sentence below; the host reports everything else, having already
        // rolled the control back.
        setConflict(err instanceof Error && /409|conflict/i.test(err.message));
      } finally {
        setPending(null);
      }
    },
    [onChange, settings],
  );

  if (error) {
    return (
      <ApiError
        title="Couldn't load capture settings"
        message="An error occurred while reading the decision policy."
        {...(onRetry ? { onRetry } : {})}
      />
    );
  }
  if (!settings) {
    return isLoading ? null : (
      <p className="text-xs text-[var(--color-text-secondary)]">
        No capture policy is available for this repository.
      </p>
    );
  }

  const locked = Boolean(readOnlyReason);
  const busy = (key: string) => pending === key;
  const llmMasterReason =
    readOnlyReason ?? (settings.enabled ? "" : "Capture is switched off.");

  return (
    <div className="space-y-5">
      {readOnlyReason && (
        <p className="text-xs text-[var(--color-text-secondary)]">
          {readOnlyReason} Change it from the checkout with{" "}
          <code className="font-mono text-[11px]">
            repowise decision config show
          </code>
          .
        </p>
      )}

      {conflict && (
        <p className="text-xs text-[var(--color-text-secondary)]">
          The policy changed somewhere else while this page was open, so the
          write was refused rather than applied on top of it. Reload to see the
          current state.
        </p>
      )}

      {settings.warnings.map((warning) => (
        <p key={warning} className="text-xs text-[var(--color-text-secondary)]">
          {warning}
        </p>
      ))}

      {settings.legacy_keys.length > 0 && (
        <p className="text-xs text-[var(--color-text-secondary)]">
          Still reading{" "}
          <span className="font-mono">{settings.legacy_keys.join(", ")}</span>{" "}
          from an older config. Saving anything here replaces{" "}
          {settings.legacy_keys.length === 1 ? "it" : "them"}.
        </p>
      )}

      {/* The two master switches. Separate rows because they are separate
          questions: capture can run with no model at all, and turning the
          model off must not read as turning capture off. */}
      <div className="space-y-3">
        <Toggle
          label="Capture decisions"
          description="Mine decisions from this repository during indexing and updates."
          checked={settings.enabled}
          disabled={locked || busy("enabled")}
          {...(readOnlyReason ? { reason: readOnlyReason } : {})}
          onChange={(v) => void write("enabled", { enabled: v })}
        />
        <Toggle
          label="Allow model calls"
          description={
            settings.provider_available
              ? "Lets the sources that have a model stage use it. Their prose is sent to the configured provider."
              : "No provider is configured, so the sources that have a model stage run their deterministic half only."
          }
          checked={settings.llm}
          disabled={locked || !settings.enabled || busy("llm")}
          {...(llmMasterReason ? { reason: llmMasterReason } : {})}
          onChange={(v) => void write("llm", { llm: v })}
        />
      </div>

      <section className={SECTION}>
        <div>
          <h3 className="text-sm font-medium text-[var(--color-text-primary)]">
            Preset
          </h3>
          <p className="text-xs text-[var(--color-text-secondary)]">
            A named set of sources. Changing one source afterwards leaves the
            preset reading <span className="font-mono">custom</span>, which is
            a description of where you are and not a preset you can pick.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {DECISION_PRESETS.map((preset) => {
            const active = settings.preset === preset;
            return (
              <button
                key={preset}
                type="button"
                aria-pressed={active}
                disabled={locked || busy("preset")}
                title={locked ? readOnlyReason : PRESET_BLURB[preset]}
                onClick={() => void write("preset", { preset })}
                className={
                  "rounded-md border px-2.5 py-1 text-xs transition-colors disabled:opacity-40 " +
                  (active
                    ? "border-[var(--color-accent-primary)] text-[var(--color-accent-primary)]"
                    : "border-[var(--color-border-default)] text-[var(--color-text-secondary)] hover:border-[var(--color-border-hover)] hover:text-[var(--color-text-primary)]")
                }
              >
                {preset}
              </button>
            );
          })}
          {/* Whatever the payload says, when it is not one of the buttons.
              `custom` is the resolver's word for "somebody changed a source
              after picking a preset"; anything else is a preset this build
              predates. Either way the reader must be able to see what is
              actually set rather than five unpressed buttons. */}
          {!(DECISION_PRESETS as readonly string[]).includes(settings.preset) && (
            <span className="self-center text-xs text-[var(--color-text-tertiary)]">
              {settings.preset}
            </span>
          )}
        </div>
      </section>

      <section className={SECTION}>
        <div>
          <h3 className="text-sm font-medium text-[var(--color-text-primary)]">
            Sources
          </h3>
          <p className="text-xs text-[var(--color-text-secondary)]">
            Each source's effective state and why it reads that way. A source
            with a model stage sends the prose it reads to the configured
            provider only while both its own switch and the master model switch
            are on.
          </p>
        </div>
        <ul className="divide-y divide-[var(--color-border-subtle)] border-t border-[var(--color-border-subtle)]">
          {settings.sources.map((source) => (
            <SourceRow
              key={source.key}
              source={source}
              masterOff={!settings.enabled}
              llmOff={!settings.llm}
              locked={locked}
              {...(readOnlyReason ? { readOnlyReason } : {})}
              busy={busy(source.key)}
              onChange={(patch) =>
                void write(source.key, { sources: { [source.key]: patch } })
              }
            />
          ))}
        </ul>
      </section>

      <section className={SECTION}>
        <div>
          <h3 className="text-sm font-medium text-[var(--color-text-primary)]">
            Broad discovery budget
          </h3>
          <p className="text-xs text-[var(--color-text-secondary)]">
            The ceiling on the one broad model pass each update. It is one call,
            not one per session, and these are its bounds.
          </p>
        </div>
        <dl className="flex flex-wrap gap-x-8 gap-y-1">
          <Figure
            label="Sessions per update"
            value={settings.discovery.max_sessions}
          />
          <Figure
            label="Input tokens"
            value={settings.discovery.max_input_tokens}
          />
        </dl>
        <p className="text-xs text-[var(--color-text-tertiary)]">
          Change them with{" "}
          <code className="font-mono text-[11px]">
            repowise decision config discovery --max-sessions N
          </code>
          .
        </p>
      </section>
    </div>
  );
}

function Figure({ label, value }: { label: string; value: number }) {
  return (
    <div className="space-y-0.5">
      <dt className={MICRO_LABEL}>{label}</dt>
      <dd className="font-mono text-sm tabular-nums text-[var(--color-text-primary)]">
        {value.toLocaleString()}
      </dd>
    </div>
  );
}

function SourceRow({
  source,
  masterOff,
  llmOff,
  locked,
  readOnlyReason,
  busy,
  onChange,
}: {
  source: DecisionSourceState;
  masterOff: boolean;
  llmOff: boolean;
  locked: boolean;
  readOnlyReason?: string;
  busy: boolean;
  onChange: (patch: { enabled?: boolean; llm?: boolean }) => void;
}) {
  // `togglable: false` is the authority route: manual entry has no capture to
  // switch off, so it gets a sentence rather than a control that cannot act.
  const captureReason = locked
    ? readOnlyReason
    : !source.togglable
      ? "Always available. Recording a decision yourself is not a capture pass."
      : masterOff
        ? "Capture is switched off."
        : undefined;
  const llmReason = locked
    ? readOnlyReason
    : !source.supports_llm
      ? "This source has no model stage."
      : masterOff
        ? "Capture is switched off."
        : llmOff
          ? "Model calls are switched off."
          : !source.enabled
            ? "This source is switched off."
            : undefined;

  return (
    <li className="flex flex-col gap-2 py-3 sm:flex-row sm:items-start sm:justify-between sm:gap-6">
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-baseline gap-x-2">
          <span className="text-sm text-[var(--color-text-primary)]">
            {source.label}
          </span>
          <span className={MICRO_LABEL}>{source.key}</span>
          {source.authority === "human" && (
            <span className={MICRO_LABEL}>human</span>
          )}
        </div>
        <p className="text-xs text-[var(--color-text-secondary)]">
          {source.description}
        </p>
        {/* The effective state, in the engine's own words. Shown on every row
            rather than only the surprising ones: this is a settings surface,
            and "why is this off" is the question it exists to answer. */}
        <p className="text-xs text-[var(--color-text-tertiary)]">
          {source.status.replace(/_/g, " ")}
          {source.reason ? ` · ${source.reason}` : ""}
        </p>
      </div>
      <div className="flex shrink-0 flex-col gap-1.5">
        <Toggle
          compact
          label="Capture"
          checked={source.enabled}
          disabled={locked || !source.togglable || masterOff || busy}
          {...(captureReason ? { reason: captureReason } : {})}
          onChange={(v) => onChange({ enabled: v })}
        />
        <Toggle
          compact
          label="Model"
          checked={source.llm_enabled}
          disabled={
            locked ||
            !source.supports_llm ||
            masterOff ||
            llmOff ||
            !source.enabled ||
            busy
          }
          {...(llmReason ? { reason: llmReason } : {})}
          onChange={(v) => onChange({ llm: v })}
        />
      </div>
    </li>
  );
}

/**
 * A labelled checkbox with its reason attached.
 *
 * A native `input type="checkbox"` rather than a bespoke switch: it is
 * keyboard reachable, announces its own state, and needs no roving-focus
 * contract explained. `title` carries the reason so a disabled control can be
 * asked why before it is pressed, which is the rule a live-looking button that
 * explains itself afterwards breaks.
 */
function Toggle({
  label,
  description,
  checked,
  disabled,
  reason,
  compact,
  onChange,
}: {
  label: string;
  description?: string;
  checked: boolean;
  disabled?: boolean;
  reason?: string;
  compact?: boolean;
  onChange: (value: boolean) => void;
}) {
  const id = React.useId();
  const reasonId = `${id}-reason`;
  const describedBy = disabled && reason ? reasonId : undefined;
  return (
    <div className={compact ? "" : "space-y-0.5"}>
      <div className="flex items-center gap-2">
        <input
          id={id}
          type="checkbox"
          checked={checked}
          disabled={disabled}
          title={disabled ? reason : undefined}
          {...(describedBy ? { "aria-describedby": describedBy } : {})}
          onChange={(e) => onChange(e.target.checked)}
          className="h-3.5 w-3.5 shrink-0 accent-[var(--color-accent-primary)] disabled:opacity-40"
        />
        <label
          htmlFor={id}
          className={
            compact
              ? "text-xs text-[var(--color-text-secondary)]"
              : "text-sm text-[var(--color-text-primary)]"
          }
        >
          {label}
        </label>
      </div>
      {description && (
        <p className="pl-[1.375rem] text-xs text-[var(--color-text-secondary)]">
          {description}
        </p>
      )}
      {disabled && reason && (
        <p
          id={reasonId}
          className={
            compact
              ? "sr-only"
              : "pl-[1.375rem] text-xs text-[var(--color-text-tertiary)]"
          }
        >
          {reason}
        </p>
      )}
    </div>
  );
}

/** Exported for a host that wants to render its own preset chooser. */
export { PRESET_BLURB };
