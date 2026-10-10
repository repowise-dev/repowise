"use client";

import React, { useEffect, useRef, useState } from "react";
import { config } from "@/lib/config";
import { getProviders } from "@/lib/api/providers";
import type { EmbedderInfo, ProviderInfo } from "@/lib/api/types";
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
  // "" until a saved choice or the server's active one is known.
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [embedder, setEmbedder] = useState("");
  const [serverProvider, setServerProvider] = useState<string | null>(null);
  // The server owns the provider and embedder lists (derived from the core
  // registries), so this page keeps no copy; until they load, only the saved
  // choices are offered.
  const [catalog, setCatalog] = useState<ProviderInfo[]>([]);
  const [flagOnly, setFlagOnly] = useState<string[]>([]);
  const [embedders, setEmbedders] = useState<EmbedderInfo[]>([]);
  const [catalogFailed, setCatalogFailed] = useState(false);
  const [saveState, setSaveState] = useState<SaveState>("idle");

  const savedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    setProvider(config.getProvider());
    setModel(config.getModel());
    setEmbedder(config.getEmbedder());
    let cancelled = false;
    void getProviders()
      .then(({ active, providers: catalog, flag_only_providers, embedders: embedderList }) => {
        if (cancelled) return;
        setServerProvider(active.provider);
        setCatalog((catalog ?? []).filter((entry) => entry.id));
        setFlagOnly(flag_only_providers ?? []);
        setEmbedders(embedderList ?? []);
        // Nothing saved: show what the server itself runs with.
        setProvider((saved) => saved || active.provider || "");
        setEmbedder((saved) => saved || active.embedder || "");
      })
      .catch((error: unknown) => {
        console.warn("[settings] Could not load the active server provider", error);
        if (!cancelled) setCatalogFailed(true);
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
    ...new Set([...catalog.map((entry) => entry.id), ...flagOnly, provider]),
  ].filter(Boolean);
  const embedderOptions = [
    ...new Set([...embedders.map((entry) => entry.id), embedder]),
  ].filter(Boolean);

  const providerInfo = catalog.find((entry) => entry.id === provider);
  const embedderInfo = embedders.find((entry) => entry.id === embedder);

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
            {catalogFailed && (
              <p className="text-xs text-[var(--color-text-tertiary)]">
                {t("provider.catalogUnavailable")}
              </p>
            )}
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
                {embedderOptions.map((e) => (
                  <SelectItem key={e} value={e}>
                    {e}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {embedderInfo?.semantic === false ? (
              <EnvVarLine
                vars={[]}
                note={t.rich("provider.embedderOffNote", {
                  code: (chunks) => <code className="font-mono text-[var(--color-text-secondary)]">{chunks}</code>,
                })}
              />
            ) : embedderInfo ? (
              <EnvVarLine
                vars={embedderInfo.env_vars ?? []}
                note={t.rich("provider.embedderOnNote", {
                  code: (chunks) => <code className="font-mono text-[var(--color-text-secondary)]">{chunks}</code>,
                  env: `REPOWISE_EMBEDDER=${embedder}`,
                })}
              />
            ) : null}
          </div>
        </SettingsRow>
      </SettingsRows>
    </OverviewSection>
  );
}
