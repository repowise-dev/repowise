"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@repowise-dev/ui/ui/card";
import { Badge } from "@repowise-dev/ui/ui/badge";
import { Button } from "@repowise-dev/ui/ui/button";
import { Switch } from "@repowise-dev/ui/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@repowise-dev/ui/ui/select";
import { listRepos } from "@/lib/api/repos";
import { getMcpToolSurface, updateMcpTools } from "@/lib/api/mcp-tools";
import type { McpToolSurface } from "@/lib/api/types";

interface RepoOption {
  id: string;
  name: string;
}

function enabledNames(surface: McpToolSurface): Set<string> {
  return new Set(surface.tools.filter((t) => t.enabled).map((t) => t.name));
}

export function McpToolsSection() {
  const [repos, setRepos] = useState<RepoOption[]>([]);
  const [repoId, setRepoId] = useState<string | null>(null);
  const [surface, setSurface] = useState<McpToolSurface | null>(null);
  const [enabled, setEnabled] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  // Load the repo list once and pick a default (the primary, else the first).
  useEffect(() => {
    listRepos()
      .then((rows) => {
        const opts = rows
          .filter((r) => !r.id.startsWith("ws:"))
          .map((r) => ({ id: r.id, name: r.name }));
        setRepos(opts);
        setRepoId((cur) => cur ?? opts[0]?.id ?? null);
        if (opts.length === 0) setLoading(false);
      })
      .catch((e) => {
        setError(String(e));
        setLoading(false);
      });
  }, []);

  const loadSurface = useCallback((id: string) => {
    setLoading(true);
    setError(null);
    getMcpToolSurface(id)
      .then((s) => {
        setSurface(s);
        setEnabled(enabledNames(s));
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (repoId) loadSurface(repoId);
  }, [repoId, loadSurface]);

  function toggle(name: string, on: boolean) {
    setSaved(false);
    setEnabled((prev) => {
      const next = new Set(prev);
      if (on) next.add(name);
      else next.delete(name);
      return next;
    });
  }

  const dirty = useMemo(() => {
    if (!surface) return false;
    const server = enabledNames(surface);
    if (server.size !== enabled.size) return true;
    for (const n of enabled) if (!server.has(n)) return true;
    return false;
  }, [surface, enabled]);

  async function save() {
    if (!surface || !repoId) return;
    setSaving(true);
    setError(null);
    // Store the selection as +/- deltas off the default surface so it stays
    // correct if the default set changes in a future release.
    const defaults = new Set(
      surface.tools.filter((t) => t.default).map((t) => t.name),
    );
    const added = [...enabled].filter((n) => !defaults.has(n)).sort();
    const removed = [...defaults].filter((n) => !enabled.has(n)).sort();
    const tools = [...added.map((n) => `+${n}`), ...removed.map((n) => `-${n}`)];
    try {
      const updated = await updateMcpTools({
        repo_id: repoId,
        tools: tools.length ? tools : null,
      });
      setSurface(updated);
      setEnabled(enabledNames(updated));
      setSaved(true);
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">MCP tool surface</CardTitle>
        <CardDescription>
          Choose which tools the MCP server exposes for a repo. Changes are saved
          to its <code className="font-mono">.repowise/config.yaml</code> and take
          effect the next time you start <code className="font-mono">repowise mcp</code>{" "}
          for that repo.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {repos.length === 0 && !loading && (
          <p className="text-sm text-[var(--color-text-tertiary)]">
            No indexed repos found. Run <code className="font-mono">repowise init</code>{" "}
            first.
          </p>
        )}

        {repos.length > 1 && (
          <div className="flex items-center gap-2">
            <span className="text-sm text-[var(--color-text-secondary)]">Repo</span>
            <Select value={repoId ?? undefined} onValueChange={setRepoId}>
              <SelectTrigger className="w-56">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {repos.map((r) => (
                  <SelectItem key={r.id} value={r.id}>
                    {r.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}

        {surface && !loading && (
          <>
            <p className="text-xs text-[var(--color-text-tertiary)]">
              {surface.is_workspace
                ? "Workspace mode — workspace-only tools are available."
                : "Single-repo mode — workspace-only tools are unavailable here."}
            </p>

            <div className="divide-y divide-[var(--color-border-default)] rounded border border-[var(--color-border-default)]">
              {surface.tools.map((tool) => {
                const locked = tool.requires_workspace && !surface.is_workspace;
                return (
                  <div
                    key={tool.name}
                    className="flex items-start gap-3 px-3 py-2.5"
                  >
                    <Switch
                      checked={enabled.has(tool.name)}
                      disabled={locked || saving}
                      onCheckedChange={(v) => toggle(tool.name, v)}
                      className="mt-0.5"
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <code className="font-mono text-sm">{tool.name}</code>
                        {tool.requires_workspace && (
                          <Badge variant="outline" className="text-[10px]">
                            workspace
                          </Badge>
                        )}
                        {!tool.default && !tool.requires_workspace && (
                          <Badge variant="outline" className="text-[10px]">
                            opt-in
                          </Badge>
                        )}
                      </div>
                      {tool.description && (
                        <p className="text-xs text-[var(--color-text-tertiary)] mt-0.5">
                          {tool.description}
                        </p>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>

            <div className="flex items-center gap-3">
              <Button onClick={save} disabled={!dirty || saving} size="sm">
                {saving ? "Saving…" : "Save"}
              </Button>
              {saved && !dirty && (
                <span className="text-sm text-[var(--color-fresh)]">Saved</span>
              )}
              {dirty && (
                <span className="text-xs text-[var(--color-text-tertiary)]">
                  Unsaved changes
                </span>
              )}
            </div>
          </>
        )}

        {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
      </CardContent>
    </Card>
  );
}
