/**
 * The data the generated settings page renders: one entry per setting, as the
 * settings API (and `repowise config list --format json`) gives it. Only the
 * fields the page reads, so any backend that serves this shape can drive it.
 */

export type RegistrySettingType = "bool" | "int" | "float" | "str" | "list" | "mapping";

/** Where a value came from. Known ones get a label; any other layer shows as sent. */
export type RegistrySource = "default" | "env" | "repo_file" | "user_file" | (string & {});

export interface RegistrySettingState {
  value?: unknown;
  source: RegistrySource;
  /** Why a value that is present was not used. */
  problem?: string | null;
  /** A layer above this one decides the value; the control is disabled. */
  locked?: boolean;
  /** Why it is locked, shown under the row. */
  locked_reason?: string | null;
  default?: unknown;
  /** Set somewhere, to something other than the default. */
  changed: boolean;
}

export interface RegistrySetting extends RegistrySettingState {
  key: string;
  type: RegistrySettingType;
  description: string;
  area: string;
  /** `repo`, `user` or `env`. */
  scope: string;
  default_note?: string | null;
  choices?: string[];
  minimum?: number | null;
  maximum?: number | null;
  exclusive_minimum?: boolean;
  env?: { name: string } | null;
  internal?: boolean;
  /** What a value must be, in plain words. */
  accepts?: string;
  /** When a change takes effect, in plain words. */
  applies?: string;
  /** This page may change it. False for environment and per-user settings. */
  writable?: boolean;
}

export interface RegistryArea {
  area: string;
  settings: RegistrySetting[];
}

/** A value that fits the repository better than the current one. */
export interface RegistrySuggestion {
  key: string;
  current_value?: unknown;
  suggested_value?: unknown;
  reason: string;
  impact?: string | null;
  /** What the change saves or improves: `tokens`, `cost` or `quality`. */
  benefit?: string | null;
  /** The command that applies it. */
  command: string;
}

/** What one write did: the setting's new state, and others it moved. */
export interface RegistryChange {
  key: string;
  after: RegistrySettingState;
  also: { key: string; after: RegistrySettingState }[];
  /** The environment variable that still decides the value, if one does. */
  env_override?: string | null;
}

/** A page that owns a setting's richer editor (provider keys, refactoring). */
export interface RegistryLink {
  href: string;
  label: string;
}
