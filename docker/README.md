# Docker

Two images are provided:

- **`Dockerfile`** runs the full repowise stack (API + Web UI) in a single container.
- **`Dockerfile.mcp`** runs only the MCP server over stdio, for MCP hosts and
  directories (see [MCP server image](#mcp-server-image) below).

Run the full repowise stack (API + Web UI) in a single container.

## Prerequisites

- Docker installed
- A repository already indexed with `repowise init` (the `.repowise/` directory exists)

## Quick Start

```bash
# Run from the project root
docker build -t repowise -f docker/Dockerfile .

# Run with a mounted .repowise directory
docker run -p 7337:7337 -p 3000:3000 \
  -v /path/to/your-repo/.repowise:/data \
  -e GEMINI_API_KEY=your-key \
  -e REPOWISE_EMBEDDER=gemini \
  repowise
```

Open http://localhost:3000 for the Web UI, http://localhost:7337 for the API.

## Docker Compose

```bash
# Set the path to your .repowise directory
export REPOWISE_DATA=/path/to/your-repo/.repowise
export GEMINI_API_KEY=your-key

docker compose up
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `REPOWISE_DB_URL` | `sqlite+aiosqlite:////data/wiki.db` | Database URL |
| `REPOWISE_EMBEDDER` | `mock` | Embedder: `gemini`, `openai`, `mock` |
| `ANTHROPIC_API_KEY` | — | Anthropic API key (for chat) |
| `OPENAI_API_KEY` | — | OpenAI API key (for chat) |
| `GEMINI_API_KEY` | — | Gemini API key (for chat + embeddings) |
| `PORT_BACKEND` | `7337` | API server port |
| `PORT_FRONTEND` | `3000` | Web UI port |

## MCP server image

`Dockerfile.mcp` is a lean image that runs only the MCP server, speaking
JSON-RPC over stdio. It carries no Web UI and exposes no ports. This is the
image to point MCP hosts and directories at.

```bash
# Build
docker build -t repowise-mcp -f docker/Dockerfile.mcp .

# Run against a repo (mount it and set it as the working dir).
# tools/list works without an index; the query tools serve a mounted .repowise.
docker run --rm -i -v /path/to/your/repo:/repo -w /repo repowise-mcp
```

The container communicates over stdin/stdout, so run it with `-i` and no `-t`.
