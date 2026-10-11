"use client";

import * as React from "react";
import { TriangleAlert } from "lucide-react";
import { Spinner } from "../ui/spinner";
import { Switch } from "../ui/switch";
import { Label } from "../ui/label";
import { toFriendlyMessage } from "../lib/errors";

export interface RefactoringSettingsValue {
  enabled: boolean;
  /** The provider chat resolves for this repo; null when none is configured. */
  provider: string | null;
  model: string | null;
}

export interface RefactoringModelToggleProps {
  value: RefactoringSettingsValue;
  /** Persist `refactoring.llm.enabled`. Host owns the API call. */
  onToggle: (enabled: boolean) => Promise<void>;
  /** Where the user configures a model, shown when none is configured. */
  setupHref?: string | undefined;
}

/**
 * The one code-generation switch. It reuses the model chat uses, so the line
 * under it names that model rather than offering a second picker. Autosaves,
 * and rolls back if the write fails.
 */
export function RefactoringModelToggle({ value, onToggle, setupHref }: RefactoringModelToggleProps) {
  const id = React.useId();
  const [enabled, setEnabled] = React.useState(value.enabled);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => setEnabled(value.enabled), [value.enabled]);

  const change = async (next: boolean) => {
    setEnabled(next);
    setSaving(true);
    setError(null);
    try {
      await onToggle(next);
    } catch (err) {
      setEnabled(!next);
      setError(toFriendlyMessage(err, "Could not save the setting."));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-1.5">
      <div className="flex items-start justify-between gap-4">
        <Label htmlFor={id} className="text-sm font-medium text-[var(--color-text-primary)]">
          Use your configured model to name helpers and draft code
        </Label>
        <Switch
          id={id}
          checked={enabled}
          disabled={saving}
          onCheckedChange={(next) => void change(Boolean(next))}
        />
      </div>
      <p className="max-w-prose text-xs text-[var(--color-text-tertiary)]">
        {value.provider ? (
          <>
            Uses <span className="text-[var(--color-text-secondary)]">{value.provider}</span>
            {value.model ? (
              <>
                {" · "}
                <span className="font-mono text-[var(--color-text-secondary)]">{value.model}</span>
              </>
            ) : null}
            , the same model as chat. Drafts are shown for review and never applied.
          </>
        ) : (
          <>
            No model configured.{" "}
            {setupHref ? (
              <a
                href={setupHref}
                className="text-[var(--color-accent-primary)] underline-offset-2 hover:underline"
              >
                Set one up
              </a>
            ) : null}
          </>
        )}
      </p>
      {error ? (
        <p role="alert" className="flex items-start gap-2 text-xs text-[var(--color-error)]">
          <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {error}
        </p>
      ) : null}
    </div>
  );
}

export interface RefactoringSettingsCardProps {
  /** Current settings, or null while loading. */
  value: RefactoringSettingsValue | null;
  onToggle: (enabled: boolean) => Promise<void>;
  setupHref?: string | undefined;
  /** True while the initial settings load is in flight. */
  loading?: boolean;
  /** Set when settings are unavailable (e.g. no local checkout on this server). */
  unavailableReason?: string | null;
}

/**
 * The settings-page home of {@link RefactoringModelToggle}, with its loading
 * and unavailable states. Presentation only, so the hosted frontend can reuse it.
 */
export function RefactoringSettingsCard({
  value,
  onToggle,
  setupHref,
  loading = false,
  unavailableReason = null,
}: RefactoringSettingsCardProps) {
  if (unavailableReason) {
    return <p className="text-sm text-[var(--color-text-tertiary)]">{unavailableReason}</p>;
  }
  if (loading || value === null) {
    return (
      <div className="flex items-center gap-2 text-sm text-[var(--color-text-secondary)]">
        <Spinner />
        Loading settings…
      </div>
    );
  }
  return <RefactoringModelToggle value={value} onToggle={onToggle} setupHref={setupHref} />;
}
