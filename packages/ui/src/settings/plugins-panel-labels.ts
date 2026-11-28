/** The plugins panel's copy; a host passes translations over these. */
export interface PluginsPanelLabels {
  sourcesTitle: string;
  pluginsTitle: string;
  ready: string;
  failed: string;
  notConfigured: string;
  /** Any other plugin status (`skipped`, `pending`). */
  status: (status: string) => string;
  settingsOf: (plugin: string) => string;
  set: string;
  notSet: string;
  /** A secret setting: only whether it is set, never a value. */
  secret: (set: boolean) => string;
  builtIn: string;
  fromPlugin: (plugin: string) => string;
  neverSynced: string;
  /** A sync that stored `batches` batches. */
  stored: (batches: number) => string;
  /** A sync that ended in `state` (`unavailable`, `refused`, `failed`). */
  syncState: (state: string) => string;
  /** With a `{time}` placeholder. */
  syncedAt: string;
  syncing: string;
  syncNow: string;
  syncNowAria: (source: string) => string;
  syncFailed: string;
  emptyTitle: string;
  emptyBody: string;
  guide: string;
  emptyBuild: string;
}

export const DEFAULT_PLUGINS_PANEL_LABELS: PluginsPanelLabels = {
  sourcesTitle: "Sources",
  pluginsTitle: "Installed plugins",
  ready: "Ready",
  failed: "Failed",
  notConfigured: "Not configured",
  status: (status) => status.charAt(0).toUpperCase() + status.slice(1),
  settingsOf: (plugin) => `Settings of ${plugin}`,
  set: "set",
  notSet: "not set",
  secret: (set) => (set ? "secret, set" : "secret, not set"),
  builtIn: "Built in",
  fromPlugin: (plugin) => `From ${plugin}`,
  neverSynced: "Never synced",
  stored: (batches) => `Stored ${batches} ${batches === 1 ? "batch" : "batches"}`,
  syncState: (state) =>
    ({ unavailable: "Unavailable", refused: "Refused", failed: "Failed" })[state] ?? state,
  syncedAt: "{time}",
  syncing: "Syncing...",
  syncNow: "Sync now",
  syncNowAria: (source) => `Sync ${source} now`,
  syncFailed: "The sync didn't start.",
  emptyTitle: "No plugins installed",
  emptyBody:
    "Plugins feed repowise what lives outside git: issue trackers for defects and decisions, production evidence, team directories, and new outputs.",
  guide: "Read the plugin guide",
  emptyBuild: "Start your own plugin:",
};
