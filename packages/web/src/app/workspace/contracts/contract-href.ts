/**
 * The contract detail route, built from the shareable identity.
 *
 * `(repo, file, id)` and not `id` alone: several repos declare the same
 * `http::GET::/user`. Query parameters rather than path segments because both
 * `file` and `id` carry slashes, which a path segment would have to escape and
 * the reader would then have to look at.
 *
 * Deliberately not keyed on the line. One file can call the same endpoint
 * twice, and the endpoint answers with the first of them; keying on the line
 * would rot every saved link the moment somebody adds an import above the call.
 */
export function contractDetailHref(contract: {
  repo: string;
  file_path: string;
  contract_id: string;
}): string {
  const params = new URLSearchParams({
    repo: contract.repo,
    file: contract.file_path,
    id: contract.contract_id,
  });
  return `/workspace/contracts/detail?${params.toString()}`;
}

/** The full list's filters, as the page reads them from the URL. */
export interface ContractListFilters {
  type?: string | undefined;
  repo?: string | undefined;
  role?: string | undefined;
  /** `yes`: on a matched link. `no`: on none. */
  linked?: string | undefined;
  q?: string | undefined;
  page?: number | undefined;
}

/** The list filtered, scrolled to the list. Empty values are left out. */
export function contractsListHref(filters: ContractListFilters): string {
  const params = new URLSearchParams();
  for (const key of ["type", "repo", "role", "linked", "q"] as const) {
    const value = filters[key];
    if (value) params.set(key, value);
  }
  if (filters.page && filters.page > 1) params.set("page", String(filters.page));
  const query = params.toString();
  return `/workspace/contracts${query ? `?${query}` : ""}#all-contracts`;
}
