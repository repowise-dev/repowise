"use client";

/**
 * Which tools the MCP server lists to an agent, as one model: a profile
 * (or each client's own default) plus per-tool switches on top of it.
 *
 * Presentational: the surface comes in as props and every change goes out as
 * the `mcp.tools` value to save, so the dashboard and any other host share it.
 */

import * as React from "react";
import { Switch } from "../ui/switch";
import { SettingsRow, SettingsRows, type SaveState } from "./settings-primitives";

export type McpToolProfileName = "core" | "lean" | "standard" | "all";

/** The profiles in the order a reader meets them: smallest first. */
export const MCP_TOOL_PROFILES: readonly McpToolProfileName[] = ["core", "lean", "standard", "all"];

export interface McpToolSurfaceTool {
  name: string;
  description: string;
  requires_workspace: boolean;
  /** In the standard set: what Claude Code lists when nothing is chosen. */
  default: boolean;
  enabled: boolean;
  schema_tokens?: number;
}

export interface McpToolSurfaceData {
  is_workspace: boolean;
  profile?: McpToolProfileName | null;
  profiles?: Partial<Record<McpToolProfileName, string[]>>;
  tools: McpToolSurfaceTool[];
}

export interface McpToolSurfaceLabels {
  profile: string;
  perClient: string;
  profileNames: Record<McpToolProfileName, string>;
  perClientHint: (core: number, standard: number) => string;
  summary: (on: number, tokens: number) => string;
  /** Per-client mode: what Claude Code and the other clients are listed. */
  summaryPerClient: (standard: number, others: number, tokens: number) => string;
  restart: string;
  workspaceOnly: string;
  coreBadge: string;
  tokens: (n: number) => string;
}

export const DEFAULT_MCP_TOOL_SURFACE_LABELS: McpToolSurfaceLabels = {
  profile: "Tools listed",
  perClient: "Per client (recommended)",
  profileNames: {
    core: "Core: answer, context, search, symbol, why",
    lean: "Lean: core plus get_risk",
    standard: "Standard: Claude Code's default set",
    all: "All: every tool available here",
  },
  perClientHint: (core, standard) =>
    `Claude Code loads tool schemas only when needed, so it lists ${standard} tools. ` +
    `Other clients load every schema each turn, so they list the ${core} core tools. ` +
    "Switches below add or remove tools for every client.",
  summary: (on, tokens) => `${on} tools on, about ${tokens.toLocaleString()} schema tokens per turn on clients that load schemas up front.`,
  summaryPerClient: (standard, others, tokens) =>
    `Claude Code lists ${standard} tools. Other clients list ${others}, about ${tokens.toLocaleString()} schema tokens per turn.`,
  restart: "Applies when the MCP server next starts: restart it, or your agent.",
  workspaceOnly: "workspace only",
  coreBadge: "core",
  tokens: (n) => `~${n} tokens`,
};

/** The `mcp.tools` value for a profile plus the switches that differ from it. */
export function mcpToolsValue(
  surface: McpToolSurfaceData,
  profile: McpToolProfileName | null,
  enabled: ReadonlySet<string>,
): string[] | null {
  const base = new Set(profileBase(surface, profile));
  const added = [...enabled].filter((n) => !base.has(n)).sort();
  const removed = [...base].filter((n) => !enabled.has(n)).sort();
  const tokens = [...(profile ? [profile] : []), ...added.map((n) => `+${n}`), ...removed.map((n) => `-${n}`)];
  return tokens.length ? tokens : null;
}

/** The tools a profile starts from; no profile starts from the standard set. */
export function profileBase(surface: McpToolSurfaceData, profile: McpToolProfileName | null): string[] {
  if (profile && surface.profiles?.[profile]) return surface.profiles[profile] ?? [];
  return surface.tools.filter((t) => t.default).map((t) => t.name);
}

export interface McpToolSurfaceEditorProps {
  surface: McpToolSurfaceData;
  profile: McpToolProfileName | null;
  enabled: ReadonlySet<string>;
  onProfileChange: (profile: McpToolProfileName | null) => void;
  onToggle: (name: string, on: boolean) => void;
  /** The row whose save state to show ("" for the profile), with that state. */
  savedTool?: string | null;
  saveState?: SaveState;
  saveError?: string | null;
  labels?: Partial<McpToolSurfaceLabels>;
}

export function McpToolSurfaceEditor({
  surface,
  profile,
  enabled,
  onProfileChange,
  onToggle,
  savedTool,
  saveState,
  saveError,
  labels: overrides,
}: McpToolSurfaceEditorProps) {
  const labels = { ...DEFAULT_MCP_TOOL_SURFACE_LABELS, ...overrides };
  const selectId = React.useId();
  const core = new Set(surface.profiles?.core ?? []);
  const available = surface.tools.filter((t) => !(t.requires_workspace && !surface.is_workspace));
  const on = available.filter((t) => enabled.has(t.name));
  const standardCount = profileBase(surface, null).length;
  // With no profile, switches are deltas on each client's own default, so
  // the clients that load schemas up front get core with the same changes.
  const standard = new Set(profileBase(surface, null));
  const upFront = profile
    ? on
    : on.filter((t) => !standard.has(t.name) || core.has(t.name));
  const tokens = upFront.reduce((sum, t) => sum + (t.schema_tokens ?? 0), 0);

  return (
    <div className="flex flex-col gap-2">
      <SettingsRows>
        <SettingsRow
          label={labels.profile}
          htmlFor={selectId}
          hint={profile ? labels.restart : `${labels.perClientHint(core.size, standardCount)} ${labels.restart}`}
          {...(savedTool === "" ? { saveState, saveError } : {})}
        >
          <select
            id={selectId}
            value={profile ?? ""}
            onChange={(e) => onProfileChange((e.target.value || null) as McpToolProfileName | null)}
            className="h-8 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-2 text-xs text-[var(--color-text-primary)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-focus-ring)]"
          >
            <option value="">{labels.perClient}</option>
            {MCP_TOOL_PROFILES.map((name) => (
              <option key={name} value={name}>
                {labels.profileNames[name]}
              </option>
            ))}
          </select>
        </SettingsRow>
      </SettingsRows>
      <p className="text-xs text-[var(--color-text-secondary)]">
        {profile ? labels.summary(on.length, tokens) : labels.summaryPerClient(on.length, upFront.length, tokens)}
      </p>
      <SettingsRows>
        {surface.tools.map((tool) => {
          const locked = tool.requires_workspace && !surface.is_workspace;
          const facts = [
            ...(locked ? [labels.workspaceOnly] : []),
            ...(core.has(tool.name) ? [labels.coreBadge] : []),
            ...(tool.schema_tokens ? [labels.tokens(tool.schema_tokens)] : []),
          ];
          return (
            <SettingsRow
              key={tool.name}
              inline
              label={<code className="font-mono">{tool.name}</code>}
              hint={facts.length ? `${facts.join(" · ")} · ${tool.description}` : tool.description}
              {...(savedTool === tool.name ? { saveState, saveError } : {})}
            >
              <Switch
                checked={enabled.has(tool.name)}
                disabled={locked}
                onCheckedChange={(v) => onToggle(tool.name, v)}
                aria-label={tool.name}
              />
            </SettingsRow>
          );
        })}
      </SettingsRows>
    </div>
  );
}
