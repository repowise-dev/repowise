"use client";

import { useEffect, useRef, useState } from "react";
import { config } from "@/lib/config";
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

const PROVIDERS = ["gemini", "openai", "anthropic", "deepseek", "kimi", "edenai", "claude_cli", "opencode", "ollama", "litellm", "mock"] as const;
const EMBEDDERS = ["mock", "gemini", "openai", "openrouter", "edenai", "ollama"] as const;

const MODEL_PLACEHOLDERS: Record<string, string> = {
  gemini: "gemini-3.5-flash-lite",
  openai: "gpt-5.6-luna",
  anthropic: "claude-haiku-4-5",
  deepseek: "deepseek-v4-flash",
  kimi: "kimi-for-coding",
  edenai: "mistral/mistral-small-latest",
  claude_cli: "claude_cli/claude-haiku-4-5",
  opencode: "opencode/default",
  ollama: "qwen3.5:4b",
  litellm: "groq/llama-3.1-70b-versatile",
  mock: "mock",
};

const PROVIDER_ENV_VARS: Record<string, { vars: string[]; installHint: string }> = {
  gemini: { vars: ["GEMINI_API_KEY"], installHint: "pip install google-genai" },
  openai: { vars: ["OPENAI_API_KEY"], installHint: "pip install openai" },
  anthropic: { vars: ["ANTHROPIC_API_KEY"], installHint: "pip install anthropic" },
  ollama: { vars: ["OLLAMA_BASE_URL"], installHint: "https://ollama.ai" },
  deepseek: { vars: ["DEEPSEEK_API_KEY"], installHint: "pip install openai" },
  kimi: { vars: ["KIMI_API_KEY"], installHint: "pip install openai" },
  edenai: { vars: ["EDENAI_API_KEY"], installHint: "pip install openai" },
  litellm: { vars: ["LITELLM_*"], installHint: "pip install litellm" },
  claude_cli: { vars: [], installHint: "https://claude.com/claude-code, then: claude login" },
  opencode: { vars: [], installHint: "curl -fsSL https://opencode.ai/install | bash" },
  mock: { vars: [], installHint: "No key needed" },
};

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
 * button against the same `/api/health` that `ConnectionSection` already tests
 * — a hand-rolled `<button>` painted with `--color-border`, a token defined in
 * no stylesheet, reporting with literal ✓/✗ glyphs. It is gone; what it
 * uniquely showed, the provider the *server* is configured with, is a line
 * here where it belongs.
 */
export function ProviderSection() {
  const [provider, setProvider] = useState("gemini");
  const [model, setModel] = useState("");
  const [embedder, setEmbedder] = useState("mock");
  const [serverProvider, setServerProvider] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<SaveState>("idle");

  const savedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    setProvider(config.getProvider());
    setModel(config.getModel());
    setEmbedder(config.getEmbedder());
    fetch("/api/health")
      .then((r) => r.json())
      .then((data) => {
        if (data?.provider) setServerProvider(data.provider);
      })
      .catch(() => {});
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

  const providerInfo = PROVIDER_ENV_VARS[provider];
  const embedderVars = EMBEDDER_ENV_VARS[embedder] ?? [];

  return (
    <OverviewSection
      title="Model defaults"
      description={
        serverProvider
          ? `Used when you trigger init or sync from this dashboard. The server itself is currently configured with ${serverProvider}.`
          : "Used when you trigger init or sync from this dashboard."
      }
      action={<SaveIndicator state={saveState} />}
    >
      <SettingsRows>
        <SettingsRow
          label="Provider"
          hint={providerInfo?.installHint}
        >
          <div className="space-y-2">
            <Select value={provider} onValueChange={handleProviderChange}>
              <SelectTrigger className="w-full sm:w-64">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PROVIDERS.map((p) => (
                  <SelectItem key={p} value={p}>
                    {p}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {providerInfo && <EnvVarLine vars={providerInfo.vars} />}
          </div>
        </SettingsRow>

        <SettingsRow
          label="Model"
          htmlFor="model"
          hint="Leave blank to use the provider's default."
        >
          <Input
            id="model"
            placeholder={MODEL_PLACEHOLDERS[provider] ?? "model name"}
            value={model}
            onChange={(e) => setModel(e.target.value)}
            onBlur={handleModelBlur}
            className="font-mono sm:max-w-md"
          />
        </SettingsRow>

        <SettingsRow
          label="Embedder"
          hint="What semantic search is built from. The mock embedder disables it."
        >
          <div className="space-y-2">
            <Select value={embedder} onValueChange={handleEmbedderChange}>
              <SelectTrigger className="w-full sm:w-64">
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
                note={
                  <>
                    Semantic search is off. Set{" "}
                    <code className="font-mono text-[var(--color-text-secondary)]">
                      REPOWISE_EMBEDDER=gemini
                    </code>{" "}
                    or{" "}
                    <code className="font-mono text-[var(--color-text-secondary)]">
                      REPOWISE_EMBEDDER=openai
                    </code>{" "}
                    on the server for real retrieval.
                  </>
                }
              />
            ) : (
              <EnvVarLine
                vars={embedderVars}
                note={
                  <>
                    Set{" "}
                    <code className="font-mono text-[var(--color-text-secondary)]">
                      REPOWISE_EMBEDDER={embedder}
                    </code>{" "}
                    on the server.
                  </>
                }
              />
            )}
          </div>
        </SettingsRow>
      </SettingsRows>
    </OverviewSection>
  );
}
