# Quickstart

Get the repowise backend and frontend running locally against an already-indexed repo.

## Prerequisites

- **Python 3.11+** managed by [uv](https://docs.astral.sh/uv/)
- **Node.js 18+** with npm
- A **Gemini API key** (or Anthropic/OpenAI — swap the env vars accordingly)
- A repo that has already been indexed with `repowise init` (the `.repowise/` directory exists)

## Test Repo

These instructions use the `interview-coach` repo as the test target:

```
Repo path:  C:\Users\ragha\Desktop\interview-coach
Wiki DB:    C:\Users\ragha\Desktop\interview-coach\.repowise\wiki.db
Provider:   gemini (gemini-3.1-flash-lite-preview)
Embedder:   gemini
```

## 1. Install Dependencies

From the repowise root:

```powershell
cd C:\Users\ragha\Desktop\repowise

# Python packages (all workspace packages)
uv sync

# Node packages (web frontend)
npm install
```

## 2. Start the Backend (port 7337)

Open a PowerShell terminal:

```powershell
cd C:\Users\ragha\Desktop\repowise

$env:REPOWISE_DB_URL = "sqlite+aiosqlite:///C:/Users/ragha/Desktop/interview-coach/.repowise/wiki.db"
$env:GEMINI_API_KEY = "YOUR_GEMINI_API_KEY"
$env:REPOWISE_EMBEDDER = "gemini"

uv run uvicorn repowise.server.app:create_app --factory --host 0.0.0.0 --port 7337 --reload
```

Verify: `http://localhost:7337/health` should return `{"status": "ok"}`.

## 3. Start the Frontend (port 3000)

Open a second PowerShell terminal:

```powershell
cd C:\Users\ragha\Desktop\repowise

$env:REPOWISE_API_URL = "http://localhost:7337"

npm run dev --workspace packages/web
```

Open **http://localhost:3000** in your browser.

## 4. Test the Chat

1. Click your repo on the dashboard
2. You'll see the chat interface with suggestion chips
3. Try: *"Give me an overview of this codebase"*
4. Watch the streaming response with tool calls appearing in real time
5. Click **View** on any tool result to open the artifact panel

## Environment Variables Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `REPOWISE_DB_URL` | Yes | SQLite/PostgreSQL connection string pointing to the indexed wiki DB |
| `GEMINI_API_KEY` | If using Gemini | Google Gemini API key |
| `ANTHROPIC_API_KEY` | If using Anthropic | Anthropic API key |
| `OPENAI_API_KEY` | If using OpenAI | OpenAI API key |
| `REPOWISE_EMBEDDER` | No (default: `mock`) | Embedder for RAG search: `gemini`, `openai`, or `mock` |
| `REPOWISE_API_URL` | Frontend only | Backend URL for Next.js proxy (default: `http://localhost:7337`) |
| `REPOWISE_API_KEY` | No | Optional API key to protect the backend; omit for local dev |

## Switching LLM Providers

The chat model selector in the UI lets you switch providers on the fly. To use a different provider, set its API key env var on the backend and restart. The chat will auto-detect configured providers.

## Troubleshooting

- **"embedder.mock_active" warning** — Set `$env:REPOWISE_EMBEDDER = "gemini"` (or `openai`) for real vector search
- **Empty repo list** — Check `REPOWISE_DB_URL` points to a DB with indexed repos
- **Chat returns 422** — The active provider doesn't have an API key set; check env vars
- **CORS errors** — Backend CORS is open (`*`) by default; ensure both servers are running
