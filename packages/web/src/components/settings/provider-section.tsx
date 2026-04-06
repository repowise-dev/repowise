"use client";

import { useState, useEffect } from "react";
import { config } from "@/lib/config";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const PROVIDERS = ["gemini", "openai", "anthropic", "ollama", "litellm", "mock"] as const;
const EMBEDDERS = ["mock", "gemini", "openai"] as const;

const MODEL_PLACEHOLDERS: Record<string, string> = {
  gemini: "gemini-3.1-flash-lite-preview",
  openai: "gpt-4.1",
  anthropic: "claude-sonnet-4-6",
  ollama: "llama3.2",
  litellm: "groq/llama-3.1-70b-versatile",
  mock: "mock",
};

const PROVIDER_ENV_VARS: Record<string, { vars: string[]; installHint: string }> = {
  gemini: { vars: ["GEMINI_API_KEY"], installHint: "pip install google-genai" },
  openai: { vars: ["OPENAI_API_KEY"], installHint: "pip install openai" },
  anthropic: { vars: ["ANTHROPIC_API_KEY"], installHint: "pip install anthropic" },
  ollama: { vars: ["OLLAMA_BASE_URL"], installHint: "https://ollama.ai" },
  litellm: { vars: ["LITELLM_*"], installHint: "pip install litellm" },
  mock: { vars: [], installHint: "No key needed" },
};

const EMBEDDER_ENV_VARS: Record<string, string[]> = {
  gemini: ["GEMINI_API_KEY"],
  openai: ["OPENAI_API_KEY"],
  mock: [],
};

type TestStatus = "idle" | "testing" | "ok" | "error";

export function ProviderSection() {
  const [provider, setProvider] = useState("gemini");
  const [model, setModel] = useState("");
  const [embedder, setEmbedder] = useState("mock");
  const [testStatus, setTestStatus] = useState<TestStatus>("idle");
  const [testError, setTestError] = useState("");
  const [serverProvider, setServerProvider] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  useEffect(() => {
    setProvider(config.getProvider());
    setModel(config.getModel());
    setEmbedder(config.getEmbedder());
    // Fetch current server config from /api/health
    fetch("/api/health")
      .then((r) => r.json())
      .then((data) => {
        if (data?.provider) setServerProvider(data.provider);
      })
      .catch(() => {});
  }, []);

  function handleProviderChange(v: string) {
    setProvider(v);
    config.setProvider(v);
    setTestStatus("idle");
  }

  function handleEmbedderChange(v: string) {
    setEmbedder(v);
    config.setEmbedder(v);
  }

  async function handleTestConnection() {
    setTestStatus("testing");
    setTestError("");
    try {
      const res = await fetch("/api/health");
      const data = await res.json();
      if (res.ok && data.status === "healthy") {
        setTestStatus("ok");
      } else {
        setTestStatus("error");
        setTestError(data.status ?? "Server returned non-healthy status");
      }
    } catch (err) {
      setTestStatus("error");
      setTestError(String(err));
    }
  }

  const providerInfo = PROVIDER_ENV_VARS[provider];
  const embedderVars = EMBEDDER_ENV_VARS[embedder] ?? [];

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Provider &amp; Model</CardTitle>
          <CardDescription>
            Defaults used when triggering init or sync from the UI.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label>Provider</Label>
              <Select value={provider} onValueChange={handleProviderChange}>
                <SelectTrigger>
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
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="model">Model</Label>
              <Input
                id="model"
                placeholder={MODEL_PLACEHOLDERS[provider] ?? "model name"}
                value={model}
                onChange={(e) => setModel(e.target.value)}
                onBlur={() => config.setModel(model)}
                className="font-mono"
              />
            </div>
          </div>

          {providerInfo && providerInfo.vars.length > 0 && (
            <div className="rounded-md border border-dashed p-3 space-y-1.5">
              <p className="text-xs font-medium text-[var(--color-text-secondary)]">
                Required env vars for {provider}:
              </p>
              <div className="flex flex-wrap gap-1.5">
                {providerInfo.vars.map((v) => (
                  <code
                    key={v}
                    className="text-xs bg-[var(--color-bg-secondary)] px-1.5 py-0.5 rounded font-mono"
                  >
                    {v}
                  </code>
                ))}
              </div>
              <p className="text-xs text-[var(--color-text-tertiary)]">{providerInfo.installHint}</p>
            </div>
          )}

          <div className="space-y-1.5">
            <Label>Embedder</Label>
            <Select value={embedder} onValueChange={handleEmbedderChange}>
              <SelectTrigger className="w-48">
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
            {embedderVars.length > 0 && (
              <p className="text-xs text-[var(--color-text-tertiary)]">
                Requires{" "}
                {embedderVars.map((v) => (
                  <code key={v} className="font-mono">{v}</code>
                ))}{" "}
                — set <code className="font-mono">REPOWISE_EMBEDDER={embedder}</code> on the server.
              </p>
            )}
            {embedder === "mock" && (
              <p className="text-xs text-[var(--color-text-tertiary)]">
                Using mock embedder — semantic search disabled. Set{" "}
                <code className="font-mono">REPOWISE_EMBEDDER=gemini</code> or{" "}
                <code className="font-mono">REPOWISE_EMBEDDER=openai</code> for real RAG.
              </p>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Server Connection</CardTitle>
          <CardDescription>
            Test that the repowise server is reachable and healthy.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {serverProvider && (
            <p className="text-sm text-[var(--color-text-secondary)]">
              Server configured with provider:{" "}
              <Badge variant="outline" className="font-mono text-xs">{serverProvider}</Badge>
            </p>
          )}
          <div className="flex items-center gap-3">
            <button
              onClick={handleTestConnection}
              disabled={testStatus === "testing"}
              className="text-sm px-3 py-1.5 rounded-md border border-[var(--color-border)] hover:bg-[var(--color-bg-secondary)] disabled:opacity-50 transition-colors"
            >
              {testStatus === "testing" ? "Testing…" : "Test connection"}
            </button>
            {testStatus === "ok" && (
              <span className="text-sm text-green-600 dark:text-green-400">✓ Server healthy</span>
            )}
            {testStatus === "error" && (
              <span className="text-sm text-red-600 dark:text-red-400">
                ✗ {testError || "Connection failed"}
              </span>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
