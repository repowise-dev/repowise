"use client";

/**
 * Installed plugins and the sources they fetch from, for one repository: the
 * same facts `repowise plugins --format json` prints. Each plugin shows its
 * state, the settings it declares (secrets as set or not set, never a value)
 * and the command that finishes its setup; each fetching source shows its last
 * sync and a Sync now button. Plain settings link to the host's settings
 * editor, so this panel never edits a value itself.
 */

import * as React from "react";
import type {
  PluginRow,
  PluginsResponse,
  SourceRow,
} from "@repowise-dev/types/generated/http";
import { CommandLine } from "../shared/command-line";
import { RelativeTime } from "../shared/relative-time";
import { Button } from "../ui/button";
import { cn } from "../lib/cn";
import { SETTINGS_MICRO_LABEL, SettingsRow, SettingsRows } from "./settings-primitives";
import { DEFAULT_PLUGINS_PANEL_LABELS, type PluginsPanelLabels } from "./plugins-panel-labels";

/** In `syncing`: a sync of every configured source is running. */
export const ALL_SOURCES = "*";

export interface PluginsPanelProps {
  data: PluginsResponse;
  /** Start a sync of one source; the host refetches `data` to follow it. */
  onSync?: (source: string) => Promise<void>;
  /** Where a plain setting is edited, or null to show it unlinked. */
  settingHref?: (key: string) => string | null;
  /** The plugin guide, linked from the empty state. */
  guideHref: string;
  /** Shown under the empty state, e.g. a hosted tip. */
  emptyExtra?: React.ReactNode;
  labels?: Partial<PluginsPanelLabels>;
}

type Tone = "ok" | "warn" | "error";

const TONE_TEXT: Record<Tone, string> = {
  ok: "text-[var(--color-text-secondary)]",
  warn: "text-[var(--color-warning)]",
  error: "text-[var(--color-error)]",
};
const TONE_DOT: Record<Tone, string> = {
  ok: "bg-[var(--color-success)]",
  warn: "bg-[var(--color-warning)]",
  error: "bg-[var(--color-error)]",
};

/** A dot and a word: the plugin or sync state. */
function StateMark({ tone, children }: { tone: Tone; children: React.ReactNode }) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-xs", TONE_TEXT[tone])}>
      <span aria-hidden className={cn("h-1.5 w-1.5 shrink-0 rounded-full", TONE_DOT[tone])} />
      {children}
    </span>
  );
}

function pluginState(row: PluginRow, labels: PluginsPanelLabels): { tone: Tone; word: string } {
  if (row.status === "failed") return { tone: "error", word: labels.failed };
  if (row.status !== "loaded") return { tone: "warn", word: labels.status(row.status) };
  if (row.configured === false) return { tone: "warn", word: labels.notConfigured };
  return { tone: "ok", word: labels.ready };
}

function PluginItem({
  row,
  settingHref,
  labels,
}: {
  row: PluginRow;
  settingHref: PluginsPanelProps["settingHref"] | undefined;
  labels: PluginsPanelLabels;
}) {
  const state = pluginState(row, labels);
  const seams = (row.seams ?? []).filter((s) => !s.startsWith("setting:"));
  const settings = row.settings ?? [];
  return (
    <SettingsRow
      label={
        <span className="inline-flex items-baseline gap-2">
          <span className="font-mono">{row.name}</span>
          {row.version && (
            <span className="font-mono text-xs font-normal text-[var(--color-text-tertiary)]">
              {row.version}
            </span>
          )}
        </span>
      }
      hint={seams.length > 0 ? <span className="font-mono">{seams.join(", ")}</span> : undefined}
    >
      <div className="flex flex-col gap-2">
        <StateMark tone={state.tone}>{state.word}</StateMark>
        {row.error && (
          <p className="text-xs text-[var(--color-text-secondary)] [overflow-wrap:anywhere]">{row.error}</p>
        )}
        {settings.length > 0 && (
          <ul className="flex flex-col gap-1" aria-label={labels.settingsOf(row.name)}>
            {settings.map((s) => {
              const href = s.secret ? null : (settingHref?.(s.key) ?? null);
              return (
                <li key={s.key} className="flex flex-wrap items-baseline gap-x-2 text-xs">
                  {href ? (
                    <a
                      href={href}
                      className="font-mono text-[var(--color-accent-primary)] hover:underline"
                    >
                      {s.key}
                    </a>
                  ) : (
                    <span className="font-mono text-[var(--color-text-primary)]">{s.key}</span>
                  )}
                  <span className="text-[var(--color-text-tertiary)]">
                    {s.secret ? labels.secret(s.set) : s.set ? labels.set : labels.notSet}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
        {row.next_command && <CommandLine command={row.next_command} />}
      </div>
    </SettingsRow>
  );
}

function LastSync({ source, labels }: { source: SourceRow; labels: PluginsPanelLabels }) {
  const last = source.last_sync;
  if (!last?.at) {
    return <p className="text-xs text-[var(--color-text-tertiary)]">{labels.neverSynced}</p>;
  }
  const stored = last.state === "stored";
  return (
    <div className="flex flex-col gap-1">
      <span className="inline-flex flex-wrap items-center gap-x-2 text-xs text-[var(--color-text-tertiary)]">
        {stored ? (
          <span>{labels.stored(last.batches ?? 0)}</span>
        ) : (
          <StateMark tone={last.state === "unavailable" ? "warn" : "error"}>
            {labels.syncState(last.state ?? "failed")}
          </StateMark>
        )}
        <RelativeTime date={last.at} template={labels.syncedAt} />
      </span>
      {!stored && last.message && (
        <p className="text-xs text-[var(--color-text-secondary)] [overflow-wrap:anywhere]">{last.message}</p>
      )}
    </div>
  );
}

function SourceItem({
  source,
  syncing,
  busy,
  onSync,
  labels,
}: {
  source: SourceRow;
  syncing: boolean;
  busy: boolean;
  onSync: ((source: string) => void) | undefined;
  labels: PluginsPanelLabels;
}) {
  return (
    <SettingsRow
      label={<span className="font-mono">{source.source}</span>}
      hint={
        <span className="inline-flex flex-wrap gap-x-2">
          <span className="font-mono">{source.seam}</span>
          <span>{source.plugin ? labels.fromPlugin(source.plugin) : labels.builtIn}</span>
        </span>
      }
    >
      <div className="flex flex-col gap-2">
        {source.configured ? (
          <div className="flex flex-wrap items-start justify-between gap-3">
            {syncing ? (
              <p className="text-xs text-[var(--color-text-secondary)]">{labels.syncing}</p>
            ) : (
              <LastSync source={source} labels={labels} />
            )}
            {onSync && (
              <Button
                variant="outline"
                size="sm"
                disabled={busy}
                onClick={() => onSync(source.source)}
                aria-label={labels.syncNowAria(source.source)}
              >
                {labels.syncNow}
              </Button>
            )}
          </div>
        ) : (
          <>
            <StateMark tone="warn">{labels.notConfigured}</StateMark>
            {source.message && (
              <p className="text-xs text-[var(--color-text-secondary)]">{source.message}</p>
            )}
            <CommandLine command={source.next_command} />
            {/* What it stored stays in the index, so its last sync stays here too. */}
            {source.last_sync?.at && <LastSync source={source} labels={labels} />}
          </>
        )}
      </div>
    </SettingsRow>
  );
}

function EmptyPlugins({
  guideHref,
  extra,
  labels,
}: {
  guideHref: string;
  extra: React.ReactNode;
  labels: PluginsPanelLabels;
}) {
  return (
    <div className="flex flex-col gap-2 border-t border-[var(--color-border-default)] pt-4">
      <h3 className="text-sm font-medium text-[var(--color-text-primary)]">{labels.emptyTitle}</h3>
      <p className="max-w-[62ch] text-xs leading-relaxed text-[var(--color-text-tertiary)]">
        {labels.emptyBody}{" "}
        <a
          href={guideHref}
          target="_blank"
          rel="noopener noreferrer"
          className="text-[var(--color-accent-primary)] hover:underline"
        >
          {labels.guide}
        </a>
      </p>
      <p className="text-xs text-[var(--color-text-tertiary)]">{labels.emptyBuild}</p>
      <CommandLine command="repowise plugins new <seam>" />
      {extra}
    </div>
  );
}

/** Starts a sync through the host; which source is starting, and why it did not start. */
function useSyncAction(onSync: PluginsPanelProps["onSync"], fallbackError: string) {
  const [starting, setStarting] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const sync = onSync
    ? (source: string) => {
        setStarting(source);
        setError(null);
        onSync(source)
          .catch((e: unknown) => setError(e instanceof Error && e.message ? e.message : fallbackError))
          .finally(() => setStarting(null));
      }
    : undefined;
  return { sync, starting, error };
}

export function PluginsPanel({
  data,
  onSync,
  settingHref,
  guideHref,
  emptyExtra,
  labels: overrides,
}: PluginsPanelProps) {
  const labels = { ...DEFAULT_PLUGINS_PANEL_LABELS, ...overrides };
  const plugins = data.plugins ?? [];
  const sources = data.sources ?? [];
  const syncing = new Set(data.syncing ?? []);
  const anySync = syncing.size > 0;
  const { sync, starting, error } = useSyncAction(onSync, labels.syncFailed);

  return (
    <div className="flex flex-col gap-6">
      {(error || data.sync_error) && (
        <p role="alert" className="text-xs text-[var(--color-error)]">
          {error ?? data.sync_error}
        </p>
      )}
      {sources.length > 0 && (
        <section aria-label={labels.sourcesTitle} className="flex flex-col gap-1">
          <h3 className={SETTINGS_MICRO_LABEL}>{labels.sourcesTitle}</h3>
          <SettingsRows>
            {sources.map((s) => (
              <SourceItem
                key={`${s.seam}:${s.source}`}
                source={s}
                syncing={syncing.has(s.source) || (syncing.has(ALL_SOURCES) && s.configured)}
                busy={anySync || starting !== null}
                onSync={sync}
                labels={labels}
              />
            ))}
          </SettingsRows>
        </section>
      )}
      {plugins.length > 0 ? (
        <section aria-label={labels.pluginsTitle} className="flex flex-col gap-1">
          <h3 className={SETTINGS_MICRO_LABEL}>{labels.pluginsTitle}</h3>
          <SettingsRows>
            {plugins.map((p) => (
              <PluginItem key={p.name} row={p} settingHref={settingHref} labels={labels} />
            ))}
          </SettingsRows>
        </section>
      ) : (
        <EmptyPlugins guideHref={guideHref} extra={emptyExtra} labels={labels} />
      )}
    </div>
  );
}
