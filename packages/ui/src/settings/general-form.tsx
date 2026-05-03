"use client";

import { useState } from "react";
import { Info, Save, X } from "lucide-react";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Label } from "../ui/label";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "../ui/tooltip";
import type { RepoSettingsValue } from "@repowise-dev/types/settings";

const SUGGESTIONS = [
  "vendor/",
  "dist/",
  "build/",
  "node_modules/",
  "*.generated.*",
  "**/fixtures/**",
];

function arraysEqual(a: string[], b: string[]) {
  return a.length === b.length && a.every((v, i) => v === b[i]);
}

export interface GeneralFormProps {
  /** Initial form value. Also used as the "saved" baseline for dirty-check. */
  value: RepoSettingsValue;
  /**
   * Optional save handler. When omitted, the form renders read-only — useful
   * for hosted, where multi-tenant settings persistence is deferred to the
   * Phase 5 RBAC data-model rewrite.
   */
  onSubmit?: (next: RepoSettingsValue) => Promise<void>;
  /** Read-only fields shown alongside the editable form. */
  localPath?: string;
  remoteUrl?: string;
  /**
   * Copy displayed underneath the disabled save button when `onSubmit` is
   * absent. Defaults to a generic "managed elsewhere" line.
   */
  disabledHint?: string;
}

export function GeneralForm({
  value,
  onSubmit,
  localPath,
  remoteUrl,
  disabledHint = "Editing repository settings is not yet available on this surface.",
}: GeneralFormProps) {
  const [name, setName] = useState(value.name);
  const [branch, setBranch] = useState(value.default_branch);
  const [patterns, setPatterns] = useState<string[]>(value.exclude_patterns);
  const [newPattern, setNewPattern] = useState("");
  const [saving, setSaving] = useState(false);

  const readOnly = onSubmit === undefined;
  const hasChanges =
    name !== value.name ||
    branch !== value.default_branch ||
    !arraysEqual(patterns, value.exclude_patterns);

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
    if (!onSubmit || !hasChanges) return;
    setSaving(true);
    try {
      await onSubmit({
        name,
        default_branch: branch,
        exclude_patterns: patterns,
      });
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
          disabled={readOnly}
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
          disabled={readOnly}
        />
      </div>

      {localPath && (
        <div className="space-y-1.5">
          <Label>Local path</Label>
          <p className="text-sm font-mono text-[var(--color-text-secondary)] break-all">
            {localPath}
          </p>
          <p className="text-xs text-[var(--color-text-tertiary)]">
            Path cannot be changed after repository is registered.
          </p>
        </div>
      )}

      {remoteUrl && (
        <div className="space-y-1.5">
          <Label>Remote URL</Label>
          <p className="text-sm font-mono text-[var(--color-text-secondary)] break-all">
            {remoteUrl}
          </p>
        </div>
      )}

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
                {!readOnly && (
                  <button
                    type="button"
                    onClick={() => removePattern(p)}
                    className="text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] transition-colors"
                    aria-label={`Remove ${p}`}
                  >
                    <X className="h-3 w-3" />
                  </button>
                )}
              </span>
            ))}
          </div>
        )}

        {!readOnly && (
          <>
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
          </>
        )}
      </div>

      <div className="space-y-1.5">
        <Button
          onClick={handleSave}
          disabled={readOnly || !hasChanges || saving}
          size="sm"
          className="gap-2"
        >
          <Save className="h-3.5 w-3.5" />
          {saving ? "Saving…" : "Save changes"}
        </Button>
        {readOnly && (
          <p className="text-xs text-[var(--color-text-tertiary)]">{disabledHint}</p>
        )}
      </div>
    </div>
  );
}
