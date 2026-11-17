"use client";

/**
 * Every setting on one page, generated from the settings registry: a section
 * per area, a row per setting with the control its type calls for, where its
 * value came from, a reset, and a prompt that hands it to an agent. Settings
 * with a richer editor elsewhere (provider keys, refactoring) link there.
 */

import * as React from "react";
import { AiPromptButton } from "../health/ai-prompt-button";
import { useAiPromptAction } from "../health/ai-prompt-action";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Switch } from "../ui/switch";
import { SettingsRows, SETTINGS_MICRO_LABEL, useSaveState, SaveIndicator } from "./settings-primitives";
import { buildSettingAiPrompt } from "./setting-prompt";
import {
  DEFAULT_SETTINGS_REGISTRY_LABELS,
  type SettingsRegistryLabels,
} from "./settings-registry-labels";
import { displayValue, SettingsRegistryRow } from "./settings-registry-row";
import type {
  RegistryArea,
  RegistryChange,
  RegistryLink,
  RegistrySetting,
  RegistrySettingState,
  RegistrySuggestion,
} from "./settings-registry-types";

export interface SettingsRegistryPageProps {
  /** Every setting by area, internal ones included; the page hides those until asked. */
  areas: RegistryArea[];
  /** Highlighted at the top and on their rows. */
  suggestions?: RegistrySuggestion[];
  /** Names the repository in agent prompts. */
  repoName: string;
  onSet: (key: string, value: unknown) => Promise<RegistryChange>;
  onReset: (key: string) => Promise<RegistryChange>;
  /** A page that owns this setting's richer editor, if one does. */
  linkFor?: (setting: RegistrySetting) => RegistryLink | null;
  labels?: Partial<SettingsRegistryLabels>;
}

function matches(setting: RegistrySetting, query: string): boolean {
  if (!query) return true;
  const q = query.toLowerCase();
  return (
    setting.key.toLowerCase().includes(q) ||
    setting.description.toLowerCase().includes(q) ||
    (setting.env?.name.toLowerCase().includes(q) ?? false)
  );
}

export function SettingsRegistryPage({
  areas,
  suggestions = [],
  repoName,
  onSet,
  onReset,
  linkFor,
  labels: labelOverrides,
}: SettingsRegistryPageProps) {
  const labels = { ...DEFAULT_SETTINGS_REGISTRY_LABELS, ...labelOverrides };
  const areaTitles = { ...DEFAULT_SETTINGS_REGISTRY_LABELS.areaTitles, ...labelOverrides?.areaTitles };
  const [query, setQuery] = React.useState("");
  const [changedOnly, setChangedOnly] = React.useState(false);
  const [showInternal, setShowInternal] = React.useState(false);
  // Writes answer with the new state of every setting they moved; it lays
  // over the props until the host passes fresh data.
  const [moved, setMoved] = React.useState<Record<string, RegistrySettingState>>({});
  React.useEffect(() => setMoved({}), [areas]);
  const { openPrompt, promptModal } = useAiPromptAction();

  const onChanged = React.useCallback((change: RegistryChange) => {
    setMoved((prev) => {
      const next = { ...prev, [change.key]: change.after };
      for (const m of change.also) next[m.key] = m.after;
      return next;
    });
  }, []);

  const suggestionFor = React.useMemo(
    () => new Map(suggestions.map((s) => [s.key, s])),
    [suggestions],
  );

  const current = React.useMemo(
    () =>
      areas.map((a) => ({
        area: a.area,
        settings: a.settings.map((s) => (moved[s.key] ? { ...s, ...moved[s.key] } : s)),
      })),
    [areas, moved],
  );
  const byKey = React.useMemo(
    () => new Map(current.flatMap((a) => a.settings.map((s) => [s.key, s] as const))),
    [current],
  );

  const pool = current.map((a) => ({
    ...a,
    settings: a.settings.filter((s) => showInternal || !s.internal),
  }));
  const total = pool.reduce((n, a) => n + a.settings.length, 0);
  const shown = pool
    .map((a) => ({
      ...a,
      settings: a.settings.filter((s) => (!changedOnly || s.changed) && matches(s, query)),
    }))
    .filter((a) => a.settings.length > 0);
  const shownCount = shown.reduce((n, a) => n + a.settings.length, 0);

  const prompt = (setting: RegistrySetting) => {
    const suggestion = suggestionFor.get(setting.key);
    openPrompt({
      title: labels.promptTitle(setting.key),
      description: labels.promptDescription,
      getPrompt: (flavor) => buildSettingAiPrompt(setting, repoName, flavor, suggestion),
    });
  };

  const liveSuggestions = suggestions.filter((s) => byKey.has(s.key));
  const filterId = React.useId();

  return (
    <div className="flex flex-col gap-6">
      {liveSuggestions.length > 0 && (
        <section aria-labelledby={`${filterId}-suggested`} className="flex flex-col gap-2">
          <div>
            <h3 id={`${filterId}-suggested`} className="text-sm font-medium text-[var(--color-text-primary)]">
              {labels.suggestionsTitle}
            </h3>
            <p className="mt-0.5 text-xs text-[var(--color-text-tertiary)]">
              {labels.suggestionsDescription}
            </p>
          </div>
          <ul className="flex flex-col">
            {liveSuggestions.map((s) => (
              <SuggestionItem
                key={s.key}
                suggestion={s}
                setting={byKey.get(s.key)!}
                labels={labels}
                onApply={async () => onChanged(await onSet(s.key, s.suggested_value))}
                onPrompt={prompt}
              />
            ))}
          </ul>
        </section>
      )}

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <Input
          type="search"
          aria-label={labels.search}
          placeholder={labels.searchPlaceholder}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="sm:max-w-xs"
        />
        <div className="flex flex-wrap items-center gap-4 text-xs text-[var(--color-text-secondary)]">
          <label htmlFor={`${filterId}-changed`} className="inline-flex items-center gap-2">
            <Switch id={`${filterId}-changed`} checked={changedOnly} onCheckedChange={setChangedOnly} />
            {labels.changedOnly}
          </label>
          <label htmlFor={`${filterId}-internal`} className="inline-flex items-center gap-2">
            <Switch id={`${filterId}-internal`} checked={showInternal} onCheckedChange={setShowInternal} />
            {labels.showInternal}
          </label>
          <span aria-live="polite" className="font-mono tabular-nums text-[var(--color-text-tertiary)]">
            {labels.count(shownCount, total)}
          </span>
        </div>
      </div>

      {shown.length === 0 ? (
        <p className="text-sm text-[var(--color-text-tertiary)]">{labels.noMatches}</p>
      ) : (
        shown.map((a) => (
          <section key={a.area} aria-labelledby={`${filterId}-${a.area}`}>
            <h3 id={`${filterId}-${a.area}`} className={SETTINGS_MICRO_LABEL}>
              {areaTitles[a.area] ?? a.area}
            </h3>
            <SettingsRows>
              {a.settings.map((s) => (
                <SettingsRegistryRow
                  key={s.key}
                  setting={s}
                  labels={labels}
                  suggestion={suggestionFor.get(s.key)}
                  link={linkFor?.(s) ?? null}
                  onSet={onSet}
                  onReset={onReset}
                  onPrompt={prompt}
                  onChanged={onChanged}
                />
              ))}
            </SettingsRows>
          </section>
        ))
      )}
      {promptModal}
    </div>
  );
}

function SuggestionItem({
  suggestion,
  setting,
  labels,
  onApply,
  onPrompt,
}: {
  suggestion: RegistrySuggestion;
  setting: RegistrySetting;
  labels: SettingsRegistryLabels;
  onApply: () => Promise<void>;
  onPrompt: (setting: RegistrySetting) => void;
}) {
  const { state, error, track } = useSaveState();
  const writable = setting.writable !== false && !setting.locked;
  return (
    <li className="flex flex-col gap-2 border-b border-[var(--color-border-default)] py-3 last:border-b-0 sm:flex-row sm:items-start sm:justify-between sm:gap-6">
      <div className="min-w-0">
        <p className="flex flex-wrap items-center gap-x-2 text-sm">
          <span className="font-mono text-[13px] text-[var(--color-text-primary)]">{suggestion.key}</span>
          <span className="text-[var(--color-text-secondary)]">
            {displayValue(setting.value, labels)} → {displayValue(suggestion.suggested_value, labels)}
          </span>
          <SaveIndicator state={state} error={error} />
        </p>
        <p className="mt-1 text-xs leading-relaxed text-[var(--color-text-tertiary)] [text-wrap:pretty]">
          {suggestion.reason}
          {suggestion.impact ? ` ${suggestion.impact}` : ""}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <AiPromptButton label={labels.copyPrompt} ariaLabel={`${labels.copyPrompt}: ${suggestion.key}`} onClick={() => onPrompt(setting)} />
        {writable && (
          <Button
            variant="outline"
            size="sm"
            aria-label={labels.applyAria(suggestion.key)}
            onClick={() =>
              void track(onApply, (e) => (e instanceof Error && e.message ? e.message : labels.saveFailed))
            }
          >
            {labels.apply}
          </Button>
        )}
      </div>
    </li>
  );
}
