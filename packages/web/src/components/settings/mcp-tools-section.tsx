"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Switch } from "@repowise-dev/ui/ui/switch";
import { SettingsRow, SettingsRows, useSaveState } from "@repowise-dev/ui/settings";
import { getMcpToolSurface, updateMcpTools } from "@/lib/api/mcp-tools";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";
import type { McpToolSurface } from "@/lib/api/types";
import { useTranslations } from "next-intl";

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
 * Which tools the MCP server exposes in one repository.
 *
 * Autosaves, like everything else in settings: each switch writes on change,
 * shows its tick on its own row, and goes back where the server has it when
 * the write fails. Only the newest write may land, so a slow older response
 * cannot undo a newer switch.
 */
export function McpToolsSection({ repoId }: { repoId: string }) {
  const t = useTranslations("settings");
  const [surface, setSurface] = useState<McpToolSurface | null>(null);
  const [enabled, setEnabled] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [savedTool, setSavedTool] = useState<string | null>(null);
  const save = useSaveState();
  // What the server last accepted, from the newest write that succeeded.
  const server = useRef<{ surface: McpToolSurface | null; seq: number }>({ surface: null, seq: 0 });
  const writeSeq = useRef(0);

  useEffect(() => {
    setLoading(true);
    setLoadError(null);
    getMcpToolSurface(repoId)
      .then((s) => {
        server.current = { surface: s, seq: 0 };
        setSurface(s);
        setEnabled(enabledNames(s));
      })
      .catch((e) => setLoadError(toFriendlyMessage(e, "Could not load tools")))
      .finally(() => setLoading(false));
  }, [repoId]);

  async function toggle(name: string, on: boolean) {
    if (!surface) return;
    const next = new Set(enabled);
    if (on) next.add(name);
    else next.delete(name);
    setEnabled(next);
    setSavedTool(name);

    const deltas = toDeltas(surface, next);
    const mine = ++writeSeq.current;
    const outcome = await save.track(
      async () => {
        const updated = await updateMcpTools({ repo_id: repoId, tools: deltas.length ? deltas : null });
        if (mine > server.current.seq) server.current = { surface: updated, seq: mine };
      },
      (e) => toFriendlyMessage(e, "Could not save"),
    );
    const accepted = server.current.surface;
    if (outcome === "saved" && accepted) setSurface(accepted);
    // A failure puts every switch back where the server still has it.
    if (outcome !== "stale" && accepted) setEnabled(enabledNames(accepted));
  }

  const summary = useMemo(() => {
    if (!surface) return null;
    const available = surface.tools.filter((tool) => !(tool.requires_workspace && !surface.is_workspace));
    const on = available.filter((tool) => enabled.has(tool.name)).length;
    const mode = surface.is_workspace ? t("mcpTools.summaryWorkspace") : t("mcpTools.summarySingleRepo");
    return t("mcpTools.summary", { on, total: available.length, mode });
  }, [surface, enabled, t]);

  if (loadError) return <p className="text-xs text-[var(--color-error)]">{loadError}</p>;
  if (loading || !surface) return null;

  return (
    <div className="flex flex-col gap-2">
      {/* The figure is the point: "9 of 15 exposed" before fifteen rows. */}
      {summary && <p className="text-xs text-[var(--color-text-secondary)]">{summary}</p>}
      <SettingsRows>
        {surface.tools.map((tool) => {
          const locked = tool.requires_workspace && !surface.is_workspace;
          return (
            <SettingsRow
              key={tool.name}
              inline
              label={<code className="font-mono">{tool.name}</code>}
              hint={locked ? `${t("mcpTools.workspaceOnly")} · ${tool.description ?? ""}` : tool.description}
              {...(savedTool === tool.name ? { saveState: save.state, saveError: save.error } : {})}
            >
              <Switch
                checked={enabled.has(tool.name)}
                disabled={locked}
                onCheckedChange={(v) => void toggle(tool.name, v)}
                aria-label={tool.name}
              />
            </SettingsRow>
          );
        })}
      </SettingsRows>
    </div>
  );
}
