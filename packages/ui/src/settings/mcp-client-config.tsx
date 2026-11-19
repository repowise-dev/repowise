"use client";

/**
 * The MCP config one agent needs for one repository, picked by agent (and by
 * repository when there are several), with what that agent will list.
 *
 * Presentational: the snippet is whatever `repowise agents print-config`
 * printed, passed in as data, so no host keeps its own copy of any agent's
 * config shape.
 */

import * as React from "react";
import { cn } from "../lib/cn";
import { CopyLine, SETTINGS_MICRO_LABEL } from "./settings-primitives";

export interface McpClientRef {
  id: string;
  display_name: string;
}

export interface McpClientConfigData {
  clients: McpClientRef[];
  target: string;
  display_name: string;
  /** The snippet to paste, in the agent's own format (JSON, TOML or YAML). */
  config: string;
  docs_url?: string | null;
  /** The profile this agent lists; null when `mcp.tools` is an exact list. */
  profile?: string | null;
  tool_count: number;
  /** `mcp.always_load` for an agent that defers schemas; null for one that loads them up front. */
  always_load?: boolean | null;
}

export interface McpClientConfigLabels {
  agent: string;
  repo: string;
  snippet: (agent: string) => string;
  /** What the agent lists: `profile` is null for an exact list. */
  surface: (agent: string, count: number, profile: string | null) => string;
  alwaysLoadOn: string;
  alwaysLoadOff: string;
  docs: (agent: string) => string;
  loading: string;
  noRepos: string;
}

export const DEFAULT_MCP_CLIENT_CONFIG_LABELS: McpClientConfigLabels = {
  agent: "Agent",
  repo: "Repository",
  snippet: (agent) => `${agent} MCP config`,
  surface: (agent, count, profile) =>
    profile
      ? `${agent} lists ${count} tools, the ${profile} profile.`
      : `${agent} lists ${count} tools, the exact list in mcp.tools.`,
  alwaysLoadOn: "Read tools load from the first turn (mcp.always_load is on).",
  alwaysLoadOff: "Read tools load through tool search (mcp.always_load is off).",
  docs: (agent) => `${agent} MCP docs`,
  loading: "Loading the config...",
  noRepos: "Add a repository first: the config points the agent at one.",
};

export interface McpClientConfigProps {
  repos: { id: string; name: string }[];
  repoId: string | null;
  onRepoChange: (id: string) => void;
  /** The agent asked for; null until the first answer names one. */
  target: string | null;
  onTargetChange: (id: string) => void;
  data?: McpClientConfigData | null;
  loading?: boolean;
  error?: string | null;
  labels?: Partial<McpClientConfigLabels>;
}

const SELECT_CLASS =
  "h-8 w-full min-w-0 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-2 text-xs text-[var(--color-text-primary)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-focus-ring)] disabled:opacity-50 sm:w-auto";

export function McpClientConfig({
  repos,
  repoId,
  onRepoChange,
  target,
  onTargetChange,
  data,
  loading = false,
  error,
  labels: overrides,
}: McpClientConfigProps) {
  const labels = { ...DEFAULT_MCP_CLIENT_CONFIG_LABELS, ...overrides };
  const agentId = React.useId();
  const repoSelectId = React.useId();

  if (repos.length === 0) {
    return <p className="text-xs text-[var(--color-text-tertiary)]">{labels.noRepos}</p>;
  }

  const clients = data?.clients ?? [];
  const facts = data
    ? [
        labels.surface(data.display_name, data.tool_count, data.profile ?? null),
        ...(data.always_load == null ? [] : [data.always_load ? labels.alwaysLoadOn : labels.alwaysLoadOff]),
      ].join(" ")
    : null;

  return (
    <div className="flex min-w-0 flex-col gap-2" aria-busy={loading}>
      <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-end">
        <div className="flex min-w-0 flex-col gap-1">
          <label htmlFor={agentId} className={SETTINGS_MICRO_LABEL}>
            {labels.agent}
          </label>
          <select
            id={agentId}
            value={target ?? data?.target ?? ""}
            disabled={clients.length === 0}
            onChange={(e) => onTargetChange(e.target.value)}
            className={SELECT_CLASS}
          >
            {clients.length === 0 && <option value="">{labels.loading}</option>}
            {clients.map((c) => (
              <option key={c.id} value={c.id}>
                {c.display_name}
              </option>
            ))}
          </select>
        </div>
        {repos.length > 1 && (
          <div className="flex min-w-0 flex-col gap-1">
            <label htmlFor={repoSelectId} className={SETTINGS_MICRO_LABEL}>
              {labels.repo}
            </label>
            <select
              id={repoSelectId}
              value={repoId ?? ""}
              onChange={(e) => onRepoChange(e.target.value)}
              className={SELECT_CLASS}
            >
              {repos.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>
      {error ? (
        <p role="alert" className="text-xs text-[var(--color-error)]">
          {error}
        </p>
      ) : data ? (
        <div className={cn("flex min-w-0 flex-col gap-1.5 transition-opacity", loading && "opacity-60")}>
          <CopyLine value={data.config} copyLabel={labels.snippet(data.display_name)} />
          <p className="text-xs leading-relaxed text-[var(--color-text-tertiary)] [text-wrap:pretty]">
            {facts}
            {data.docs_url && (
              <>
                {" "}
                <a
                  href={data.docs_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-[var(--color-text-secondary)] underline underline-offset-2 hover:text-[var(--color-text-primary)]"
                >
                  {labels.docs(data.display_name)}
                </a>
              </>
            )}
          </p>
        </div>
      ) : (
        // Holds about the height of a snippet so the answer does not push the page.
        <p className="min-h-[9rem] text-xs text-[var(--color-text-tertiary)]" role="status">
          {labels.loading}
        </p>
      )}
    </div>
  );
}
