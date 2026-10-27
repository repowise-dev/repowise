/** What on this machine makes a provider usable, from the providers API
 *  `detected`: a signed-in agent CLI, a key in the env or the repo's
 *  `.repowise/.env`, or a key stored on the server. */
export type ModelSourceDetection = "signed_in" | "env" | "saved";

/** What the providers API may report: a source above, or `installed` (an agent
 *  CLI is on PATH but its sign-in is unknown). `installed` is not a ready
 *  source, so it never reaches a row. */
export type ProviderDetection = ModelSourceDetection | "installed";

/** The provider fields the source list reads. */
export interface ModelSourceProvider {
  id: string;
  name: string;
  requires_key?: boolean;
  /** A model that runs on this machine (not an agent CLI). */
  local_model?: boolean;
  detected?: ProviderDetection | null;
}

export type ModelSourceKind = "cli" | "key" | "local" | "paste" | "hosted";

/** One row of the model upgrade sheet. `id` is the provider id for the
 *  provider rows, and `PASTE_SOURCE_ID` / `HOSTED_SOURCE_ID` for the two
 *  fixed rows. */
export interface ModelSource {
  id: string;
  kind: ModelSourceKind;
  /** The provider this row makes the repo's choice; null for paste and hosted. */
  providerId: string | null;
  /** Display name without the catalog's "(Local CLI)" suffix. */
  name: string;
  detected: ModelSourceDetection | null;
}

export const PASTE_SOURCE_ID = "paste";
export const HOSTED_SOURCE_ID = "hosted";

/** "Claude Code (Local CLI)" -> "Claude Code", "Ollama (Local)" -> "Ollama". */
function shortName(name: string): string {
  return name.replace(/\s*\((?:Local CLI|Local)\)\s*$/, "");
}

function providerSource(
  provider: ModelSourceProvider,
  kind: ModelSourceKind,
): ModelSource {
  return {
    id: provider.id,
    kind,
    providerId: provider.id,
    name: shortName(provider.name),
    detected:
      provider.detected === "installed" ? null : (provider.detected ?? null),
  };
}

const PASTE_SOURCE: ModelSource = {
  id: PASTE_SOURCE_ID,
  kind: "paste",
  providerId: null,
  name: "",
  detected: null,
};

const HOSTED_SOURCE: ModelSource = {
  id: HOSTED_SOURCE_ID,
  kind: "hosted",
  providerId: null,
  name: "",
  detected: null,
};

/**
 * The sheet's rows, in order: signed-in agent CLIs, then providers with a key
 * already on this machine (catalog order within each), then the local model,
 * then "Paste a key", then hosted. The local model is the first catalog
 * provider flagged `local_model`; other keyless providers (proxies, local
 * OpenAI-compatible servers) stay in Settings, where their endpoint is set. Providers with nothing set up are left out;
 * Settings lists every provider.
 */
export function orderModelSources(providers: readonly ModelSourceProvider[]): ModelSource[] {
  const signedIn = providers
    .filter((p) => p.detected === "signed_in")
    .map((p) => providerSource(p, "cli"));
  const keyed = providers
    .filter((p) => p.detected === "env" || p.detected === "saved")
    .map((p) => providerSource(p, "key"));
  const local = providers.find((p) => p.local_model === true);
  return [
    ...signedIn,
    ...keyed,
    ...(local ? [providerSource(local, "local")] : []),
    PASTE_SOURCE,
    HOSTED_SOURCE,
  ];
}

/**
 * The row selected when the sheet opens: the repo's current provider when it
 * is one of the rows, else the first detected source, else "Paste a key".
 * Never the local model on its own (it may not be running) and never hosted.
 */
export function defaultModelSource(
  sources: readonly ModelSource[],
  activeProvider: string | null | undefined,
): string {
  const active = activeProvider
    ? sources.find((s) => s.providerId === activeProvider)
    : undefined;
  if (active) return active.id;
  return sources.find((s) => s.detected !== null)?.id ?? PASTE_SOURCE_ID;
}

/** Providers "Paste a key" offers: every one that takes an API key. */
export function pasteKeyProviders<P extends ModelSourceProvider>(providers: readonly P[]): P[] {
  return providers.filter((p) => p.requires_key !== false);
}
