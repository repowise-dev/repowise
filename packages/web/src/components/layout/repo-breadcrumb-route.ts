/** The docs reader. Every wiki page lives at this one URL, keyed by `?page=`. */
const DOCS_READER = /^\/repos\/[^/]+\/docs\/?$/;

/**
 * Whether the route breadcrumb should render on a given path.
 *
 * The docs reader draws its own breadcrumb from the wiki page tree. The route
 * cannot express that hierarchy — every page sits at the same `/docs` URL — so
 * the route trail there is always the same three crumbs, stacked directly above
 * the reader's. One of the two bars has to go, and the reader's is the one
 * carrying information.
 *
 * Scoped to the reader itself: `/docs/coverage` is an ordinary page with no
 * breadcrumb of its own, and keeps the route trail.
 */
export function showsRouteBreadcrumb(pathname: string): boolean {
  return !DOCS_READER.test(pathname);
}
