"use client";

import { useEffect } from "react";
import { config } from "@/lib/config";
import { Label } from "@repowise/ui/ui/label";
import { Input } from "@repowise/ui/ui/input";
import { Switch } from "@repowise/ui/ui/switch";
import { Slider } from "@repowise/ui/ui/slider";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@repowise/ui/ui/select";

export interface RunConfig {
  provider: string;
  model: string;
  skipTests: boolean;
  skipInfra: boolean;
  concurrency: number;
}

interface Props {
  value: RunConfig;
  onChange: (v: RunConfig) => void;
}

const PROVIDERS = ["litellm", "openai", "anthropic", "ollama", "mock"] as const;

export function RunConfigForm({ value, onChange }: Props) {
  // Seed from saved settings on mount
  useEffect(() => {
    onChange({
      provider: config.getProvider(),
      model: config.getModel(),
      skipTests: false,
      skipInfra: false,
      concurrency: 4,
    });
    // only on mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function set<K extends keyof RunConfig>(key: K, v: RunConfig[K]) {
    onChange({ ...value, [key]: v });
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="run-provider">Provider</Label>
          <Select value={value.provider} onValueChange={(v) => set("provider", v)}>
            <SelectTrigger id="run-provider" aria-label="Provider">
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
          <Label htmlFor="run-model">Model</Label>
          <Input
            id="run-model"
            value={value.model}
            onChange={(e) => set("model", e.target.value)}
            placeholder="gemini/gemini-2.0-flash"
            className="font-mono"
          />
        </div>
      </div>

      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm text-[var(--color-text-primary)]">Skip test files</p>
            <p className="text-xs text-[var(--color-text-tertiary)]">
              Exclude test files from documentation
            </p>
          </div>
          <Switch
            checked={value.skipTests}
            onCheckedChange={(v) => set("skipTests", v)}
          />
        </div>

        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm text-[var(--color-text-primary)]">Skip infra files</p>
            <p className="text-xs text-[var(--color-text-tertiary)]">
              Exclude CI, Docker, config files
            </p>
          </div>
          <Switch
            checked={value.skipInfra}
            onCheckedChange={(v) => set("skipInfra", v)}
          />
        </div>
      </div>

      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <Label htmlFor="run-concurrency">Concurrency</Label>
          <span className="text-sm tabular-nums text-[var(--color-text-secondary)]">
            {value.concurrency}
          </span>
        </div>
        <Slider
          id="run-concurrency"
          aria-label="Concurrency"
          aria-valuetext={`${value.concurrency} parallel`}
          min={1}
          max={10}
          step={1}
          value={[value.concurrency]}
          onValueChange={([v]) => set("concurrency", v)}
        />
        <p className="text-xs text-[var(--color-text-tertiary)]">
          Parallel page generations (higher = faster but more API usage)
        </p>
      </div>
    </div>
  );
}
