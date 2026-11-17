/**
 * The welcome tour opens from a link: the repo overview with `?tour=1`. The
 * welcome dialog listens for this query param on the overview route, opens
 * itself, and removes the param once it has.
 */
export const TOUR_QUERY_PARAM = "tour";

export function tourHref(repoId: string): string {
  return `/repos/${encodeURIComponent(repoId)}/overview?${TOUR_QUERY_PARAM}=1`;
}
