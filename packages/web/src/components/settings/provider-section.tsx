"use client";

import React, { useEffect, useRef, useState } from "react";
import { config } from "@/lib/config";
import { getProviders } from "@/lib/api/providers";
import type { ProviderInfo } from "@/lib/api/types";
import { OverviewSection } from "@repowise-dev/ui/overview";
import { Input } from "@repowise-dev/ui/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@repowise-dev/ui/ui/select";
import {
  SettingsRow,
  SettingsRows,
  SaveIndicator,
  EnvVarLine,
  type SaveState,
} from "@repowise-dev/ui/settings";
import { useTranslations } from "next-intl";

const EMBEDDERS = ["mock", "gemini", "openai", "openrouter", "edenai", "ollama"] as const;

// Real, registerable providers the server catalog deliberately leaves out.
// `mock` is a keyless test provider (`KEYLESS_PROVIDERS` in the registry) that
// this page has always offered; it is flag-only, so it is absent from
// PROVIDER_CATALOG and would otherwise vanish the moment the catalog loads.
const FLAG_ONLY_PROVIDERS = ["mock"] as const;

const EMBEDDER_ENV_VARS: Record<string, string[]> = {
  gemini: ["GEMINI_API_KEY"],
  openai: ["OPENAI_API_KEY"],
  openrouter: ["OPENROUTER_API_KEY"],
  edenai: ["EDENAI_API_KEY"],
  ollama: ["OLLAMA_BASE_URL"],
  mock: [],
};

/**
 * Model and embedder defaults for init/sync triggered from the UI.
 *
 * This used to render a second card, "Server Connection", with its own Test
 * button against the same `/health` that `ConnectionSection` already tests
 * — a hand-rolled `<button>` painted with `--color-border`, a token defined in
 * no stylesheet, reporting with literal ✓/✗ glyphs. It is gone; what it
 * uniquely showed, the provider the *server* is configured with, is a line
 * here where it belongs.
 */
export function ProviderSection() {
  const t = useTranslations("settings");
  const [provider, setProvider] = useState("gemini");
  const [model, setModel] = useState("");
  const [embedder, setEmbedder] = useState("mock");
  const [serverProvider, setServerProvider] = useState<string | null>(null);
  // The server owns the catalog (it is derived from the provider specs), so
  // this page keeps no copy of its own; until it loads, only the saved
  // provider and the flag-only ones are offered.
  const [catalog, setCatalog] = useState<ProviderInfo[]>([]);
  const [saveState, setSaveState] = useState<SaveState>("idle");

  const savedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    setProvider(config.getProvider());
    setModel(config.getModel());
    setEmbedder(config.getEmbedder());
    let cancelled = false;
    void getProviders()
      .then(({ active, providers: catalog }) => {
        if (cancelled) return;
        setServerProvider(active.provider);
        setCatalog((catalog ?? []).filter((entry) => entry.id));
      })
      .catch((error: unknown) => {
        console.warn("[settings] Could not load the active server provider", error);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(
    () => () => {
      if (savedTimer.current) clearTimeout(savedTimer.current);
    },
    [],
  );

  function flashSaved() {
    setSaveState("saved");
    if (savedTimer.current) clearTimeout(savedTimer.current);
    savedTimer.current = setTimeout(() => setSaveState("idle"), 2000);
  }

  function handleProviderChange(v: string) {
    setProvider(v);
    config.setProvider(v);
    flashSaved();
  }

  function handleEmbedderChange(v: string) {
    setEmbedder(v);
    config.setEmbedder(v);
    flashSaved();
  }

  function handleModelBlur() {
    config.setModel(model);
    flashSaved();
  }

  // The catalog is not a superset of what can be selected, so rendering it
  // verbatim silently takes options away: the flag-only providers never
  // appear in it, and a saved provider the server has since stopped
  // advertising would leave a blank trigger with nothing to recover with.
  const providerOptions = [
    ...new Set([...catalog.map((entry) => entry.id), ...FLAG_ONLY_PROVIDERS, provider]),
  ];

  const providerInfo = catalog.find((entry) => entry.id === provider);
  const embedderVars = EMBEDDER_ENV_VARS[embedder] ?? [];

  return (
    <OverviewSection
      title={t("provider.title")}
      description={
        serverProvider
          ? t("provider.descriptionWithServer", { provider: serverProvider })
          : t("provider.description")
      }
      action={<SaveIndicator state={saveState} />}
    >
      <SettingsRows>
        <SettingsRow
          label={t("provider.providerLabel")}
          hint={providerInfo?.setup_hint || undefined}
        >
          <div className="space-y-2">
            <Select value={provider} onValueChange={handleProviderChange}>
              <SelectTrigger
                aria-label={t("provider.providerAria")}
                className="w-full sm:w-64"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {providerOptions.map((p) => (
                  <SelectItem key={p} value={p}>
                    {p}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {providerInfo && <EnvVarLine vars={providerInfo.env_vars ?? []} />}
          </div>
        </SettingsRow>

        <SettingsRow
          label={t("provider.modelLabel")}
          htmlFor="model"
          hint={t("provider.modelHint")}
        >
          <Input
            id="model"
            placeholder={providerInfo?.default_model || "model name"}
            value={model}
            onChange={(e) => setModel(e.target.value)}
            onBlur={handleModelBlur}
            className="font-mono sm:max-w-md"
          />
        </SettingsRow>

        <SettingsRow
          label={t("provider.embedderLabel")}
          hint={t("provider.embedderHint")}
        >
          <div className="space-y-2">
            <Select value={embedder} onValueChange={handleEmbedderChange}>
              <SelectTrigger
                aria-label={t("provider.embedderAria")}
                className="w-full sm:w-64"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {EMBEDDERS.map((e) => (
                  <SelectItem key={e} value={e}>
                    {e}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {embedder === "mock" ? (
              <EnvVarLine
                vars={[]}
                note={t.rich("provider.embedderOffNote", {
                  code: (chunks) => <code className="font-mono text-[var(--color-text-secondary)]">{chunks}</code>,
                })}
              />
            ) : (
              <EnvVarLine
                vars={embedderVars}
                note={t.rich("provider.embedderOnNote", {
                  code: (chunks) => <code className="font-mono text-[var(--color-text-secondary)]">{chunks}</code>,
                  env: `REPOWISE_EMBEDDER=${embedder}`,
                })}
              />
            )}
          </div>
        </SettingsRow>
      </SettingsRows>
    </OverviewSection>
  );
}
