"use client";

/**
 * Presentational shell for the chat model selector. The interactive surface
 * (popover, provider choice, searchable model list) lives here; the data
 * hooks (the `/providers` catalog and a provider's live models) live in the
 * consumer app via a thin wrapper that maps its hook output onto these props.
 *
 * A pick here applies to one conversation only; the repo's saved model is
 * changed in Settings.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronUp } from "lucide-react";
import { cn } from "../lib/cn";
import { ModelCombobox, type ModelChoice } from "../settings/model-combobox";

export interface ModelSelectorProvider {
  id: string;
  name: string;
  models: string[];
  default_model: string;
  configured: boolean;
}

export interface ModelSelectorLabels {
  dialog: string;
  provider: string;
  search: string;
  useTyped: (id: string) => string;
  /** Tag on the repo's saved model. */
  repoDefault: string;
  /** Tag on a provider's own default, for a provider the repo does not use. */
  providerDefault: string;
  loading: string;
  noMatch: string;
  configure: string;
  needsSetup: string;
}

const DEFAULT_LABELS: ModelSelectorLabels = {
  dialog: "Choose conversation model",
  provider: "Provider",
  search: "Search or type a model id",
  useTyped: (id) => `Use "${id}"`,
  repoDefault: "repo default",
  providerDefault: "default",
  loading: "Loading models",
  noMatch: "No listed model matches.",
  configure: "Configure in Settings",
  needsSetup: "More providers need a key first.",
};

export interface ModelSelectorProps {
  providers: ModelSelectorProvider[];
  activeProvider: string | null;
  activeModel: string | null;
  isLoading?: boolean;
  onActivate: (providerId: string, model: string) => void | Promise<void>;
  /** The repo's saved provider and model, marked in the list. */
  defaultProvider?: string | null;
  defaultModel?: string | null;
  /** Told which provider the open menu shows (null when it closes), so the
   *  consumer can fetch that provider's live models lazily. */
  onBrowse?: (providerId: string | null) => void;
  /** Models of the provider last passed to `onBrowse`. Without them the
   *  provider's static `models` are offered. */
  models?: ModelChoice[];
  modelsLoading?: boolean;
  /** API-key management belongs in Settings, outside routine model choice. */
  settingsHref?: string;
  /** @deprecated Key management is ignored here and belongs in Settings. */
  onSaveKey?: (providerId: string, key: string) => void | Promise<void>;
  /** Optional label override when no provider is active. */
  emptyLabel?: string;
  labels?: Partial<ModelSelectorLabels>;
  className?: string;
}

export function ModelSelector({
  providers,
  activeProvider,
  activeModel,
  isLoading = false,
  onActivate,
  defaultProvider = null,
  defaultModel = null,
  onBrowse,
  models,
  modelsLoading = false,
  settingsHref = "/settings",
  emptyLabel = "Select model",
  labels: labelOverrides,
  className,
}: ModelSelectorProps) {
  const labels = { ...DEFAULT_LABELS, ...labelOverrides };
  const [open, setOpen] = useState(false);
  const [browsing, setBrowsing] = useState<string | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const close = useCallback(() => {
    setOpen(false);
    onBrowse?.(null);
    window.requestAnimationFrame(() => triggerRef.current?.focus());
  }, [onBrowse]);
  useEffect(() => {
    if (!open) return;
    panelRef.current?.querySelector<HTMLElement>("input, button, a")?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [close, open]);

  const configured = providers.filter((p) => p.configured);
  const activeP = providers.find((p) => p.id === activeProvider);
  const label = activeP
    ? `${activeP.name} · ${activeModel ?? activeP.default_model}`
    : emptyLabel;
  const shown = configured.find((p) => p.id === browsing) ?? null;

  function browse(providerId: string | null) {
    setBrowsing(providerId);
    onBrowse?.(providerId);
  }

  function toggle() {
    if (open) {
      close();
      return;
    }
    setOpen(true);
    const start =
      configured.find((p) => p.id === activeProvider) ??
      configured.find((p) => p.id === defaultProvider) ??
      configured[0];
    browse(start?.id ?? null);
  }

  async function handleSelect(model: string) {
    if (!shown) return;
    await onActivate(shown.id, model);
    close();
  }

  const isRepoProvider = shown !== null && shown.id === defaultProvider;
  const listModels =
    onBrowse && models ? models : (shown?.models ?? []).map((id) => ({ id }));

  return (
    <div className={cn("relative", className)}>
      <button
        ref={triggerRef}
        type="button"
        onClick={toggle}
        aria-expanded={open}
        aria-haspopup="dialog"
        className={cn(
          "flex min-h-7 items-center gap-1.5 rounded px-1.5 py-0.5 text-xs",
          "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]",
          "hover:bg-[var(--color-bg-elevated)] transition-colors",
        )}
      >
        {/* The model name is the longest string in a crowded row. Below `sm`
            it drops to the provider alone rather than squeezing the row —
            labels go before controls do. */}
        <span className="truncate max-w-[120px] sm:max-w-[200px]">
          {isLoading ? "…" : label}
        </span>
        <ChevronUp className="h-3 w-3 shrink-0" />
      </button>

      {open && (
        <>
          <div
            className="fixed inset-0 z-[var(--z-dropdown)]"
            onClick={() => {
              close();
            }}
          />

          <div ref={panelRef} role="dialog" aria-label={labels.dialog} className="absolute bottom-full left-0 z-[calc(var(--z-dropdown)+1)] mb-1 w-80 overflow-hidden rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-overlay)] shadow-[var(--shadow-lg)]">
            <div className="space-y-2 p-2">
              {configured.length > 0 && (
                <div role="group" aria-label={labels.provider} className="flex flex-wrap gap-1">
                  {configured.map((provider) => (
                    <button
                      key={provider.id}
                      type="button"
                      aria-pressed={provider.id === shown?.id}
                      onClick={() => browse(provider.id)}
                      className={cn(
                        "rounded px-2 py-1 text-xs transition-colors",
                        provider.id === shown?.id
                          ? "bg-[var(--color-bg-elevated)] text-[var(--color-text-primary)]"
                          : "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]",
                      )}
                    >
                      {provider.name}
                    </button>
                  ))}
                </div>
              )}

              {shown && (
                <ModelCombobox
                  key={shown.id}
                  inline
                  value={activeProvider === shown.id ? (activeModel ?? "") : ""}
                  onSelect={(model) => void handleSelect(model)}
                  models={listModels}
                  defaultModel={
                    isRepoProvider && defaultModel ? defaultModel : shown.default_model
                  }
                  loading={modelsLoading}
                  ariaLabel={`${shown.name} models`}
                  labels={{
                    placeholder: labels.search,
                    useTyped: labels.useTyped,
                    defaultTag: isRepoProvider ? labels.repoDefault : labels.providerDefault,
                    loading: labels.loading,
                    noMatch: labels.noMatch,
                  }}
                />
              )}

              {configured.length < providers.length && (
                <p className="flex items-center justify-between gap-2 px-1 text-[11px] text-[var(--color-text-tertiary)]">
                  <span>{labels.needsSetup}</span>
                  <a href={settingsHref} className="text-[var(--color-accent-primary)] hover:underline">
                    {labels.configure}
                  </a>
                </p>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
