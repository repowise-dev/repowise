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
  /** The repo's config.yaml URL is not confirmed on this machine. */
  unconfirmedUrl: (url: string) => string;
  trustUrl: string;
  active: string;
  noKeyRequired: string;
  keySet: string;
  noKey: string;
  use: string;
  useAria: (name: string) => string;
  test: string;
  testAria: (name: string) => string;
  addKey: string;
  updateKey: string;
  removeKeyAria: (name: string) => string;
  keyAria: (name: string) => string;
  keyPlaceholder: (name: string) => string;
  saveKeyAria: (name: string) => string;
  connectionVerified: string;
  connectionFailed: string;
  /** Shown when a save is rejected; the drafts stay. */
  saveFailed: string;
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
  unconfirmedUrl: (url) =>
    `This repository's .repowise/config.yaml sends requests to ${url}, which has not been confirmed on this machine, so your API key is not sent there. Trust it only if you set it or know the host.`,
  trustUrl: "Trust this URL",
  active: "Active",
  noKeyRequired: "No key required",
  keySet: "Key set",
  noKey: "No key",
  use: "Use",
  useAria: (name) => `Use ${name}`,
  test: "Test",
  testAria: (name) => `Test ${name} connection`,
  addKey: "Add key",
  updateKey: "Update key",
  removeKeyAria: (name) => `Remove ${name} key`,
  keyAria: (name) => `${name} API key`,
  keyPlaceholder: (name) => `Paste your ${name} API key`,
  saveKeyAria: (name) => `Save ${name} API key`,
  connectionVerified: "Connection verified",
  connectionFailed: "Connection failed",
  saveFailed: "Not saved. Your changes are still here; try again.",
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
    /** The saved config.yaml URL is refused until the user confirms it. */
    base_url_unconfirmed?: boolean;
  };
  onAddKey: (providerId: string, apiKey: string) => Promise<void> | void;
  onRemoveKey: (providerId: string) => Promise<void> | void;
  /** Saves provider, model and (when given) base URL; `""` clears it.
   *  Reject to keep the form open with the user's drafts. */
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

interface RowProps {
  onAddKey: ProviderSettingsProps["onAddKey"];
  onRemoveKey: ProviderSettingsProps["onRemoveKey"];
  onSetActive: ProviderSettingsProps["onSetActive"];
  onValidate: ProviderSettingsProps["onValidate"];
  onLoadModels: ProviderSettingsProps["onLoadModels"];
  provider: ProviderOption;
  isActive: boolean;
  active: ProviderSettingsProps["active"];
  validation: ProviderValidationState | undefined;
  labels: ProviderSettingsLabels;
}

function ProviderRow({ provider, isActive, labels, validation, ...props }: RowProps) {
  // "Use" opens the form for an inactive provider; nothing is written until Save.
  const [settingUp, setSettingUp] = useState(false);
  const keys = useKeyEditor(provider.id, props.onAddKey, props.onRemoveKey);
  const keyless = provider.requires_key === false;

  return (
    <div
      className={cn(
        "rounded-md border p-3 transition-colors",
        isActive
          ? "border-[var(--color-selection-border)] bg-[var(--color-selection-wash)]"
          : "border-[var(--color-border-default)]",
      )}
    >
      <div className="flex items-center gap-3">
        <ProviderHeading provider={provider} isActive={isActive} labels={labels} />
        <div className="flex shrink-0 items-center gap-1.5">
          {!isActive && !settingUp && (
            <UseButton
              provider={provider}
              disabled={keys.busy !== null}
              onUse={() => setSettingUp(true)}
              labels={labels}
            />
          )}
          {(keyless || provider.configured) && (
            <TestButton
              provider={provider}
              testing={validation?.status === "testing"}
              onTest={() => props.onValidate(provider.id)}
              labels={labels}
            />
          )}
          {!keyless && !keys.editing && (
            <KeyButtons provider={provider} keys={keys} labels={labels} />
          )}
        </div>
      </div>

      {keys.editing && !keyless && <KeyEditor provider={provider} keys={keys} labels={labels} />}

      {(isActive || settingUp) && (
        <ProviderForm
          provider={provider}
          isActive={isActive}
          active={props.active}
          onSetActive={props.onSetActive}
          onLoadModels={props.onLoadModels}
          onDone={() => setSettingUp(false)}
          labels={labels}
        />
      )}

      <ValidationLine validation={validation} labels={labels} />
    </div>
  );
}

function ProviderHeading({
  provider,
  isActive,
  labels,
}: {
  provider: ProviderOption;
  isActive: boolean;
  labels: ProviderSettingsLabels;
}) {
  return (
    <div className="min-w-0 flex-1">
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium text-[var(--color-text-primary)]">
          {provider.name}
        </span>
        {isActive && (
          <Badge variant="accent" className="text-[10px]">
            {labels.active}
          </Badge>
        )}
        <KeyStatus provider={provider} labels={labels} />
      </div>
    </div>
  );
}

function KeyStatus({ provider, labels }: { provider: ProviderOption; labels: ProviderSettingsLabels }) {
  if (provider.requires_key === false) {
    return (
      <Badge variant="outline" className="text-[10px]">
        {labels.noKeyRequired}
      </Badge>
    );
  }
  if (provider.configured) {
    return (
      <span className="inline-flex items-center gap-1 text-[11px] text-[var(--color-success)]">
        <Check className="h-3 w-3" />
        {labels.keySet}
      </span>
    );
  }
  return <span className="text-[11px] text-[var(--color-text-tertiary)]">{labels.noKey}</span>;
}

function UseButton({
  provider,
  disabled,
  onUse,
  labels,
}: {
  provider: ProviderOption;
  disabled: boolean;
  onUse: () => void;
  labels: ProviderSettingsLabels;
}) {
  const usable =
    provider.requires_key === false || provider.configured || provider.local_server === true;
  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      className="h-7 text-xs"
      disabled={disabled || !usable}
      onClick={onUse}
      aria-label={labels.useAria(provider.name)}
    >
      {labels.use}
    </Button>
  );
}

function TestButton({
  provider,
  testing,
  onTest,
  labels,
}: {
  provider: ProviderOption;
  testing: boolean;
  onTest: () => void;
  labels: ProviderSettingsLabels;
}) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      className="h-7 gap-1.5 text-xs"
      disabled={testing}
      onClick={onTest}
      aria-label={labels.testAria(provider.name)}
    >
      {testing ? <Spinner size="sm" /> : <Plug className="h-3.5 w-3.5" />}
      {labels.test}
    </Button>
  );
}

type KeyEditorState = ReturnType<typeof useKeyEditor>;

/** The add/update/remove key affordance of one row. */
function useKeyEditor(
  providerId: string,
  onAddKey: ProviderSettingsProps["onAddKey"],
  onRemoveKey: ProviderSettingsProps["onRemoveKey"],
) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState<null | "save" | "remove">(null);

  async function run(kind: "save" | "remove", action: () => Promise<void> | void) {
    setBusy(kind);
    try {
      await action();
    } finally {
      setBusy(null);
    }
  }

  function cancel() {
    setEditing(false);
    setDraft("");
  }

  async function save() {
    const key = draft.trim();
    if (!key) return;
    await run("save", async () => {
      await onAddKey(providerId, key);
      cancel();
    });
  }

  return {
    editing,
    draft,
    busy,
    open: () => setEditing(true),
    setDraft,
    cancel,
    save,
    remove: () => run("remove", () => onRemoveKey(providerId)),
  };
}

function KeyButtons({
  provider,
  keys,
  labels,
}: {
  provider: ProviderOption;
  keys: KeyEditorState;
  labels: ProviderSettingsLabels;
}) {
  return (
    <>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="h-7 gap-1.5 text-xs"
        onClick={keys.open}
      >
        <KeyRound className="h-3.5 w-3.5" />
        {provider.configured ? labels.updateKey : labels.addKey}
      </Button>
      {provider.configured && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-7 w-7 p-0 text-[var(--color-text-tertiary)] hover:text-[var(--color-error)]"
          disabled={keys.busy !== null}
          onClick={() => void keys.remove()}
          aria-label={labels.removeKeyAria(provider.name)}
        >
          {keys.busy === "remove" ? <Spinner size="sm" /> : <Trash2 className="h-3.5 w-3.5" />}
        </Button>
      )}
    </>
  );
}

const KEY_INPUT_ACTIONS: Record<string, (keys: KeyEditorState) => void> = {
  Enter: (keys) => void keys.save(),
  Escape: (keys) => keys.cancel(),
};

function KeyEditor({
  provider,
  keys,
  labels,
}: {
  provider: ProviderOption;
  keys: KeyEditorState;
  labels: ProviderSettingsLabels;
}) {
  return (
    <div className="mt-3 flex items-center gap-2">
      <Input
        type="password"
        autoComplete="off"
        autoFocus
        aria-label={labels.keyAria(provider.name)}
        value={keys.draft}
        onChange={(e) => keys.setDraft(e.target.value)}
        onKeyDown={(e) => {
          const action = KEY_INPUT_ACTIONS[e.key];
          if (!action) return;
          if (e.key === "Enter") e.preventDefault();
          action(keys);
        }}
        placeholder={labels.keyPlaceholder(provider.name)}
        className="h-8 font-mono text-xs"
      />
      <Button
        type="button"
        size="sm"
        className="h-8 text-xs"
        disabled={!keys.draft.trim() || keys.busy === "save"}
        onClick={() => void keys.save()}
        aria-label={labels.saveKeyAria(provider.name)}
      >
        {keys.busy === "save" ? <Spinner size="sm" /> : labels.save}
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="h-8 w-8 p-0"
        onClick={keys.cancel}
        aria-label={labels.cancel}
      >
        <X className="h-3.5 w-3.5" />
      </Button>
    </div>
  );
}

/** One provider's key field on its own, for a surface that offers a key
 *  without the full provider list. Escape and the close button clear it. */
export function ProviderKeyEntry({
  provider,
  onAddKey,
  labels: labelOverrides,
}: {
  provider: ProviderOption;
  onAddKey: ProviderSettingsProps["onAddKey"];
  labels?: Partial<ProviderSettingsLabels>;
}) {
  const keys = useKeyEditor(provider.id, onAddKey, () => undefined);
  const labels = { ...DEFAULT_PROVIDER_SETTINGS_LABELS, ...labelOverrides };
  return <KeyEditor provider={provider} keys={keys} labels={labels} />;
}

function ValidationLine({
  validation,
  labels,
}: {
  validation: ProviderValidationState | undefined;
  labels: ProviderSettingsLabels;
}) {
  if (validation?.status === "ok") {
    return (
      <p className="mt-2 inline-flex items-center gap-1 text-xs text-[var(--color-success)]">
        <Check className="h-3.5 w-3.5" />
        {labels.connectionVerified}
      </p>
    );
  }
  if (validation?.status === "error") {
    return (
      <p className="mt-2 text-xs text-[var(--color-error)]">
        {validation.error || labels.connectionFailed}
      </p>
    );
  }
  return null;
}

const HTTP_URL = /^https?:\/\/[^\s/?#]+/i;

interface FormProps {
  provider: ProviderOption;
  isActive: boolean;
  active: ProviderSettingsProps["active"];
  onSetActive: ProviderSettingsProps["onSetActive"];
  onLoadModels: ProviderSettingsProps["onLoadModels"];
  onDone: () => void;
  labels: ProviderSettingsLabels;
}

type FormState = ReturnType<typeof useProviderFormState>;

/** Drafts for model and base URL, and the save that writes them. */
function useProviderFormState({ provider, isActive, active, onSetActive, onDone }: FormProps) {
  const savedModel = isActive ? (active.model ?? "") : provider.default_model;
  const savedUrl = isActive ? (active.base_url ?? "") : "";
  const [model, setModel] = useState(savedModel);
  const [baseUrl, setBaseUrl] = useState(savedUrl);
  const [saving, setSaving] = useState(false);
  const [saveFailed, setSaveFailed] = useState(false);

  // A save (here or elsewhere) changes what the drafts start from.
  useEffect(() => setModel(savedModel), [savedModel]);
  useEffect(() => setBaseUrl(savedUrl), [savedUrl]);

  const url = baseUrl.trim();
  const modelChanged = !isActive || model.trim() !== savedModel;

  async function save(trust = false) {
    setSaving(true);
    setSaveFailed(false);
    try {
      await onSetActive(
        provider.id,
        // Trusting re-saves the URL only; a model that came from elsewhere
        // must not get pinned into config.yaml on the way.
        trust && active.provenance?.model !== "config.yaml"
          ? undefined
          : model.trim() || undefined,
        url !== savedUrl || trust ? { baseUrl: url } : undefined,
      );
      onDone();
    } catch {
      // The consumer reports why; the drafts stay and the form says it was
      // not saved, so the user can retry without retyping.
      setSaveFailed(true);
    } finally {
      setSaving(false);
    }
  }

  return {
    model,
    setModel,
    baseUrl,
    setBaseUrl,
    url,
    savedModel,
    savedUrl,
    urlValid: !url || HTTP_URL.test(url),
    probeUrl: url && url !== savedUrl ? url : "",
    modelChanged,
    dirty: modelChanged || url !== savedUrl,
    saving,
    saveFailed,
    save,
  };
}

type ConnectionState = ReturnType<typeof useConnectionTest>;

/** The model list for the endpoint in the form, fetched on demand. */
function useConnectionTest(
  provider: ProviderOption,
  form: FormState,
  onLoadModels: ProviderSettingsProps["onLoadModels"],
) {
  // The list and the endpoint it was fetched for ("" = the saved one).
  const [list, setList] = useState<{ url: string; result: ProviderModelList } | null>(null);
  const [loading, setLoading] = useState(false);
  const [tested, setTested] = useState(false);

  // A key or endpoint change makes a fetched list stale: fetch again on open.
  useEffect(() => {
    setList(null);
    setTested(false);
  }, [provider.configured, form.savedUrl]);

  const current = list?.url === form.probeUrl ? list.result : null;

  async function load(refresh = false) {
    if (!onLoadModels || !form.urlValid || (current && !refresh)) return;
    const url = form.probeUrl;
    setLoading(true);
    try {
      const result = await onLoadModels(provider.id, {
        ...(url ? { baseUrl: url } : {}),
        refresh,
      });
      setList({ url, result });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setList({ url, result: catalogFallback(provider, message) });
    } finally {
      setLoading(false);
    }
  }

  return {
    current,
    loading,
    tested,
    canTest: Boolean(onLoadModels) && form.urlValid && !loading,
    resetTest: () => setTested(false),
    open: () => void load(),
    test: () => {
      setTested(true);
      void load(true);
    },
  };
}

function catalogFallback(provider: ProviderOption, error: string): ProviderModelList {
  return { models: [], source: "catalog", default_model: provider.default_model, error };
}

/** Model and endpoint for one provider. Writes nothing until Save. */
function ProviderForm(props: FormProps) {
  const { provider, labels } = props;
  const formId = useId();
  const form = useProviderFormState(props);
  const connection = useConnectionTest(provider, form, props.onLoadModels);
  const sourceOf = (field: "model" | "base_url", unchanged: boolean) => {
    const source = props.isActive && unchanged ? props.active.provenance?.[field] : null;
    return source ? labels.provenance(source) : null;
  };

  return (
    <div className="mt-3 space-y-3 border-t border-[var(--color-border-default)] pt-3">
      <ModelSection
        id={`${formId}-model`}
        provider={provider}
        form={form}
        connection={connection}
        source={sourceOf("model", form.model.trim() === form.savedModel)}
        labels={labels}
      />
      {provider.accepts_base_url && (
        <BaseUrlSection
          id={`${formId}-url`}
          provider={provider}
          form={form}
          connection={connection}
          unconfirmed={props.isActive && props.active.base_url_unconfirmed === true}
          source={sourceOf("base_url", form.url === form.savedUrl)}
          labels={labels}
        />
      )}
      <SaveBar provider={provider} isActive={props.isActive} form={form} onCancel={props.onDone} labels={labels} />
    </div>
  );
}

interface SectionProps {
  id: string;
  provider: ProviderOption;
  form: FormState;
  connection: ConnectionState;
  /** Where the saved value came from, worded; null to show nothing. */
  source: string | null;
  labels: ProviderSettingsLabels;
}

function ModelSection({ id, provider, form, connection, source, labels }: SectionProps) {
  const { current } = connection;
  return (
    <div className="space-y-1.5">
      <FieldLabel htmlFor={id}>{labels.model}</FieldLabel>
      <ModelCombobox
        id={id}
        ariaLabel={`${provider.name} ${labels.model}`}
        value={form.model}
        onSelect={form.setModel}
        models={current?.models ?? provider.models.map((m) => ({ id: m }))}
        defaultModel={current?.default_model ?? provider.default_model}
        loading={connection.loading}
        onOpen={connection.open}
        className="sm:max-w-sm"
      />
      {source && <Muted>{source}</Muted>}
      {current?.error && !connection.tested && <Muted>{current.error}</Muted>}
    </div>
  );
}

function BaseUrlSection({
  id,
  provider,
  form,
  connection,
  unconfirmed,
  source,
  labels,
}: SectionProps & { unconfirmed: boolean }) {
  const edit = (value: string) => {
    form.setBaseUrl(value);
    connection.resetTest();
  };
  return (
    <div className="space-y-1.5">
      <FieldLabel htmlFor={id}>{labels.baseUrl}</FieldLabel>
      <div className="flex flex-wrap items-center gap-2">
        <Input
          id={id}
          type="url"
          inputMode="url"
          autoComplete="off"
          spellCheck={false}
          value={form.baseUrl}
          onChange={(e) => edit(e.target.value)}
          placeholder="https://"
          aria-invalid={!form.urlValid || undefined}
          aria-describedby={`${id}-hint`}
          className="h-8 font-mono text-xs sm:max-w-sm"
        />
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-8 gap-1.5 text-xs"
          disabled={!connection.canTest}
          onClick={connection.test}
        >
          {connection.loading && connection.tested ? (
            <Spinner size="sm" />
          ) : (
            <Plug className="h-3.5 w-3.5" />
          )}
          {labels.testConnection}
        </Button>
      </div>
      {provider.local_server && <LocalServerPresets onPick={edit} labels={labels} />}
      {unconfirmed && form.url === form.savedUrl && (
        <UnconfirmedUrl url={form.savedUrl} form={form} labels={labels} />
      )}
      <Muted id={`${id}-hint`}>
        {form.urlValid ? (source ?? labels.baseUrlHint) : labels.baseUrlInvalid}
      </Muted>
      <ConnectionResult connection={connection} labels={labels} />
    </div>
  );
}

function LocalServerPresets({
  onPick,
  labels,
}: {
  onPick: (url: string) => void;
  labels: ProviderSettingsLabels;
}) {
  return (
    <p className="flex flex-wrap items-center gap-x-2 text-xs text-[var(--color-text-tertiary)]">
      <span>{labels.localServer}:</span>
      {LOCAL_SERVER_PRESETS.map((preset) => (
        <button
          key={preset.url}
          type="button"
          className="text-[var(--color-accent-primary)] hover:underline"
          onClick={() => onPick(preset.url)}
        >
          {preset.name}
        </button>
      ))}
    </p>
  );
}

function UnconfirmedUrl({
  url,
  form,
  labels,
}: {
  url: string;
  form: FormState;
  labels: ProviderSettingsLabels;
}) {
  return (
    <div role="alert" className="space-y-1.5">
      <p className="text-xs text-[var(--color-error)]">{labels.unconfirmedUrl(url)}</p>
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="h-7 text-xs"
        disabled={form.saving}
        onClick={() => void form.save(true)}
      >
        {labels.trustUrl}
      </Button>
    </div>
  );
}

function ConnectionResult({
  connection,
  labels,
}: {
  connection: ConnectionState;
  labels: ProviderSettingsLabels;
}) {
  const { current } = connection;
  if (!connection.tested || !current || connection.loading) return null;
  if (current.source === "live") {
    return (
      <p role="status" className="inline-flex items-center gap-1 text-xs text-[var(--color-success)]">
        <Check className="h-3.5 w-3.5" />
        {labels.connected(current.models.length)}
      </p>
    );
  }
  return (
    <p role="alert" className="text-xs text-[var(--color-error)]">
      {current.error}
    </p>
  );
}

function SaveBar({
  provider,
  isActive,
  form,
  onCancel,
  labels,
}: {
  provider: ProviderOption;
  isActive: boolean;
  form: FormState;
  onCancel: () => void;
  labels: ProviderSettingsLabels;
}) {
  const idleLabel = isActive ? labels.save : labels.useProvider(provider.name);
  return (
    <>
      {form.dirty && form.modelChanged && <Muted>{labels.regenerateNotice}</Muted>}
      {form.saveFailed && (
        <p role="alert" className="text-xs text-[var(--color-error)]">
          {labels.saveFailed}
        </p>
      )}
      <div className="flex items-center gap-2">
        <Button
          type="button"
          size="sm"
          className="h-8 text-xs"
          disabled={!form.dirty || !form.urlValid || form.saving}
          onClick={() => void form.save()}
        >
          {form.saving ? <Spinner size="sm" /> : idleLabel}
        </Button>
        {!isActive && (
          <Button type="button" variant="ghost" size="sm" className="h-8 text-xs" onClick={onCancel}>
            {labels.cancel}
          </Button>
        )}
      </div>
    </>
  );
}

function FieldLabel({ htmlFor, children }: { htmlFor: string; children: ReactNode }) {
  return (
    <Label htmlFor={htmlFor} className="text-xs text-[var(--color-text-secondary)]">
      {children}
    </Label>
  );
}

function Muted({ id, children }: { id?: string; children: ReactNode }) {
  return (
    <p id={id} className="text-xs text-[var(--color-text-tertiary)]">
      {children}
    </p>
  );
}
