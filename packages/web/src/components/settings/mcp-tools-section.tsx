"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { OverviewSection } from "@repowise-dev/ui/overview";
import { Switch } from "@repowise-dev/ui/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@repowise-dev/ui/ui/select";
import {
  SaveIndicator,
  SettingsRow,
  SettingsRows,
  type SaveState,
} from "@repowise-dev/ui/settings";
import { listRepos } from "@/lib/api/repos";
import { getMcpToolSurface, updateMcpTools } from "@/lib/api/mcp-tools";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";
import type { McpToolSurface } from "@/lib/api/types";
import { useTranslations } from "next-intl";

interface RepoOption {
  id: string;
  name: string;
}

function enabledNames(surface: McpToolSurface): Set<string> {
  return new Set(surface.tools.filter((t) => t.enabled).map((t) => t.name));
}

/** Selection as +/- deltas off the default set, so it survives a release that
 *  changes which tools are on by default. */
function toDeltas(surface: McpToolSurface, enabled: Set<string>): string[] {
  const defaults = new Set(
    surface.tools.filter((t) => t.default).map((t) => t.name),
  );
  const added = [...enabled].filter((n) => !defaults.has(n)).sort();
  const removed = [...defaults].filter((n) => !enabled.has(n)).sort();
  return [...added.map((n) => `+${n}`), ...removed.map((n) => `-${n}`)];
}

/**
 * Which tools the MCP server exposes.
 *
 * This was the page's only explicit Save button, on a surface where every other
 * control wrote on change. Nothing said which half you were in, so a toggle
 * that silently did nothing until you found the button read as broken. It
 * autosaves now, like everything else, and reports through the one shared
 * indicator.
 */
export function McpToolsSection() {
  const t = useTranslations("settings");
  const [repos, setRepos] = useState<RepoOption[]>([]);
  const [repoId, setRepoId] = useState<string | null>(null);
  const [surface, setSurface] = useState<McpToolSurface | null>(null);
  const [enabled, setEnabled] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [error, setError] = useState<string | null>(null);

  const savedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Guards against an older in-flight save landing after a newer one.
  const saveSeq = useRef(0);

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
        setError(toFriendlyMessage(e, "Could not list repositories"));
        setLoading(false);
      });
  }, []);

  useEffect(
    () => () => {
      if (savedTimer.current) clearTimeout(savedTimer.current);
    },
    [],
  );

  const loadSurface = useCallback((id: string) => {
    setLoading(true);
    setError(null);
    getMcpToolSurface(id)
      .then((s) => {
        setSurface(s);
        setEnabled(enabledNames(s));
      })
      .catch((e) => setError(toFriendlyMessage(e, "Could not load tools")))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (repoId) loadSurface(repoId);
  }, [repoId, loadSurface]);

  async function toggle(name: string, on: boolean) {
    if (!surface || !repoId) return;

    const next = new Set(enabled);
    if (on) next.add(name);
    else next.delete(name);
    setEnabled(next);

    const seq = ++saveSeq.current;
    setSaveState("saving");
    setError(null);
    try {
      const deltas = toDeltas(surface, next);
      const updated = await updateMcpTools({
        repo_id: repoId,
        tools: deltas.length ? deltas : null,
      });
      if (seq !== saveSeq.current) return;
      setSurface(updated);
      setEnabled(enabledNames(updated));
      setSaveState("saved");
      if (savedTimer.current) clearTimeout(savedTimer.current);
      savedTimer.current = setTimeout(() => setSaveState("idle"), 2000);
    } catch (e) {
      if (seq !== saveSeq.current) return;
      // Put the switch back where the server still has it.
      setEnabled(enabledNames(surface));
      setError(toFriendlyMessage(e, "Could not save"));
      setSaveState("error");
    }
  }

  const summary = useMemo(() => {
    if (!surface) return null;
    const available = surface.tools.filter(
      (t) => !(t.requires_workspace && !surface.is_workspace),
    );
    const on = available.filter((t) => enabled.has(t.name)).length;
    const mode = surface.is_workspace
      ? t("mcpTools.summaryWorkspace")
      : t("mcpTools.summarySingleRepo");
    return t("mcpTools.summary", { on, total: available.length, mode });
  }, [surface, enabled, t]);

  return (
    <OverviewSection
      title={t("mcpTools.title")}
      // Rule 4: the figure is the point. "9 of 15 exposed" answers the question
      // the list is there to answer, before you read fifteen rows.
      description={
        summary ?? t("mcpTools.description")
      }
      action={<SaveIndicator state={saveState} error={error} />}
    >
      {repos.length === 0 && !loading && (
        <p className="text-sm text-[var(--color-text-tertiary)]">
          {t("mcpTools.empty")}
        </p>
      )}

      {repos.length > 1 && (
        <SettingsRows>
          <SettingsRow
            label={t("mcpTools.repoLabel")}
            hint={t("mcpTools.repoHint")}
          >
            <Select value={repoId ?? undefined} onValueChange={setRepoId}>
              <SelectTrigger className="w-full sm:w-64">
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
          </SettingsRow>
        </SettingsRows>
      )}

      {surface && !loading && (
        <ul className="border-t border-[var(--color-border-default)]">
          {surface.tools.map((tool) => {
            const locked = tool.requires_workspace && !surface.is_workspace;
            return (
              <li
                key={tool.name}
                className="flex items-start gap-3 border-b border-[var(--color-border-default)] py-3"
              >
                <Switch
                  checked={enabled.has(tool.name)}
                  disabled={locked}
                  onCheckedChange={(v) => void toggle(tool.name, v)}
                  aria-label={tool.name}
                  className="mt-0.5"
                />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-baseline gap-x-2">
                    <code className="font-mono text-sm text-[var(--color-text-primary)]">
                      {tool.name}
                    </code>
                    {/* Rule 10: only mark what needs attention. Every non-default
                        tool carried an "opt-in" badge and every workspace tool a
                        "workspace" badge, which badged most rows and so said
                        nothing. The switch already reports opt-in state; only
                        the unavailable case needs a word. */}
                    {locked && (
                      <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
                        {t("mcpTools.workspaceOnly")}
                      </span>
                    )}
                  </div>
                  {tool.description && (
                    <p className="mt-0.5 text-xs leading-relaxed text-[var(--color-text-tertiary)]">
                      {tool.description}
                    </p>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </OverviewSection>
  );
}
