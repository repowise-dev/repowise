/**
 * Per-repo settings form value contract.
 *
 * Hosted ships a read-only variant — the @repowise-dev/ui shell receives an
 * optional `onSubmit`; if absent, the form renders disabled. OSS web wires
 * the full save round-trip via its own `updateRepo` API.
 */

export interface RepoSettingsValue {
  name: string;
  default_branch: string;
  exclude_patterns: string[];
}
