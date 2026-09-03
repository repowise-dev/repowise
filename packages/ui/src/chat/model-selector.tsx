"use client";

/**
 * Presentational shell for the chat model selector. The interactive surface
 * (popover, "add API key" form, model list, active highlight) lives here;
 * the data hook (e.g. SWR fetch of `/providers`) lives in the consumer app
 * via a thin wrapper that maps its hook output onto these props.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronUp, Check } from "lucide-react";
import { cn } from "../lib/cn";

export interface ModelSelectorProvider {
  id: string;
  name: string;
  models: string[];
  default_model: string;
  configured: boolean;
}

export interface ModelSelectorProps {
  providers: ModelSelectorProvider[];
  activeProvider: string | null;
  activeModel: string | null;
  isLoading?: boolean;
  onActivate: (providerId: string, model: string) => void | Promise<void>;
  /** API-key management belongs in Settings, outside routine model choice. */
  settingsHref?: string;
  /** @deprecated Key management is ignored here and belongs in Settings. */
  onSaveKey?: (providerId: string, key: string) => void | Promise<void>;
  /** Optional label override when no provider is active. */
  emptyLabel?: string;
  className?: string;
}

export function ModelSelector({
  providers,
  activeProvider,
  activeModel,
  isLoading = false,
  onActivate,
  settingsHref = "/settings",
  emptyLabel = "Select model",
  className,
}: ModelSelectorProps) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const close = useCallback(() => {
    setOpen(false);
    window.requestAnimationFrame(() => triggerRef.current?.focus());
  }, []);
  useEffect(() => {
    if (!open) return;
    panelRef.current?.querySelector<HTMLElement>("button, a")?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [close, open]);

  const activeP = providers.find((p) => p.id === activeProvider);
  const label = activeP
    ? `${activeP.name} · ${activeModel ?? activeP.default_model}`
    : emptyLabel;

  async function handleSelect(providerId: string, model: string) {
    await onActivate(providerId, model);
    close();
  }

  return (
    <div className={cn("relative", className)}>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen(!open)}
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

          <div ref={panelRef} role="dialog" aria-label="Choose conversation model" className="absolute bottom-full left-0 z-[calc(var(--z-dropdown)+1)] mb-1 w-72 overflow-hidden rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-overlay)] shadow-[var(--shadow-lg)]">
            <div className="p-2 space-y-1 max-h-80 overflow-y-auto">
              {providers.map((provider) => {
                const isConfigured = provider.configured;
                return (
                  <div key={provider.id}>
                    <div className="flex items-center justify-between px-2 py-1">
                      <span
                        className={cn(
                          "text-xs font-medium",
                          isConfigured
                            ? "text-[var(--color-text-primary)]"
                            : "text-[var(--color-text-tertiary)]",
                        )}
                      >
                        {provider.name}
                      </span>
                      {!isConfigured && (
                        <a href={settingsHref} className="text-[10px] text-[var(--color-accent-primary)] hover:underline">
                          Configure in Settings
                        </a>
                      )}
                    </div>

                    {isConfigured &&
                      provider.models.map((model) => {
                        const isActive =
                          activeProvider === provider.id &&
                          activeModel === model;
                        return (
                          <button
                            key={model}
                            onClick={() => handleSelect(provider.id, model)}
                            className={cn(
                              "flex w-full items-center gap-2 rounded-md px-3 py-1.5 text-xs transition-colors",
                              isActive
                                ? "bg-[var(--color-accent-secondary)]/10 text-[var(--color-accent-secondary)]"
                                : "text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]",
                            )}
                          >
                            <span className="flex-1 text-left font-mono truncate">
                              {model}
                            </span>
                            {isActive && (
                              <Check className="h-3 w-3 shrink-0 text-[var(--color-accent-secondary)]" />
                            )}
                          </button>
                        );
                      })}

                    {!isConfigured &&
                      provider.models.map((model) => (
                        <div
                          key={model}
                          className="px-3 py-1.5 text-xs font-mono text-[var(--color-text-tertiary)] opacity-50"
                        >
                          {model}
                        </div>
                      ))}
                  </div>
                );
              })}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
