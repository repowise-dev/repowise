/**
 * The class list for the box the docs reader lives in.
 *
 * The reader has to be exactly as tall as the space `main` leaves after the
 * bands a repo stacks above the page — the reindex hint, the active-job banner,
 * the upgrade banner — and it has to scroll inside that box rather than grow it.
 * That needs a height with a number behind it, for two reasons:
 *
 * - `flex-1` here would do nothing. This box's parent is the wrapper
 *   `PageTransition` renders, which is a flex item of `main` but is not itself a
 *   flex container, so a `flex-1` child of it gets no share of anything and
 *   falls back to the height of its own content.
 * - `DocsExplorer`'s own root asks for `h-full`, and a percentage height against
 *   a parent whose height is `auto` resolves to `auto` as well. With no number
 *   here, the tree and the reader below it collapse onto their content.
 *
 * `h-full` supplies the number. The wrapper's height is settled by the flex
 * layout of `main`, so 100% of it is the space actually left over, with the
 * banners already subtracted. `h-screen` was the previous answer and overflowed
 * by exactly the height of whichever banners were showing, pushing the reader's
 * bottom chrome below the fold.
 */
export const DOCS_READER_SHELL_CLASS = "flex h-full flex-col";
