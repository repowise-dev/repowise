/** The generated settings page's copy; a host passes translations over these. */
export interface SettingsRegistryLabels {
  search: string;
  searchPlaceholder: string;
  changedOnly: string;
  showInternal: string;
  /** "12 of 140 settings", for the rows the filters show. */
  count: (shown: number, total: number) => string;
  noMatches: string;
  suggestionsTitle: string;
  suggestionsDescription: string;
  apply: string;
  applyAria: (key: string) => string;
  suggested: (value: string) => string;
  changed: string;
  defaultValue: (value: string) => string;
  /** Labels for known sources; any other source shows as sent. */
  sources: Record<string, string>;
  /** An environment variable decides a value the file could hold. */
  envOverride: (name: string) => string;
  /** The setting lives only in an environment variable. */
  envOnly: (name: string) => string;
  /** The setting is saved per user, not per repository. */
  userSetting: (key: string) => string;
  lockedReason: string;
  reset: string;
  resetAria: (key: string) => string;
  copyPrompt: string;
  promptTitle: (key: string) => string;
  promptDescription: string;
  saveFailed: string;
  unset: string;
  listPlaceholder: string;
  /** Section titles by area; an area without one shows its name. */
  areaTitles: Record<string, string>;
}

export const DEFAULT_SETTINGS_REGISTRY_LABELS: SettingsRegistryLabels = {
  search: "Search settings",
  searchPlaceholder: "Search by name or what it does",
  changedOnly: "Changed only",
  showInternal: "Show internal settings",
  count: (shown, total) => (shown === total ? `${total} settings` : `${shown} of ${total} settings`),
  noMatches: "No setting matches these filters.",
  suggestionsTitle: "Suggested for this repository",
  suggestionsDescription: "Values that fit this repository better than the current ones.",
  apply: "Apply",
  applyAria: (key) => `Apply the suggested value for ${key}`,
  suggested: (value) => `Suggested: ${value}`,
  changed: "Changed",
  defaultValue: (value) => `default ${value}`,
  sources: {
    default: "Default",
    env: "Environment",
    repo_file: "Repo file",
    user_file: "User file",
  },
  envOverride: (name) => `${name} is set in the environment and wins over the file.`,
  envOnly: (name) => `Read from ${name} in the server's environment.`,
  userSetting: (key) =>
    `Saved for your user, not this repository. Change it with repowise config set ${key} in a terminal.`,
  lockedReason: "Set above this repository, so it cannot be changed here.",
  reset: "Reset",
  resetAria: (key) => `Reset ${key} to its default`,
  copyPrompt: "Copy prompt",
  promptTitle: (key) => `Change ${key} with your agent`,
  promptDescription:
    "Your agent explains the setting, asks before changing it, and gives you the undo command.",
  saveFailed: "Could not save",
  unset: "unset",
  listPlaceholder: "a, b",
  areaTitles: {
    account: "Account",
    attribution: "AI attribution",
    decisions: "Decisions",
    distill: "Command output",
    editor: "Editor files",
    events: "Change events",
    generation: "Documentation",
    health: "Code health",
    hooks: "Agent hooks",
    indexing: "Indexing",
    mcp: "MCP",
    providers: "Model providers",
    server: "Server",
  },
};
