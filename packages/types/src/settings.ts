/**
 * Per-repo settings form value contract.
 *
 * Hosted ships a read-only variant — the @repowise-dev/ui shell receives an
 * optional `onSubmit`; if absent, the form renders disabled. OSS web wires
 * the full save round-trip via its own `updateRepo` API.
 */

/**
 * Built-in wiki documentation styles. Mirrors the Python registry in
 * `packages/core/.../generation/styles/registry.py`. Custom styles
 * (`.repowise/styles/`) are a CLI/power-user feature and are not surfaced here.
 */
export type WikiStyle = "comprehensive" | "caveman" | "reference" | "tutorial";

export const DEFAULT_WIKI_STYLE: WikiStyle = "comprehensive";

export interface WikiStyleOption {
  id: WikiStyle;
  label: string;
  description: string;
}

export const WIKI_STYLES: readonly WikiStyleOption[] = [
  {
    id: "comprehensive",
    label: "Comprehensive",
    description: "Full, narrative documentation for humans and AI (default).",
  },
  {
    id: "caveman",
    label: "Caveman",
    description: "Token-condensed, AI-first pages — terse fragments, ~70% smaller.",
  },
  {
    id: "reference",
    label: "Reference",
    description: "API-manual style — signature-dense, exhaustive, minimal narrative.",
  },
  {
    id: "tutorial",
    label: "Tutorial",
    description: "Guided, beginner-friendly walkthroughs that teach the codebase.",
  },
] as const;

export interface RepoSettingsValue {
  name: string;
  default_branch: string;
  exclude_patterns: string[];
  /** Documentation voice/density. Defaults to "comprehensive" when unset. */
  wiki_style?: WikiStyle;
}
