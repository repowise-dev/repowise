# Releasing the VS Code extension

The extension publishes to the Visual Studio Marketplace (publisher
`repowise-dev`) and to Open VSX (for VS Code forks). Releases are driven by the
`publish-vscode.yml` workflow; this doc is the checklist around it.

## Versioning

- The extension version in `package.json` is **independent of the Python
  package**. The only cross-version coupling is the `MIN_SERVER_VERSION`
  constant the extension checks against the running server. Bump it only when
  the extension starts relying on a newer server contract.
- Marketplace pre-releases use the same version number published to the
  pre-release channel. Cut a stable release from a tag once a pre-release has
  been dogfooded.

## When to cut a release (don't let the bundled UI freeze)

The webviews compile `@repowise-dev/ui` (`packages/ui`) into the VSIX at build
time, so shared-component improvements reach extension users only on a rebuild.
The extension source can go untouched for cycles while its bundled UI drifts,
leaving users frozen on whatever `packages/ui` shipped at the last `vscode-v*`
tag. Cut a release whenever `packages/ui` changed in a webview-consumed subpath
since the last extension tag, even if `packages/vscode/` itself did not:

```
LAST_EXT_TAG=$(git tag --list 'vscode-v*' --sort=-v:refname | head -1)
git log $LAST_EXT_TAG..origin/main --oneline -- packages/ui/src/
```

Webview-consumed subpaths: `graph/`, `health/`, `docs/`, `c4/`, `refactoring/`,
`lib/format`, `ui/`, `shared`, `wiki/`. Confirm the live import set with
`git grep -hoE 'from "@repowise-dev/ui[^"]*"' -- 'packages/vscode/webview/**'`.

## The one rule that has bitten us before

**CI (and this checklist) must BUILD, not just type-check.** A barrel
value-import once passed `tsc` and still broke the built bundle (see the web
lesson in `.claude/RELEASING.md`). Both the host esbuild bundle and the Vite
webview bundle must be built and exercised before every publish. The publish
workflow builds both and runs the webview + electron smoke tests for exactly
this reason.

## Pre-publish checklist

Run from the repo root unless noted.

1. `npm run type-check --workspace packages/vscode`
2. `npm run build --workspace packages/vscode` (asserts the host bundle size
   budget)
3. `npm run build:webview --workspace packages/vscode` (review the chunk-size
   report; the largest lazy chunk is ELK, shared by the graph and C4 views)
4. `npm run test:webview --workspace packages/vscode`
5. `npm run test --workspace packages/vscode` (electron smoke; use
   `xvfb-run -a` on Linux)
6. `npm run package --workspace packages/vscode` and note the VSIX size
7. **Clean-profile install smoke test**: install the packaged VSIX into a fresh
   VS Code profile with no `repowise` CLI configured, and confirm:
   - the walkthrough appears and walks install -> index -> first insight -> MCP
   - after setup, a low-health file shows diagnostics and gutter heat
   - a dashboard opens and renders (health or knowledge graph)
   - the Repowise MCP tools are listed in agent mode
8. **Fork check (optional but recommended)**: install the VSIX in Cursor and
   confirm the MCP tools load via the `.vscode/mcp.json` fallback written by
   **Repowise: Configure MCP for this Workspace**.

## Publishing

Secrets required in the repository (Settings -> Secrets -> Actions):

- `VSCE_PAT` - a Marketplace Personal Access Token for the `repowise-dev`
  publisher.
- `OVSX_PAT` - an Open VSX access token.

### Pre-release (from main)

Run the **Publish VS Code extension** workflow via `workflow_dispatch` with the
default (pre-release checked). It builds, tests, packages with `--pre-release`,
and publishes to both marketplaces.

### Stable

Bump `packages/vscode/package.json` version, commit, then push a tag:

```
git tag vscode-v0.1.0
git push origin vscode-v0.1.0
```

The tag push triggers the workflow and publishes a stable build to both
marketplaces.

## After publishing

- Verify the listing on the Marketplace and Open VSX (icon, gallery banner,
  README render, screenshots).
- Install the published build from the Marketplace into a clean profile once
  more as a final confirmation.

## Notes

- No plan or phase references, competitor names, AI attribution, or em dashes in
  the published README, CHANGELOG, or listing copy.
- Screenshots and GIFs go under `media/screenshots` and are linked from the
  README before the first stable release.
