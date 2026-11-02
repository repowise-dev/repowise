# repowise web UI

The local dashboard: a Next.js app that renders the wiki, graph, health, dead
code, decisions and the other views for an indexed repository. `repowise serve`
downloads a prebuilt copy on first run, caches it, and runs it on port 3000.

## Where it sits

This is the `repowise-web` npm workspace in the repowise monorepo. Pages live in
`src/app/`, app-specific components in `src/components/`, and the API client in
`src/lib/api/`. Shared components come from `packages/ui`, shared types from
`packages/types`, and the typed client from `packages/api-client`. It reads
everything from the API in `packages/server`.

## Install the product

```bash
uv tool install repowise      # or: pipx install repowise / pip install repowise
cd your-repo
repowise init
repowise serve                # API on :7337, web UI on :3000
```

## Develop on it

```bash
npm install                   # from the repository root (npm workspaces)
repowise serve --no-ui        # start only the API on :7337
npm run dev                   # web UI with hot reload on :3000
```

The server-side API address defaults to `http://localhost:7337`; set
`REPOWISE_API_URL` to point elsewhere. See
[CONTRIBUTING.md](../../.github/CONTRIBUTING.md).

## Documentation

- CLI commands, including `serve`: [docs/reference/CLI_REFERENCE.md](../../docs/reference/CLI_REFERENCE.md)
- How the system is built: [docs/architecture/](../../docs/architecture/)
