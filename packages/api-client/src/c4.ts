/**
 * REST client for the C4 export endpoint.
 * Backend: packages/server/src/repowise/server/routers/c4.py
 */

import { BASE_URL, buildHeaders, doFetch } from "./client";

export interface StructurizrOptions {
  /** Emit a complete runnable workspace instead of a model fragment. */
  standalone?: boolean;
  /** Include the component (L3) level — one box per directory. */
  components?: boolean;
  /** Include third-party dependencies. Defaults to true server-side. */
  externals?: boolean;
}

/**
 * Fetch the Structurizr DSL for this repository.
 *
 * Text rather than JSON: the DSL is the artefact people commit and read, so
 * the backend returns it verbatim.
 */
export async function getStructurizrDsl(
  repoId: string,
  options: StructurizrOptions = {},
): Promise<string> {
  const params = new URLSearchParams();
  if (options.standalone) params.set("standalone", "true");
  if (options.components) params.set("components", "true");
  if (options.externals === false) params.set("externals", "false");
  const query = params.toString();
  const res = await doFetch(
    `${BASE_URL}/api/graph/${repoId}/c4/structurizr${query ? `?${query}` : ""}`,
    { headers: buildHeaders() },
  );
  if (!res.ok) {
    throw new Error(`Failed to fetch Structurizr DSL (${res.status})`);
  }
  return res.text();
}
