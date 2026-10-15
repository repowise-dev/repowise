"use client";

import { useEffect, useId, useState, type ReactNode } from "react";
import {
  Check,
  KeyRound,
  Plug,
  Trash2,
  X,
} from "lucide-react";
import { Spinner } from "../ui/spinner";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Label } from "../ui/label";
import { cn } from "../lib/cn";
import { ModelCombobox, type ModelChoice } from "./model-combobox";

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
  /** `false` for local runtimes and CLIs: the key affordance is hidden and
   *  only reachability is validated. */
  requires_key?: boolean;
  /** Takes a custom endpoint; shows the Base URL field. */
  accepts_base_url?: boolean;
  /** Can point at a local OpenAI-compatible server and run with no key. */
  local_server?: boolean;
}

/** Where each active value came from: `config.yaml`, `global`, `env:<VAR>`,
 *  `default`, `autodetect`; null when unset. */
export interface ProviderProvenance {
  provider?: string | null;
  model?: string | null;
  base_url?: string | null;
}

/** A provider's model list, live or the built-in one with the reason. */
export interface ProviderModelList {
  models: ModelChoice[];
  source: "live" | "catalog";
  default_model: string;
  error?: string | null;
}

export interface ProviderSettingsLabels {
  model: string;
  baseUrl: string;
  baseUrlHint: string;
  baseUrlInvalid: string;
  localServer: string;
  testConnection: string;
  connected: (count: number) => string;
  save: string;
  useProvider: (name: string) => string;
  cancel: string;
  /** Shown before a save that changes the model. */
  regenerateNotice: string;
  /** One muted line naming where a value came from, or null to show none. */
  provenance: (source: string) => string | null;
}

/** Default wording for a provenance value. */
export function describeSource(source: string): string | null {
  if (source === "config.yaml") return "Saved in .repowise/config.yaml.";
  if (source === "global") return "From the server default in Settings.";
  if (source === "default") return "The provider's default.";
  if (source === "autodetect") return "Picked because its API key is set.";
  if (source.startsWith("env:")) {
    return `From the ${source.slice(4)} environment variable. Saving here takes precedence.`;
  }
  return null;
}

export const DEFAULT_PROVIDER_SETTINGS_LABELS: ProviderSettingsLabels = {
  model: "Model",
  baseUrl: "Base URL",
  baseUrlHint: "Leave empty to use the provider's own endpoint.",
  baseUrlInvalid: "Enter a URL that starts with http:// or https://.",
  localServer: "Local server",
  testConnection: "Test connection",
  connected: (count) =>
    `Connected. ${count} ${count === 1 ? "model" : "models"} listed.`,
  save: "Save",
  useProvider: (name) => `Use ${name}`,
  cancel: "Cancel",
  regenerateNotice:
    "Documentation is regenerated with the new model on the next sync. To try a model first, pick it in the chat model menu; that changes one conversation only.",
  provenance: describeSource,
};

/** OpenAI-compatible servers people run on their own machine. */
export const LOCAL_SERVER_PRESETS: { name: string; url: string }[] = [
  { name: "LM Studio", url: "http://localhost:1234/v1" },
  { name: "Ollama", url: "http://localhost:11434/v1" },
  { name: "vLLM", url: "http://localhost:8000/v1" },
];

export type ProviderValidationStatus = "idle" | "testing" | "ok" | "error";

export interface ProviderValidationState {
  status: ProviderValidationStatus;
  error?: string | null;
}

export interface ProviderSettingsProps {
  providers: ProviderOption[];
  /** The active selection for this scope, with where each value came from. */
  active: {
    provider: string | null;
    model: string | null;
    base_url?: string | null;
    provenance?: ProviderProvenance;
  };
  onAddKey: (providerId: string, apiKey: string) => Promise<void> | void;
  onRemoveKey: (providerId: string) => Promise<void> | void;
  /** Saves provider, model and (when given) base URL; `""` clears it. */
  onSetActive: (
    providerId: string,
    model?: string,
    options?: { baseUrl?: string },
  ) => Promise<void> | void;
  onValidate: (providerId: string) => Promise<void> | void;
  /** Fetches a provider's models; `baseUrl` probes an unsaved endpoint and
   *  `refresh` skips caches. Called when the model list opens and on Test
   *  connection. Without it the static `models` are offered. */
  onLoadModels?: (
    providerId: string,
    options?: { baseUrl?: string; refresh?: boolean },
  ) => Promise<ProviderModelList>;
  /** Per-provider probe state, keyed by provider id. */
  validation?: Record<string, ProviderValidationState>;
  /** Rendered when there are no providers at all (e.g. a fetch miss). */
  emptyHint?: string;
  labels?: Partial<ProviderSettingsLabels>;
}

export function ProviderSettings({
  providers,
  active,
  onAddKey,
  onRemoveKey,
  onSetActive,
  onValidate,
  onLoadModels,
  validation = {},
  emptyHint = "No providers available.",
  labels: labelOverrides,
}: ProviderSettingsProps) {
  const labels = { ...DEFAULT_PROVIDER_SETTINGS_LABELS, ...labelOverrides };
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
          active={active}
          onAddKey={onAddKey}
          onRemoveKey={onRemoveKey}
          onSetActive={onSetActive}
          onValidate={onValidate}
          onLoadModels={onLoadModels}
          validation={validation[provider.id]}
          labels={labels}
        />
      ))}
    </div>
  );
}

function ProviderRow({
  provider,
  isActive,
  active,
  onAddKey,
  onRemoveKey,
  onSetActive,
  onValidate,
  onLoadModels,
  validation,
  labels,
}: {
  provider: ProviderOption;
  isActive: boolean;
  active: ProviderSettingsProps["active"];
  onAddKey: ProviderSettingsProps["onAddKey"];
  onRemoveKey: ProviderSettingsProps["onRemoveKey"];
  onSetActive: ProviderSettingsProps["onSetActive"];
  onValidate: ProviderSettingsProps["onValidate"];
  onLoadModels: ProviderSettingsProps["onLoadModels"];
  validation: ProviderValidationState | undefined;
  labels: ProviderSettingsLabels;
}) {
  const [editing, setEditing] = useState(false);
  const [keyDraft, setKeyDraft] = useState("");
  const [busy, setBusy] = useState<null | "save" | "remove">(null);
  // "Use" opens the form for an inactive provider; nothing is written until Save.
  const [settingUp, setSettingUp] = useState(false);

  const keyless = provider.requires_key === false;
  const usable = keyless || provider.configured || provider.local_server === true;
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
          {!isActive && !settingUp && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-7 text-xs"
              disabled={busy !== null || !usable}
              onClick={() => setSettingUp(true)}
              aria-label={`Use ${provider.name}`}
            >
              Use
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
                <Spinner size="sm" />
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
                <Spinner size="sm" />
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
              <Spinner size="sm" />
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

      {(isActive || settingUp) && (
        <ProviderForm
          provider={provider}
          isActive={isActive}
          active={active}
          onSetActive={onSetActive}
          onLoadModels={onLoadModels}
          onDone={() => setSettingUp(false)}
          labels={labels}
        />
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

const HTTP_URL = /^https?:\/\/[^\s/?#]+/i;

/** Model and endpoint for one provider. Writes nothing until Save. */
function ProviderForm({
  provider,
  isActive,
  active,
  onSetActive,
  onLoadModels,
  onDone,
  labels,
}: {
  provider: ProviderOption;
  isActive: boolean;
  active: ProviderSettingsProps["active"];
  onSetActive: ProviderSettingsProps["onSetActive"];
  onLoadModels: ProviderSettingsProps["onLoadModels"];
  onDone: () => void;
  labels: ProviderSettingsLabels;
}) {
  const formId = useId();
  const savedModel = isActive ? (active.model ?? "") : provider.default_model;
  const savedUrl = isActive ? (active.base_url ?? "") : "";
  const [model, setModel] = useState(savedModel);
  const [baseUrl, setBaseUrl] = useState(savedUrl);
  // The list and the endpoint it was fetched for ("" = the saved one).
  const [list, setList] = useState<{ url: string; result: ProviderModelList } | null>(null);
  const [loading, setLoading] = useState(false);
  const [tested, setTested] = useState(false);
  const [saving, setSaving] = useState(false);

  // A save (here or elsewhere) changes what the drafts start from.
  useEffect(() => setModel(savedModel), [savedModel]);
  useEffect(() => setBaseUrl(savedUrl), [savedUrl]);

  const url = baseUrl.trim();
  const urlValid = !url || HTTP_URL.test(url);
  const probeUrl = url && url !== savedUrl ? url : "";
  const current = list?.url === probeUrl ? list.result : null;
  const modelChanged = !isActive || model.trim() !== savedModel;
  const dirty = modelChanged || url !== savedUrl;

  async function load(refresh = false) {
    if (!onLoadModels || !urlValid || (current && !refresh)) return;
    setLoading(true);
    try {
      const result = await onLoadModels(provider.id, {
        ...(probeUrl ? { baseUrl: probeUrl } : {}),
        refresh,
      });
      setList({ url: probeUrl, result });
    } catch (error) {
      setList({
        url: probeUrl,
        result: {
          models: [],
          source: "catalog",
          default_model: provider.default_model,
          error: error instanceof Error ? error.message : String(error),
        },
      });
    } finally {
      setLoading(false);
    }
  }

  async function save() {
    setSaving(true);
    try {
      await onSetActive(
        provider.id,
        model.trim() || undefined,
        url !== savedUrl ? { baseUrl: url } : undefined,
      );
      onDone();
    } catch {
      // The consumer reports the failure; the drafts stay for another try.
    } finally {
      setSaving(false);
    }
  }

  const models = current?.models ?? provider.models.map((id) => ({ id }));
  const provenance = (field: "model" | "base_url", unchanged: boolean) => {
    const source = isActive && unchanged ? active.provenance?.[field] : null;
    return source ? labels.provenance(source) : null;
  };
  const modelSource = provenance("model", model.trim() === savedModel);
  const urlSource = provenance("base_url", url === savedUrl);

  return (
    <div className="mt-3 space-y-3 border-t border-[var(--color-border-default)] pt-3">
      <div className="space-y-1.5">
        <Label htmlFor={`${formId}-model`} className="text-xs text-[var(--color-text-secondary)]">
          {labels.model}
        </Label>
        <ModelCombobox
          id={`${formId}-model`}
          ariaLabel={`${provider.name} ${labels.model}`}
          value={model}
          onSelect={setModel}
          models={models}
          defaultModel={current?.default_model ?? provider.default_model}
          loading={loading}
          onOpen={() => void load()}
          className="sm:max-w-sm"
        />
        {modelSource && <Muted>{modelSource}</Muted>}
        {current?.error && !tested && <Muted>{current.error}</Muted>}
      </div>

      {provider.accepts_base_url && (
        <div className="space-y-1.5">
          <Label htmlFor={`${formId}-url`} className="text-xs text-[var(--color-text-secondary)]">
            {labels.baseUrl}
          </Label>
          <div className="flex flex-wrap items-center gap-2">
            <Input
              id={`${formId}-url`}
              type="url"
              inputMode="url"
              autoComplete="off"
              spellCheck={false}
              value={baseUrl}
              onChange={(e) => {
                setBaseUrl(e.target.value);
                setTested(false);
              }}
              placeholder="https://"
              aria-invalid={!urlValid || undefined}
              aria-describedby={`${formId}-url-hint`}
              className="h-8 font-mono text-xs sm:max-w-sm"
            />
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-8 gap-1.5 text-xs"
              disabled={!onLoadModels || !urlValid || loading}
              onClick={() => {
                setTested(true);
                void load(true);
              }}
            >
              {loading && tested ? <Spinner size="sm" /> : <Plug className="h-3.5 w-3.5" />}
              {labels.testConnection}
            </Button>
          </div>
          {provider.local_server && (
            <p className="flex flex-wrap items-center gap-x-2 text-xs text-[var(--color-text-tertiary)]">
              <span>{labels.localServer}:</span>
              {LOCAL_SERVER_PRESETS.map((preset) => (
                <button
                  key={preset.url}
                  type="button"
                  className="text-[var(--color-accent-primary)] hover:underline"
                  onClick={() => {
                    setBaseUrl(preset.url);
                    setTested(false);
                  }}
                >
                  {preset.name}
                </button>
              ))}
            </p>
          )}
          <Muted id={`${formId}-url-hint`}>
            {!urlValid ? labels.baseUrlInvalid : (urlSource ?? labels.baseUrlHint)}
          </Muted>
          {tested && current && !loading && (
            current.source === "live" ? (
              <p role="status" className="inline-flex items-center gap-1 text-xs text-[var(--color-success)]">
                <Check className="h-3.5 w-3.5" />
                {labels.connected(current.models.length)}
              </p>
            ) : (
              <p role="alert" className="text-xs text-[var(--color-error)]">
                {current.error}
              </p>
            )
          )}
        </div>
      )}

      {dirty && modelChanged && <Muted>{labels.regenerateNotice}</Muted>}

      <div className="flex items-center gap-2">
        <Button
          type="button"
          size="sm"
          className="h-8 text-xs"
          disabled={!dirty || !urlValid || saving}
          onClick={save}
        >
          {saving ? <Spinner size="sm" /> : isActive ? labels.save : labels.useProvider(provider.name)}
        </Button>
        {!isActive && (
          <Button type="button" variant="ghost" size="sm" className="h-8 text-xs" onClick={onDone}>
            {labels.cancel}
          </Button>
        )}
      </div>
    </div>
  );
}

function Muted({ id, children }: { id?: string; children: ReactNode }) {
  return (
    <p id={id} className="text-xs text-[var(--color-text-tertiary)]">
      {children}
    </p>
  );
}
