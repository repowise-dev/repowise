"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Info, Save, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { updateRepo } from "@/lib/api/repos";
import type { RepoResponse } from "@/lib/api/types";

const SUGGESTIONS = [
  "vendor/",
  "dist/",
  "build/",
  "node_modules/",
  "*.generated.*",
  "**/fixtures/**",
];

function arraysEqual(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((v, i) => v === b[i]);
}

interface RepoSettingsFormProps {
  repo: RepoResponse;
}

export function RepoSettingsForm({ repo }: RepoSettingsFormProps) {
  const [name, setName] = useState(repo.name);
  const [branch, setBranch] = useState(repo.default_branch);
  const [patterns, setPatterns] = useState<string[]>(
    (repo.settings?.exclude_patterns as string[] | undefined) ?? []
  );
  const [newPattern, setNewPattern] = useState("");
  const [saving, setSaving] = useState(false);

  const savedPatterns = (repo.settings?.exclude_patterns as string[] | undefined) ?? [];
  const hasChanges =
    name !== repo.name ||
    branch !== repo.default_branch ||
    !arraysEqual(patterns, savedPatterns);

  function addPattern(pattern: string) {
    const trimmed = pattern.trim();
    if (!trimmed || patterns.includes(trimmed)) return;
    setPatterns([...patterns, trimmed]);
    setNewPattern("");
  }

  function removePattern(pattern: string) {
    setPatterns(patterns.filter((p) => p !== pattern));
  }

  async function handleSave() {
    if (!hasChanges) return;
    setSaving(true);
    try {
      await updateRepo(repo.id, {
        name,
        default_branch: branch,
        settings: { ...repo.settings, exclude_patterns: patterns },
      });
      toast.success("Repository settings saved");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save settings");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-5">
      <div className="space-y-1.5">
        <Label htmlFor="repo-name">Repository name</Label>
        <Input
          id="repo-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="my-project"
          className="max-w-sm"
        />
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="repo-branch">Default branch</Label>
        <Input
          id="repo-branch"
          value={branch}
          onChange={(e) => setBranch(e.target.value)}
          placeholder="main"
          className="max-w-sm"
        />
      </div>

      <div className="space-y-1.5">
        <Label>Local path</Label>
        <p className="text-sm font-mono text-[var(--color-text-secondary)] break-all">
          {repo.local_path}
        </p>
        <p className="text-xs text-[var(--color-text-tertiary)]">
          Path cannot be changed after repository is registered.
        </p>
      </div>

      {repo.url && (
        <div className="space-y-1.5">
          <Label>Remote URL</Label>
          <p className="text-sm font-mono text-[var(--color-text-secondary)] break-all">
            {repo.url}
          </p>
        </div>
      )}

      {/* Excluded Paths */}
      <div className="space-y-3 border-t border-[var(--color-border-default)] pt-5">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">Excluded Paths</span>
          {patterns.length > 0 && (
            <Badge variant="accent" className="text-xs">
              {patterns.length} active
            </Badge>
          )}
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <Info className="h-3.5 w-3.5 text-[var(--color-text-tertiary)] cursor-help" />
              </TooltipTrigger>
              <TooltipContent>
                <p>Supports full .gitignore syntax. Paths are relative to the repo root.</p>
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        </div>
        <p className="text-xs text-[var(--color-text-secondary)]">
          Gitignore-style patterns. Excluded folders are skipped during indexing and generation.
        </p>

        {/* Chips */}
        {patterns.length === 0 ? (
          <p className="text-xs text-[var(--color-text-tertiary)] italic">
            No custom patterns — .gitignore and .repowiseIgnore are always respected.
          </p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {patterns.map((p) => (
              <span
                key={p}
                className="inline-flex items-center gap-1 rounded-full bg-[var(--color-bg-inset)] px-2.5 py-0.5 text-xs font-mono text-[var(--color-text-primary)] border border-[var(--color-border-default)]"
              >
                {p}
                <button
                  type="button"
                  onClick={() => removePattern(p)}
                  className="text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] transition-colors"
                  aria-label={`Remove ${p}`}
                >
                  <X className="h-3 w-3" />
                </button>
              </span>
            ))}
          </div>
        )}

        {/* Add input */}
        <div className="flex gap-2 max-w-sm">
          <Input
            id="exclude-pattern"
            aria-label="New excluded pattern"
            value={newPattern}
            onChange={(e) => setNewPattern(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                addPattern(newPattern);
              }
            }}
            placeholder="e.g. vendor/, src/generated/**, *.min.js"
            className="font-mono text-xs"
          />
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => addPattern(newPattern)}
            disabled={!newPattern.trim()}
          >
            Add
          </Button>
        </div>

        {/* Quick-add suggestions */}
        <div className="flex flex-wrap gap-1.5">
          {SUGGESTIONS.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              onClick={() => addPattern(suggestion)}
              disabled={patterns.includes(suggestion)}
              className="text-xs rounded-full border border-[var(--color-border-default)] px-2.5 py-0.5 font-mono text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] hover:bg-[var(--color-bg-inset)] transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            >
              + {suggestion}
            </button>
          ))}
        </div>
      </div>

      <Button
        onClick={handleSave}
        disabled={!hasChanges || saving}
        size="sm"
        className="gap-2"
      >
        <Save className="h-3.5 w-3.5" />
        {saving ? "Saving…" : "Save changes"}
      </Button>
    </div>
  );
}
