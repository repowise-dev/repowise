"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  McpToolSurfaceEditor,
  mcpToolsValue,
  profileBase,
  useSaveState,
  type McpToolProfileName,
  type McpToolSurfaceLabels,
} from "@repowise-dev/ui/settings";
import { getMcpToolSurface, updateMcpTools } from "@/lib/api/mcp-tools";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";
import type { McpToolSurface } from "@/lib/api/types";
import { useTranslations } from "next-intl";

function enabledNames(surface: McpToolSurface): Set<string> {
  return new Set(surface.tools.filter((t) => t.enabled).map((t) => t.name));
}

/**
 * Which tools the MCP server lists in one repository: a profile, or each
 * client's own default, plus per-tool switches saved as +/- deltas on it.
 *
 * Autosaves, like everything else in settings: each change writes at once,
 * shows its tick on its own row, and goes back where the server has it when
 * the write fails. Only the newest write may land, so a slow older response
 * cannot undo a newer change.
 */
export function McpToolsSection({ repoId }: { repoId: string }) {
  const t = useTranslations("settings");
  const [surface, setSurface] = useState<McpToolSurface | null>(null);
  const [profile, setProfile] = useState<McpToolProfileName | null>(null);
  const [enabled, setEnabled] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [savedTool, setSavedTool] = useState<string | null>(null);
  const save = useSaveState();
  // What the server last accepted, from the newest write that succeeded.
  const server = useRef<{ surface: McpToolSurface | null; seq: number }>({ surface: null, seq: 0 });
  const writeSeq = useRef(0);

  function show(s: McpToolSurface) {
    setSurface(s);
    setProfile(s.profile ?? null);
    setEnabled(enabledNames(s));
  }

  useEffect(() => {
    setLoading(true);
    setLoadError(null);
    getMcpToolSurface(repoId)
      .then((s) => {
        server.current = { surface: s, seq: 0 };
        show(s);
      })
      .catch((e) => setLoadError(toFriendlyMessage(e, "Could not load tools")))
      .finally(() => setLoading(false));
  }, [repoId]);

  async function write(nextProfile: McpToolProfileName | null, next: Set<string>, row: string) {
    if (!surface) return;
    setProfile(nextProfile);
    setEnabled(next);
    setSavedTool(row);

    const tools = mcpToolsValue(surface, nextProfile, next);
    const mine = ++writeSeq.current;
    const outcome = await save.track(
      async () => {
        const updated = await updateMcpTools({ repo_id: repoId, tools });
        if (mine > server.current.seq) server.current = { surface: updated, seq: mine };
      },
      (e) => toFriendlyMessage(e, "Could not save"),
    );
    const accepted = server.current.surface;
    // A failure puts every control back where the server still has it.
    if (outcome !== "stale" && accepted) show(accepted);
  }

  function toggle(name: string, on: boolean) {
    const next = new Set(enabled);
    if (on) next.add(name);
    else next.delete(name);
    void write(profile, next, name);
  }

  function chooseProfile(next: McpToolProfileName | null) {
    if (!surface) return;
    void write(next, new Set(profileBase(surface, next)), "");
  }

  const labels = useMemo<Partial<McpToolSurfaceLabels>>(
    () => ({
      profile: t("mcpTools.profile"),
      perClient: t("mcpTools.perClient"),
      profileNames: {
        core: t("mcpTools.profileCore"),
        lean: t("mcpTools.profileLean"),
        standard: t("mcpTools.profileStandard"),
        all: t("mcpTools.profileAll"),
      },
      perClientHint: (core, standard) => t("mcpTools.perClientHint", { core, standard }),
      summary: (on, tokens) => t("mcpTools.summary", { on, tokens }),
      restart: t("mcpTools.restart"),
      workspaceOnly: t("mcpTools.workspaceOnly"),
      coreBadge: t("mcpTools.coreBadge"),
      tokens: (n) => t("mcpTools.tokens", { n }),
    }),
    [t],
  );

  if (loadError) return <p className="text-xs text-[var(--color-error)]">{loadError}</p>;
  if (loading || !surface) return null;

  return (
    <McpToolSurfaceEditor
      surface={surface}
      profile={profile}
      enabled={enabled}
      onProfileChange={chooseProfile}
      onToggle={toggle}
      savedTool={savedTool}
      saveState={save.state}
      saveError={save.error}
      labels={labels}
    />
  );
}
