"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  MCP_ALWAYS_LOAD_KEY,
  McpToolSurfaceEditor,
  mcpToolsValue,
  profileBase,
  useSaveState,
  type McpToolProfileName,
  type McpToolSurfaceLabels,
  type RegistrySetting,
} from "@repowise-dev/ui/settings";
import { getMcpToolSurface, updateMcpTools } from "@/lib/api/mcp-tools";
import { getSettings, setSetting } from "@/lib/api/settings";
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
 * cannot undo a newer change. The always-load switch reads and writes
 * `mcp.always_load` through the settings API, like any registry setting.
 */
export function McpToolsSection({ repoId }: { repoId: string }) {
  const t = useTranslations("settings");
  const [surface, setSurface] = useState<McpToolSurface | null>(null);
  const [profile, setProfile] = useState<McpToolProfileName | null>(null);
  const [enabled, setEnabled] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [savedTool, setSavedTool] = useState<string | null>(null);
  const [alwaysLoad, setAlwaysLoad] = useState<RegistrySetting | null>(null);
  const save = useSaveState();
  // What the server last accepted, from the newest write that succeeded.
  const server = useRef<{ surface: McpToolSurface | null; seq: number }>({ surface: null, seq: 0 });
  const writeSeq = useRef(0);
  // The same for `mcp.always_load`: its last accepted state and the newest write.
  const alwaysServer = useRef<RegistrySetting | null>(null);
  const alwaysSeq = useRef(0);

  function show(s: McpToolSurface) {
    setSurface(s);
    setProfile(s.profile ?? null);
    setEnabled(enabledNames(s));
  }

  useEffect(() => {
    let live = true;
    setLoading(true);
    setLoadError(null);
    // Writes started for the previous repository are older than this load and never land.
    server.current = { surface: null, seq: writeSeq.current };
    getMcpToolSurface(repoId)
      .then((s) => {
        server.current = { surface: s, seq: writeSeq.current };
        show(s);
      })
      .catch((e) => setLoadError(toFriendlyMessage(e, "Could not load tools")))
      .finally(() => setLoading(false));
    // Without it the page still edits the tool list; the switch is left out.
    // A write still in flight for the previous repository no longer counts as newest.
    setAlwaysLoad(null);
    alwaysServer.current = null;
    alwaysSeq.current++;
    getSettings(repoId)
      .then((r) => {
        if (!live) return;
        const found = r.areas.flatMap((a) => a.settings).find((s) => s.key === MCP_ALWAYS_LOAD_KEY);
        alwaysServer.current = (found as RegistrySetting | undefined) ?? null;
        setAlwaysLoad(alwaysServer.current);
      })
      .catch(() => {
        if (live) setAlwaysLoad(null);
      });
    return () => {
      live = false;
    };
  }, [repoId]);

  async function writeAlwaysLoad(on: boolean) {
    const base = alwaysServer.current;
    if (!base) return;
    setAlwaysLoad((current) => (current ? { ...current, value: on } : current));
    setSavedTool(MCP_ALWAYS_LOAD_KEY);
    const mine = ++alwaysSeq.current;
    const outcome = await save.track(
      async () => {
        const change = await setSetting(repoId, MCP_ALWAYS_LOAD_KEY, on);
        if (mine === alwaysSeq.current) {
          alwaysServer.current = { ...base, ...(change.after as Partial<RegistrySetting>) };
        }
      },
      (e) => toFriendlyMessage(e, "Could not save"),
    );
    // Only the newest write decides; a failure goes back to what the server has.
    if (outcome !== "stale" && mine === alwaysSeq.current) setAlwaysLoad(alwaysServer.current);
  }

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
      summaryPerClient: (standard, others, tokens) =>
        t("mcpTools.summaryPerClient", { standard, others, tokens }),
      restart: t("mcpTools.restart"),
      workspaceOnly: t("mcpTools.workspaceOnly"),
      coreBadge: t("mcpTools.coreBadge"),
      tokens: (n) => t("mcpTools.tokens", { n }),
      sameAs: t("mcpTools.sameAs"),
      alwaysLoad: t("mcpTools.alwaysLoad"),
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
      alwaysLoad={alwaysLoad}
      onAlwaysLoadChange={(on) => void writeAlwaysLoad(on)}
    />
  );
}
