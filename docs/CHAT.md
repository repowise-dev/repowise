# Codebase Chat — Technical Reference

The codebase chat feature lets users have an interactive conversation with their
codebase. The agent uses whichever LLM provider the user has configured, has
access to all 8 MCP tools, and streams responses back to the browser in real time
showing tool calls as they happen and rendering results in an artifact panel.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Database Schema](#2-database-schema)
3. [ChatProvider Protocol](#3-chatprovider-protocol)
4. [Tool Registry](#4-tool-registry)
5. [Provider Configuration](#5-provider-configuration)
6. [SSE Streaming Protocol](#6-sse-streaming-protocol)
7. [Agentic Loop](#7-agentic-loop)
8. [REST API Endpoints](#8-rest-api-endpoints)
9. [Frontend Architecture](#9-frontend-architecture)
10. [Provider-Specific Notes](#10-provider-specific-notes)

---

## 1. Architecture Overview

```
User types question
        |
        v
POST /api/repos/{repo_id}/chat/messages
        |
        v
+------ Chat Router (SSE stream) ------+
|                                       |
|  1. Create/load conversation          |
|  2. Save user message to DB           |
|  3. Build LLM message history         |
|  4. Call provider.stream_chat()  <----+---- tool_executor callback
|        |                              |
|        v                              |
|  5. Stream text_delta events -------> SSE to browser
|  6. On tool_start:                    |
|     - Execute tool (or provider       |
|       executes internally)            |
|     - Emit tool_result event -------> SSE to browser
|  7. If tool calls found:             |
|     - Append to history, loop to 4   |
|  8. If no tool calls:                |
|     - Save assistant message to DB   |
|     - Emit done event                |
+---------------------------------------+
```

The agentic loop is in the chat router for most providers (OpenAI, Anthropic,
Ollama, LiteLLM). For Gemini, the loop runs inside `stream_chat()` using native
Content objects to preserve thought signatures. The router passes a
`tool_executor` callback that Gemini calls internally.

---

## 2. Database Schema

Two tables added in migration `0005_chat_conversations.py`:

### `conversations`

| Column | Type | Notes |
|--------|------|-------|
| `id` | `String(32)` PK | UUID hex |
| `repository_id` | `String(32)` FK | CASCADE delete |
| `title` | `Text` | Auto-generated from first 6 words |
| `created_at` | `DateTime(tz)` | |
| `updated_at` | `DateTime(tz)` | Auto-updated on new messages |

**Index:** `ix_conversations_repo_updated` on `(repository_id, updated_at)`

### `chat_messages`

| Column | Type | Notes |
|--------|------|-------|
| `id` | `String(32)` PK | UUID hex |
| `conversation_id` | `String(32)` FK | CASCADE delete |
| `role` | `String(32)` | `user` or `assistant` |
| `content_json` | `Text` | JSON blob (see below) |
| `created_at` | `DateTime(tz)` | |

**Index:** `ix_chat_messages_conv_created` on `(conversation_id, created_at)`

### Message content format

**User messages:**
```json
{"text": "What does the auth module do?"}
```

**Assistant messages:**
```json
{
  "text": "The auth module handles...",
  "tool_calls": [
    {
      "id": "call_abc123",
      "name": "get_context",
      "arguments": {"targets": ["src/auth"]},
      "result": { ... }
    }
  ]
}
```

---

## 3. ChatProvider Protocol

Defined in `packages/core/src/wikicode/core/providers/base.py`.

The existing `BaseProvider.generate()` is untouched. A new `ChatProvider`
protocol class (using `typing.Protocol` + `@runtime_checkable`) adds streaming
chat with tool use as an opt-in capability.

```python
@runtime_checkable
class ChatProvider(Protocol):
    def stream_chat(
        self,
        messages: list[dict],          # OpenAI-format message list
        tools: list[dict],             # OpenAI-format tool definitions
        system_prompt: str,
        max_tokens: int = 8192,
        temperature: float = 0.7,
        request_id: str | None = None,
        tool_executor: Any | None = None,  # async callable(name, args) -> dict
    ) -> AsyncIterator[ChatStreamEvent]: ...
```

**Supporting dataclasses:**

- `ChatToolCall(id, name, arguments)` — a tool call the LLM wants to make
- `ChatStreamEvent(type, text?, tool_call?, tool_result_data?, stop_reason?, input_tokens, output_tokens)` — a single event in the stream

**Event types:**

| `type` | Populated fields | Meaning |
|--------|-----------------|---------|
| `text_delta` | `text` | Incremental text token(s) |
| `tool_start` | `tool_call` | LLM wants to call a tool |
| `tool_result` | `tool_call`, `tool_result_data` | Tool executed (by provider internally) |
| `usage` | `input_tokens`, `output_tokens` | Token usage update |
| `stop` | `stop_reason` | Generation ended (`end_turn`, `tool_use`, `max_tokens`) |

**Implementations:** Anthropic, OpenAI, Gemini, Ollama, LiteLLM. All accept the
`tool_executor` parameter; only Gemini uses it (for thought signature handling).

---

## 4. Tool Registry

Defined in `packages/server/src/wikicode/server/chat_tools.py`.

Single source of truth for tool schemas and execution. Imports the 8 MCP tool
functions directly from `wikicode.server.mcp_server`.

```python
TOOL_REGISTRY: dict[str, ToolDef]  # name -> ToolDef(name, description, parameters, function, artifact_type)
```

**Key functions:**

| Function | Purpose |
|----------|---------|
| `get_tool_schemas_for_llm()` | Returns OpenAI-format tool definitions for the LLM |
| `execute_tool(name, args)` | Runs a tool and ensures JSON-serializable output |
| `get_artifact_type(name)` | Maps tool name to frontend artifact type |
| `init_tool_state(...)` | Bridges FastAPI app state to MCP module globals |

**Tool to artifact type mapping:**

| Tool | Artifact Type |
|------|--------------|
| `get_overview` | `overview` |
| `get_context` | `wiki_page` |
| `get_risk` | `risk_report` |
| `get_why` | `decisions` |
| `search_codebase` | `search_results` |
| `get_dependency_path` | `graph` |
| `get_dead_code` | `dead_code` |
| `get_architecture_diagram` | `diagram` |

---

## 5. Provider Configuration

Defined in `packages/server/src/wikicode/server/provider_config.py`.

API keys and active provider/model selection are stored in a server-side
`provider_config.json` file. Environment variables take precedence over stored
keys.

**Resolution order for API keys:**
1. Environment variable (e.g. `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`)
2. Stored key in `provider_config.json`

**Active provider resolution:**
1. Explicitly set via `PATCH /api/providers/active`
2. Auto-detect from first configured provider

**Provider catalog:** Gemini, Anthropic, OpenAI, Ollama (local, no key), LiteLLM.

---

## 6. SSE Streaming Protocol

The chat endpoint returns `Content-Type: text/event-stream`. Each event is:

```
event: data
data: {"type": "...", ...}

```

**Event shapes:**

```jsonc
// Incremental text from the LLM
{"type": "text_delta", "text": "The auth module..."}

// LLM wants to call a tool
{"type": "tool_start", "tool_id": "call_123", "tool_name": "get_context", "input": {"targets": ["src/auth"]}}

// Tool execution completed
{"type": "tool_result", "tool_id": "call_123", "tool_name": "get_context", "summary": "Context for 1 target(s)", "artifact": {"type": "wiki_page", "data": {...}}}

// Stream complete
{"type": "done", "conversation_id": "abc123", "message_id": "def456"}

// Error
{"type": "error", "message": "Provider error: ..."}
```

**Headers:** `Cache-Control: no-cache`, `X-Accel-Buffering: no`, `Connection: keep-alive`

**Retry:** `retry: 3000` sent at stream start.

---

## 7. Agentic Loop

The loop runs up to 10 iterations per request.

```
for each iteration:
    1. Call provider.stream_chat(messages, tools, system_prompt, tool_executor)
    2. Collect text_delta events -> stream to client
    3. Collect tool_start events -> stream to client
    4. Collect tool_result events (from internal execution) -> stream to client
    5. If there are pending tool calls (not internally executed):
       a. Execute each tool
       b. Emit tool_result to client
       c. Append assistant + tool results to message history
       d. Continue loop
    6. If no tool calls: break
```

After the loop, the assistant message (text + all tool calls with results) is
saved to the database and a `done` event is emitted.

---

## 8. REST API Endpoints

### Chat

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/repos/{repo_id}/chat/messages` | SSE stream — send a message and get a streaming response |
| `GET` | `/api/repos/{repo_id}/chat/conversations` | List conversations for a repo |
| `GET` | `/api/repos/{repo_id}/chat/conversations/{id}` | Get conversation with all messages |
| `DELETE` | `/api/repos/{repo_id}/chat/conversations/{id}` | Delete a conversation |

**POST body:**
```json
{
  "message": "What does the auth module do?",
  "conversation_id": null,
  "provider": null,
  "model": null
}
```

`conversation_id` — omit or `null` to start a new conversation.
`provider` / `model` — optional per-request overrides.

### Providers

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/providers` | List all providers with status and active selection |
| `PATCH` | `/api/providers/active` | Set active provider and model |
| `POST` | `/api/providers/{id}/key` | Store an API key |
| `DELETE` | `/api/providers/{id}/key` | Remove an API key |

---

## 9. Frontend Architecture

### API Layer (`src/lib/api/`)

- `chat.ts` — `listConversations`, `getConversation`, `deleteConversation`, `postChatMessage` (returns raw Response for SSE reading)
- `providers.ts` — `getProviders`, `setActiveProvider`, `addProviderKey`, `removeProviderKey`

### Hooks (`src/lib/hooks/`)

- `useChat(repoId)` — full chat state machine. Uses `fetch` + `ReadableStream` (not `EventSource`, which is GET-only). Manages messages, streaming state, conversation ID, error handling, and abort control. Exposes `sendMessage`, `loadConversation`, `reset`.
- `useProviders()` — SWR wrapper for provider management. Exposes `providers`, `activeProvider`, `activeModel`, `activate`, `saveKey`, `removeKey`.

### Components (`src/components/chat/`)

| Component | Purpose |
|-----------|---------|
| `ChatInterface` | Main container — empty state (greeting + suggestions + model selector) and active state (message list + input) |
| `ChatMessage` | Renders user bubble or assistant message (tool blocks + markdown) |
| `ChatMarkdown` | Client-side markdown renderer using `react-markdown` + `remark-gfm` with design token styling |
| `ToolCallBlock` | Inline tool call visualization — running (spinner), done collapsed (checkmark + summary), done expanded (input/output JSON) |
| `ArtifactPanel` | Right slide-in panel with tabs for multiple artifacts. Renders by type: markdown, Mermaid diagrams, search results, raw JSON |
| `ModelSelector` | Compact popover for switching provider/model and adding API keys inline |
| `ConversationHistory` | Dropdown listing past conversations with delete and new-conversation actions |

### Page Structure

The repo landing page (`/repos/[id]`) is the chat interface:
- Compact header (repo name + commit badge + branch badge)
- `ChatInterface` filling remaining viewport height
- Sidebar nav item updated from "Overview" to "Chat"

All other repo sub-pages (graph, wiki, coverage, etc.) are unchanged.

---

## 10. Provider-Specific Notes

### Anthropic

Uses `client.messages.stream()` with native Anthropic message format. Converts
OpenAI-format messages to Anthropic format (tool results as `user` role with
`tool_result` content blocks, tool calls as `tool_use` content blocks). The
agentic loop runs in the chat router.

### OpenAI

Uses `client.chat.completions.create(stream=True)`. Native OpenAI format —
minimal conversion needed. Tool call fragments are accumulated across stream
chunks and emitted as complete `tool_start` events. The agentic loop runs in
the chat router.

### Gemini

Uses `client.models.generate_content()` (non-streaming, in a thread pool).
**Runs the agentic loop internally** via the `tool_executor` callback to
preserve `thought_signature` on function call parts. Gemini's API requires
these signatures when replaying function calls in conversation history; the
OpenAI-format round-trip through the router would lose them. Native `Content`
objects are used throughout the internal loop.

### Ollama

Uses the OpenAI-compatible endpoint (`localhost:11434/v1`) via `AsyncOpenAI`.
Same streaming pattern as OpenAI. The agentic loop runs in the chat router.

### LiteLLM

Uses `litellm.acompletion(stream=True)`. OpenAI-compatible streaming. The
agentic loop runs in the chat router.
