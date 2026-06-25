"use client";

import * as React from "react";
import { Check, Loader2, TriangleAlert, Wand2 } from "lucide-react";
import { Switch } from "../ui/switch";
import { Input } from "../ui/input";
import { Label } from "../ui/label";

export interface RefactoringSettingsValue {
  enabled: boolean;
  provider: string | null;
  model: string | null;
}

export interface RefactoringSettingsCardProps {
  /** Current persisted settings, or null while loading. */
  value: RefactoringSettingsValue | null;
  /** Persist the new settings. Host owns the API call. */
  onSave: (value: RefactoringSettingsValue) => Promise<void>;
  /** True while the initial settings load is in flight. */
  loading?: boolean;
  /** Set when settings are unavailable (e.g. no local checkout on this server). */
  unavailableReason?: string | null;
}

type SaveState = "idle" | "saving" | "saved" | "error";

/**
 * The opt-in code-generation switch (`refactoring.llm`). A toggle plus optional
 * provider/model overrides, written to the repo's config. Presentation only —
 * the host wires `onSave` to the settings endpoint — so the hosted frontend can
 * reuse it unchanged.
 */
export function RefactoringSettingsCard({
  value,
  onSave,
  loading = false,
  unavailableReason = null,
}: RefactoringSettingsCardProps) {
  const [enabled, setEnabled] = React.useState(false);
  const [provider, setProvider] = React.useState("");
  const [model, setModel] = React.useState("");
  const [save, setSave] = React.useState<SaveState>("idle");
  const [error, setError] = React.useState<string | null>(null);

  // Seed the form from the persisted value once it arrives / changes.
  React.useEffect(() => {
    if (!value) return;
    setEnabled(value.enabled);
    setProvider(value.provider ?? "");
    setModel(value.model ?? "");
  }, [value]);

  const dirty =
    value !== null &&
    (enabled !== value.enabled ||
      provider !== (value.provider ?? "") ||
      model !== (value.model ?? ""));

  const persist = React.useCallback(async () => {
    setSave("saving");
    setError(null);
    try {
      await onSave({
        enabled,
        provider: provider.trim() || null,
        model: model.trim() || null,
      });
      setSave("saved");
      window.setTimeout(() => setSave("idle"), 1800);
    } catch (err) {
      setSave("error");
      setError(err instanceof Error ? err.message : "Could not save settings.");
    }
  }, [enabled, provider, model, onSave]);

  if (unavailableReason) {
    return (
      <p className="text-sm text-[var(--color-text-tertiary)]">{unavailableReason}</p>
    );
  }

  if (loading || value === null) {
    return (
      <div className="flex items-center gap-2 text-sm text-[var(--color-text-secondary)]">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading settings…
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-0.5">
          <div className="flex items-center gap-2">
            <Wand2 className="h-4 w-4 text-[var(--color-accent-primary)]" />
            <Label htmlFor="refactoring-llm-enabled" className="text-sm font-medium">
              Generate code from plans
            </Label>
          </div>
          <p className="max-w-prose text-xs text-[var(--color-text-tertiary)]">
            Let the Refactoring tab turn a deterministic plan into a reviewable diff using your
            configured model. Off by default; never runs during indexing. Suggestion only — no
            auto-apply.
          </p>
        </div>
        <Switch
          id="refactoring-llm-enabled"
          checked={enabled}
          onCheckedChange={(next) => setEnabled(Boolean(next))}
        />
      </div>

      {enabled ? (
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="refactoring-llm-provider" className="text-xs text-[var(--color-text-secondary)]">
              Provider <span className="text-[var(--color-text-tertiary)]">(optional)</span>
            </Label>
            <Input
              id="refactoring-llm-provider"
              value={provider}
              onChange={(e) => setProvider(e.target.value)}
              placeholder="repo default (e.g. anthropic)"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="refactoring-llm-model" className="text-xs text-[var(--color-text-secondary)]">
              Model <span className="text-[var(--color-text-tertiary)]">(optional)</span>
            </Label>
            <Input
              id="refactoring-llm-model"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="repo default"
            />
          </div>
        </div>
      ) : null}

      {error ? (
        <div className="flex items-start gap-2 text-xs text-[var(--color-error)]">
          <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {error}
        </div>
      ) : null}

      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={persist}
          disabled={!dirty || save === "saving"}
          className="inline-flex items-center gap-1.5 rounded-lg bg-[var(--color-accent-primary)] px-3.5 py-2 text-sm font-semibold text-[var(--color-bg-surface)] transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          {save === "saving" ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
          Save
        </button>
        {save === "saved" ? (
          <span className="inline-flex items-center gap-1 text-xs font-medium text-[var(--color-success)]">
            <Check className="h-3.5 w-3.5" />
            Saved
          </span>
        ) : null}
      </div>
    </div>
  );
}
