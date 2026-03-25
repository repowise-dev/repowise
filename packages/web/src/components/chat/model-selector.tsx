"use client";

import { useState } from "react";
import { ChevronDown, Check, Key, X } from "lucide-react";
import { useProviders } from "@/lib/hooks/use-providers";
import { cn } from "@/lib/utils/cn";

export function ModelSelector() {
  const {
    providers,
    activeProvider,
    activeModel,
    isLoading,
    activate,
    saveKey,
  } = useProviders();

  const [open, setOpen] = useState(false);
  const [addingKeyFor, setAddingKeyFor] = useState<string | null>(null);
  const [keyInput, setKeyInput] = useState("");
  const [saving, setSaving] = useState(false);

  const activeP = providers.find((p) => p.id === activeProvider);
  const label = activeP
    ? `${activeP.name} · ${activeModel ?? activeP.default_model}`
    : "Select model";

  async function handleSelect(providerId: string, model: string) {
    await activate(providerId, model);
    setOpen(false);
  }

  async function handleSaveKey(providerId: string) {
    if (!keyInput.trim()) return;
    setSaving(true);
    try {
      await saveKey(providerId, keyInput.trim());
      setAddingKeyFor(null);
      setKeyInput("");
    } catch {
      // error handled by SWR
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        className={cn(
          "flex items-center gap-1.5 rounded-md px-2 py-1 text-xs",
          "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]",
          "hover:bg-[var(--color-bg-elevated)] transition-colors",
          "border border-[var(--color-border-default)]",
        )}
      >
        <span className="truncate max-w-[200px]">{isLoading ? "..." : label}</span>
        <ChevronDown className="h-3 w-3 shrink-0" />
      </button>

      {open && (
        <>
          {/* Backdrop */}
          <div
            className="fixed inset-0 z-[var(--z-dropdown)]"
            onClick={() => {
              setOpen(false);
              setAddingKeyFor(null);
            }}
          />

          {/* Popover */}
          <div className="absolute right-0 top-full mt-1 z-[calc(var(--z-dropdown)+1)] w-72 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-overlay)] shadow-lg overflow-hidden">
            <div className="p-2 space-y-1 max-h-80 overflow-y-auto">
              {providers.map((provider) => {
                const isConfigured = provider.configured;
                return (
                  <div key={provider.id}>
                    {/* Provider header */}
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
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            setAddingKeyFor(
                              addingKeyFor === provider.id
                                ? null
                                : provider.id,
                            );
                            setKeyInput("");
                          }}
                          className="text-[10px] text-[var(--color-accent-primary)] hover:underline flex items-center gap-0.5"
                        >
                          <Key className="h-3 w-3" />
                          Add key
                        </button>
                      )}
                    </div>

                    {/* Add key form */}
                    {addingKeyFor === provider.id && (
                      <div className="px-2 pb-1.5 flex gap-1">
                        <input
                          type="password"
                          value={keyInput}
                          onChange={(e) => setKeyInput(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter")
                              handleSaveKey(provider.id);
                          }}
                          placeholder="API key..."
                          className="flex-1 rounded border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-2 py-1 text-xs text-[var(--color-text-primary)] outline-none focus:ring-1 focus:ring-[var(--color-accent-primary)]"
                          autoFocus
                        />
                        <button
                          onClick={() => handleSaveKey(provider.id)}
                          disabled={saving || !keyInput.trim()}
                          className="rounded bg-[var(--color-accent-primary)] px-2 py-1 text-xs text-white disabled:opacity-50"
                        >
                          {saving ? "..." : "Save"}
                        </button>
                        <button
                          onClick={() => {
                            setAddingKeyFor(null);
                            setKeyInput("");
                          }}
                          className="p-1 text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]"
                        >
                          <X className="h-3 w-3" />
                        </button>
                      </div>
                    )}

                    {/* Models */}
                    {isConfigured &&
                      provider.models.map((model) => {
                        const isActive =
                          activeProvider === provider.id &&
                          activeModel === model;
                        return (
                          <button
                            key={model}
                            onClick={() =>
                              handleSelect(provider.id, model)
                            }
                            className={cn(
                              "flex w-full items-center gap-2 rounded-md px-3 py-1.5 text-xs transition-colors",
                              isActive
                                ? "bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]"
                                : "text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]",
                            )}
                          >
                            <span className="flex-1 text-left font-mono truncate">
                              {model}
                            </span>
                            {isActive && (
                              <Check className="h-3 w-3 shrink-0" />
                            )}
                          </button>
                        );
                      })}

                    {/* Unconfigured models (greyed) */}
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
