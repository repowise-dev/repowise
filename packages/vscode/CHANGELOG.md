# Changelog

Notable changes to the Repowise VS Code extension. The extension versions
independently of the `repowise` Python package; the only cross-version coupling
is the minimum server version it checks against.

This file starts at 0.7.0. Earlier releases are described in the repository's
release history.

## 0.7.0

The webviews compile the shared Repowise UI components at build time, so most of
what changed here arrived with a rebuild rather than with edits to the extension
itself. Everything below is visible in the editor.

### Docs

- The tree reads as a table of contents rather than a directory listing. Every
  group on the top rung opens on load and nothing below it does, so the first
  screen is the outline: the layers, the orientation chapters and the way into
  the file corpus. Expanding every rung used to put roughly ninety module rows
  in front of you once layers grouped every module in the repo; limiting the
  depth holds that back without hiding the outline to do it.
- The file corpus closes the tree, still collapsed. It is a reference rather
  than a section, and its row count was setting the shape of what you met first.
- Selecting a page opens the chapters above it, so a deep link no longer
  highlights a row inside a collapsed parent.
- Outline group rows drop their folder glyph and their trailing graph link. Rows
  with no page behind them were the one place the no-icons rule never reached.
- An unclaimed module no longer poses as a layer, a top-level row shows its
  count only when the number is meaningful, and modules group under the layer
  their members belong to.
- Diagrams scale to the column they are read in. They rendered at natural size
  inside a scroll box, so an architecture map arrived clipped mid-subgraph with
  two scrollbars over it. Width only, never enlarged past 1:1, and the height is
  reserved so a fitted diagram does not sit in a pool of empty space. Maximize
  still pans and zooms for the dense ones.
- The docs page no longer loads every page's body to draw the tree. On a large
  repository that was several sequential round trips and tens of megabytes of
  text the tree never renders.
- One breadcrumb instead of two, correct reader height, wrapping backlinks, and
  links that land where their label says.
- The command palette answers a keypress once rather than racing itself.

### Architecture

- One scope control instead of two. Scope used to live in both the tab strip and
  a floating pill cluster, with the same option appearing twice on one screen.
  Tabs select the dataset now and scope is a single labelled control.
- The canvas stopped doing per-node and per-frame work it did not need, which is
  most of the several seconds the view used to spend arriving.
- The view no longer blames the backend while data is still loading.
- Structurizr DSL export, built on typed node ids.

### Health and refactoring

- Code Health rebuilt on the section design language: a sentence above each
  figure saying what the figure means, hairlines instead of a grid of
  near-identical bordered cards.
- The refactoring view leads with what the plan pile actually is. The
  priority-by-effort quadrant plotted a small fraction of plans because most rate
  small effort and the axis was one column; it is replaced with charts whose axes
  spread.

### Graph and decisions

- Graph canvas performance work from the architecture pass applies here too.
- Decisions rebuilt on the section design language.

### Notes

- Requires a Repowise server at 0.26.0 or newer, unchanged from 0.6.0.
