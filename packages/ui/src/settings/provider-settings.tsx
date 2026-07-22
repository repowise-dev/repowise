"use client";

import { useState } from "react";
import {
  Check,
  KeyRound,
  Loader2,
  Plug,
  Trash2,
  X,
} from "lucide-react";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Label } from "../ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../ui/select";
import { cn } from "../lib/cn";

/** One provider row for the settings surface. Structurally a subset of the
 *  api-client `ProviderInfo`, redeclared here so the shared component stays
 *  free of any api-client dependency. */
export interface ProviderOption {
  id: string;
  name: string;
  models: string[];
  default_model: string;
  /** Whether a usable key is on file (server-computed; never the key itself). */
  configured: boolean;
}

export type ProviderValidationStatus = "idle" | "testing" | "ok" | "error";

export interface ProviderValidationState {
  status: ProviderValidationStatus;
  error?: string | null;
}

/** Providers that authenticate without an API key (local runtimes / CLIs).
 *  For these the key affordance is hidden — only reachability is validated. */
const KEYLESS_PROVIDERS = new Set(["ollama", "opencode", "codex_cli", "mock"]);

export interface ProviderSettingsProps {
  providers: ProviderOption[];
  /** The active provider/model selection for this repo. */
  active: { provider: string | null; model: string | null };
  onAddKey: (providerId: string, apiKey: string) => Promise<void> | void;
  onRemoveKey: (providerId: string) => Promise<void> | void;
  onSetActive: (providerId: string, model?: string) => Promise<void> | void;
  onValidate: (providerId: string) => Promise<void> | void;
  /** Per-provider probe state, keyed by provider id. */
  validation?: Record<string, ProviderValidationState>;
  /** Rendered when there are no providers at all (e.g. a fetch miss). */
  emptyHint?: string;
}

export function ProviderSettings({
  providers,
  active,
  onAddKey,
  onRemoveKey,
  onSetActive,
  onValidate,
  validation = {},
  emptyHint = "No providers available.",
}: ProviderSettingsProps) {
  if (providers.length === 0) {
    return (
      <p className="text-sm text-[var(--color-text-tertiary)]">{emptyHint}</p>
    );
  }

  return (
    <div className="space-y-2.5">
      {providers.map((provider) => (
        <ProviderRow
          key={provider.id}
          provider={provider}
          isActive={active.provider === provider.id}
          activeModel={active.provider === provider.id ? active.model : null}
          onAddKey={onAddKey}
          onRemoveKey={onRemoveKey}
          onSetActive={onSetActive}
          onValidate={onValidate}
          validation={validation[provider.id]}
        />
      ))}
    </div>
  );
}

function ProviderRow({
  provider,
  isActive,
  activeModel,
  onAddKey,
  onRemoveKey,
  onSetActive,
  onValidate,
  validation,
}: {
  provider: ProviderOption;
  isActive: boolean;
  activeModel: string | null;
  onAddKey: ProviderSettingsProps["onAddKey"];
  onRemoveKey: ProviderSettingsProps["onRemoveKey"];
  onSetActive: ProviderSettingsProps["onSetActive"];
  onValidate: ProviderSettingsProps["onValidate"];
  validation: ProviderValidationState | undefined;
}) {
  const [editing, setEditing] = useState(false);
  const [keyDraft, setKeyDraft] = useState("");
  const [busy, setBusy] = useState<null | "save" | "remove" | "activate">(null);

  const keyless = KEYLESS_PROVIDERS.has(provider.id);
  const status = validation?.status ?? "idle";

  async function saveKey() {
    const key = keyDraft.trim();
    if (!key) return;
    setBusy("save");
    try {
      await onAddKey(provider.id, key);
      setKeyDraft("");
      setEditing(false);
    } finally {
      setBusy(null);
    }
  }

  async function removeKey() {
    setBusy("remove");
    try {
      await onRemoveKey(provider.id);
    } finally {
      setBusy(null);
    }
  }

  async function activate(model?: string) {
    setBusy("activate");
    try {
      await onSetActive(provider.id, model);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div
      className={cn(
        "rounded-md border p-3 transition-colors",
        isActive
          ? "border-[var(--color-border-active)] bg-[var(--color-accent-muted)]"
          : "border-[var(--color-border-default)]",
      )}
    >
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-[var(--color-text-primary)]">
              {provider.name}
            </span>
            {isActive && (
              <Badge variant="accent" className="text-[10px]">
                Active
              </Badge>
            )}
            {keyless ? (
              <Badge variant="outline" className="text-[10px]">
                No key required
              </Badge>
            ) : provider.configured ? (
              <span className="inline-flex items-center gap-1 text-[11px] text-[var(--color-success)]">
                <Check className="h-3 w-3" />
                Key set
              </span>
            ) : (
              <span className="text-[11px] text-[var(--color-text-tertiary)]">
                No key
              </span>
            )}
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-1.5">
          {!isActive && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-7 text-xs"
              disabled={busy !== null || (!keyless && !provider.configured)}
              onClick={() => activate()}
              aria-label={`Use ${provider.name}`}
            >
              {busy === "activate" ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                "Use"
              )}
            </Button>
          )}
          {(keyless || provider.configured) && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-7 gap-1.5 text-xs"
              disabled={status === "testing"}
              onClick={() => onValidate(provider.id)}
              aria-label={`Test ${provider.name} connection`}
            >
              {status === "testing" ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Plug className="h-3.5 w-3.5" />
              )}
              Test
            </Button>
          )}
          {!keyless &&
            (editing ? null : (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 gap-1.5 text-xs"
                onClick={() => setEditing(true)}
              >
                <KeyRound className="h-3.5 w-3.5" />
                {provider.configured ? "Update key" : "Add key"}
              </Button>
            ))}
          {!keyless && provider.configured && !editing && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-7 w-7 p-0 text-[var(--color-text-tertiary)] hover:text-[var(--color-error)]"
              disabled={busy !== null}
              onClick={removeKey}
              aria-label={`Remove ${provider.name} key`}
            >
              {busy === "remove" ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Trash2 className="h-3.5 w-3.5" />
              )}
            </Button>
          )}
        </div>
      </div>

      {editing && !keyless && (
        <div className="mt-3 flex items-center gap-2">
          <Input
            type="password"
            autoComplete="off"
            autoFocus
            aria-label={`${provider.name} API key`}
            value={keyDraft}
            onChange={(e) => setKeyDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                void saveKey();
              }
              if (e.key === "Escape") {
                setEditing(false);
                setKeyDraft("");
              }
            }}
            placeholder={`Paste your ${provider.name} API key`}
            className="h-8 font-mono text-xs"
          />
          <Button
            type="button"
            size="sm"
            className="h-8 text-xs"
            disabled={!keyDraft.trim() || busy === "save"}
            onClick={saveKey}
            aria-label={`Save ${provider.name} API key`}
          >
            {busy === "save" ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              "Save"
            )}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-8 w-8 p-0"
            onClick={() => {
              setEditing(false);
              setKeyDraft("");
            }}
            aria-label="Cancel"
          >
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
      )}

      {isActive && provider.models.length > 0 && (
        <div className="mt-3 flex items-center gap-2">
          <Label className="text-xs text-[var(--color-text-secondary)]">Model</Label>
          <Select
            value={activeModel ?? provider.default_model}
            onValueChange={(m) => activate(m)}
          >
            <SelectTrigger className="h-8 w-64 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {provider.models.map((m) => (
                <SelectItem key={m} value={m} className="text-xs">
                  {m}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}

      {status === "ok" && (
        <p className="mt-2 inline-flex items-center gap-1 text-xs text-[var(--color-success)]">
          <Check className="h-3.5 w-3.5" />
          Connection verified
        </p>
      )}
      {status === "error" && (
        <p className="mt-2 text-xs text-[var(--color-error)]">
          {validation?.error || "Connection failed"}
        </p>
      )}
    </div>
  );
}
