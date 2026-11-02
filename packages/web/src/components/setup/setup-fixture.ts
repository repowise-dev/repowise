import type { SetupResponse } from "@/lib/api/setup";

export const NOW = Date.parse("2026-10-25T12:00:00Z");

/** Index up to date, Claude Code connected with no calls, Cursor installed, sync on. */
export function setupFixture(overrides: Partial<SetupResponse> = {}): SetupResponse {
  return {
    repo_id: "r1",
    generated_at: "2026-10-25T12:00:00Z",
    index: {
      state: "up_to_date",
      indexed_commit: "abcdef1234",
      live_head: "abcdef1234",
      indexed_at: "2026-10-25T11:00:00Z",
      file_count: 120,
      parse_error_count: 0,
      parse_error_files: [],
    },
    agents_available: true,
    calls_available: true,
    coverage_available: true,
    model_available: true,
    agents: [
      {
        id: "claude-code",
        display_name: "Claude Code",
        installed: true,
        connected: true,
        registrations: [{ method: "mcp", scope: "user", config_path: "~/.claude.json" }],
        config_files: { user: ["~/.claude.json"] },
        first_call_at: null,
        last_call_at: null,
      },
      {
        id: "cursor",
        display_name: "Cursor",
        installed: true,
        connected: false,
        config_files: { project: [".cursor/mcp.json"] },
      },
      { id: "codex", display_name: "Codex", installed: false, connected: false },
    ],
    calls: { first_call_at: null, last_call_at: null, unattributed: false },
    sync: { installed: true, detail: "installed" },
    rewrite_hook: { state: "not_installed", detail: "not installed" },
    coverage: {
      ingested_at: null,
      configured_paths: [],
      discovered: [{ path: "coverage/lcov.info", modified_at: "2026-10-23T00:00:00Z", age_days: 2 }],
    },
    model: { docs_mode: "deterministic", active_provider: null, configured_providers: ["provider-a"] },
    checks_available: true,
    checks: [
      { id: "post-commit-hook", name: "Post-commit hook", level: "ok", detail: "installed" },
      { id: "database", name: "Database", level: "ok", detail: "ok" },
    ],
    onboarding: {
      welcome_seen: {},
      setup_pill_hidden: false,
      hosted_card_hidden: false,
      hints_enabled: true,
      signed_in: false,
    },
    writes_allowed: true,
    ...overrides,
  };
}

/** Everything done: index current, an agent has called, sync on. */
export function doneFixture(overrides: Partial<SetupResponse> = {}): SetupResponse {
  const base = setupFixture();
  return setupFixture({
    agents: (base.agents ?? []).map((a) =>
      a.id === "claude-code"
        ? { ...a, first_call_at: "2026-10-25T11:56:00Z", last_call_at: "2026-10-25T11:58:00Z" }
        : a,
    ),
    calls: { first_call_at: "2026-10-25T11:56:00Z", last_call_at: "2026-10-25T11:58:00Z" },
    ...overrides,
  });
}
