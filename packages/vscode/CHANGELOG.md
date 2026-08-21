# Changelog

Notable changes to the Repowise VS Code extension. The extension versions
independently of the `repowise` Python package; the only cross-version coupling
is the minimum server version it checks against.

This file starts at 0.7.0. Earlier releases are described in the repository's
release history.

## 0.8.0

The views are on the current design language, and the risk panel now leads with
where a change lands rather than with how big it is. Much of the rest arrived
with a rebuild: the webviews compile the shared Repowise UI at build time, so
improvements to the shared components ship here when the extension is rebuilt.

**Requires repowise 0.43.0 or newer.** The risk panel reads a payload field no
earlier server sends, so the status bar flags an older one and asks you to
upgrade with `pip install --upgrade repowise`.

### Design language

- Two local copies of the health score colour called anything at or above 7.5
  healthy-green, while the canonical bands start Healthy at 8 and the shared
  ramp paints 7.5 to 7.99 amber. The health panel therefore printed a file's
  score in green beside a map colouring the same file amber. Both copies are
  gone, so the figure, the map and the web app's file table now agree.
- High-contrast themes get a high-contrast treatment. They used to fall through
  to the ordinary ramps, so a user who explicitly asked for high contrast got
  0.07-alpha hairlines and ordinary tertiary text. The brand palette stays:
  editor greys have no vocabulary for a health band, and remapping would trade
  contrast for making the map unreadable as data.
- Health takes the same lede, map, lens switcher, legend and trend the web page
  leads with. Churn is a lens over the map rather than its own quadrant chart,
  fetched on selection and joined onto the map rows by path.
- Settings takes the shared rows, switches, inputs and save indicator, and
  stamps each write so a slow response cannot undo a newer one. The optimistic
  update and its rollback are unchanged.
- The risk view's thirty card wrappers, none of them clickable, become
  hairlines and vertical rhythm, with the score on the shared page lede. The
  home sidebar's two statistics lose their card chrome; the seven launchers
  keep theirs, because those are navigation buttons.
- Decisions takes the shared status mark, whose `proposed` colour differed from
  the local copy's.

### Change risk

- The panel leads with the repo-relative ranking. The classification and
  percentile are the headline, the raw 0-10 score drops to a secondary line
  that names the corpus it is anchored to, and a new note says which of the
  touched files have broken before and where that sits among the repo's own
  fix-bearing files. That is the signal the score cannot see: the score
  restates diff size, so a small edit to a file that keeps breaking used to
  read safer than a large mechanical change to files that never have.
- `probability` is gone from the payload. It was the score divided by ten with
  extra decimal places, presented as a second, more precise-looking number for
  the same quantity.
- The language-model tool description says what the tool now returns, so an
  agent asking for it is told to read the fix history before the score.

### Decisions

- Dismiss dismisses. The button sent "deprecated", which the engine keeps
  re-deriving on every re-index, so dismissing a wrong proposal did not stick.
  A Dismissed filter comes with it, without which a dismissal is a one-way door
  with nothing able to show or undo it.

### Docs

- The low-confidence banner is gone from pages that were never uncertain. One
  confidence value meant two things: a page whose provider call failed, and the
  page an index built without an API key renders deterministically on purpose.
  On a keyless index the banner therefore landed on the repository overview and
  every subsystem page, about content assembled entirely from the parse, the
  import graph and git history. A page that really did lose its prose to a
  provider error now says so in one line at the end of the content, beside the
  affordance that fixes it, with no alarm colour. It reads correctly against a
  wiki published before this change, with no reindex.

### Refactoring

- Performance plans use the same refactoring webview, tree and CodeLens as
  structural plans. Priority comes from the canonical shared contract, and the
  drawer renders validation basis, provenance, true totals, capped tests and
  commands through shared UI components. Missing fields from older servers
  degrade cleanly; the extension does not import web code or execute a plan.

- An extract-helper plan names a directory as its destination. It used to
  prefer a graph community label, which named a directory the duplicated code
  did not live in: censused over 963 stored plans, all 905 that carried a label
  pointed somewhere none of the occurrences were. Plans stored before the fix
  still render, falling back to the old field only when there is no directory.

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
