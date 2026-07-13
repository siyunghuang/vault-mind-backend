# Python AI Backend — Architecture & Build Spec

## 1. System Overview

```
Svelte Frontend  <-->  Python Backend  <-->  Model Provider (Cloud or Local)
                              |
                              v
                        MCP Server (tools)
```

- Frontend never talks to the model or MCP server directly — everything goes through the Python backend.
- The backend exposes one stable API contract to the frontend, regardless of which model provider is active behind the scenes.
- The model (cloud or local) can call tools exposed by an MCP server; the backend brokers those tool calls.

## 2. Goals

- One consistent API for the Svelte frontend to call, no matter which model is running.
- Swap between a **cloud model** (e.g. Anthropic/OpenAI API) and a **local model** (e.g. via Ollama) — via config or per-request override.
- MCP integration so the model can use external tools/resources.
- Streaming responses back to the frontend (token-by-token).
- Reasonable error handling / fallback if a provider or the MCP server is unavailable.

## 3. Tech Stack (recommended)

- **FastAPI** — async, native SSE/WebSocket support, good for this shape of app
- **uvicorn** — ASGI server
- **pydantic** — request/response schemas
- **mcp** (official Python SDK) — MCP client
- **httpx** or provider SDK (e.g. `anthropic`, `openai`) — cloud calls
- **ollama** python client (or raw HTTP to its API) — local model calls
- **python-dotenv** — env config

## 4. Project Structure

```
backend/
  app/
    main.py                 # FastAPI app, CORS, router mounting
    api/
      chat.py                # POST /api/chat, streaming endpoint
      health.py               # GET /api/health
    core/
      config.py               # env-driven settings (pydantic BaseSettings)
      logging.py
    models/
      base.py                 # ModelProvider interface
      cloud_provider.py       # Cloud implementation
      local_provider.py       # Local implementation
      factory.py               # picks provider based on config/request
    mcp/
      client.py               # MCP client setup, tool listing, tool execution
    schemas/
      chat.py                 # Pydantic request/response models
  .env.example
  requirements.txt
```

## 5. Model Abstraction Layer

Define one interface both providers implement, so the rest of the app doesn't care which is active:

```python
class ModelProvider(Protocol):
    async def generate(self, messages: list[Message], tools: list[Tool]) -> AsyncIterator[Chunk]:
        ...
```

- `CloudProvider` — wraps the cloud API's SDK, forwards MCP tool definitions as native tool-use.
- `LocalProvider` — wraps a local runtime (e.g. Ollama's `/api/chat`). Note: tool-calling support varies a lot by local model — confirm the specific model supports it before assuming MCP tools will work locally (see Open Decisions).
- A `factory.py` picks the provider based on:
  1. Explicit `provider` field in the request (if the frontend lets the user choose), else
  2. `MODEL_PROVIDER` env var default.

## 6. MCP Integration

- On backend startup, the MCP client connects to the configured MCP server (stdio subprocess or SSE/HTTP transport) and lists available tools.
- Tool definitions are converted into the format each `ModelProvider` expects and passed into `generate()`.
- Tool-call loop:
  1. Model responds with a tool-use request.
  2. Backend routes the call through the MCP client to actually execute it.
  3. Result is fed back to the model as a tool result.
  4. Model continues generating until it produces a final answer.
- If the MCP server is unreachable at startup, log a warning and continue without tools rather than failing the whole backend (configurable).

## 7. API Contract (Frontend ↔ Backend)

### `POST /api/chat`

Request:
```json
{
  "message": "string",
  "session_id": "string",
  "provider": "cloud" | "local"   // optional, overrides default
}
```

Response: **Server-Sent Events** stream (simplest for one-directional token streaming; switch to WebSocket later only if you need bidirectional push).

```
data: {"type": "token", "content": "Hel"}

data: {"type": "token", "content": "lo"}

data: {"type": "tool_call", "name": "search", "args": {...}}

data: {"type": "tool_result", "name": "search", "result": {...}}

data: {"type": "done", "usage": {"input_tokens": 123, "output_tokens": 45}}
```

### `GET /api/health`

Returns active provider, provider reachability, and MCP server connection status. Useful for the frontend to show a status indicator.

### `GET /api/models`

Lists available providers/models so the frontend can build a selector (if you want end users to switch model at all).

## 8. Configuration

```env
# .env.example
MODEL_PROVIDER=cloud                # cloud | local

# Cloud
ANTHROPIC_API_KEY=
CLOUD_MODEL=claude-sonnet-4-6

# Local
LOCAL_MODEL_ENDPOINT=http://localhost:11434
LOCAL_MODEL_NAME=llama3

# MCP
MCP_SERVER_COMMAND=                 # for stdio transport
MCP_SERVER_URL=                     # for SSE/HTTP transport
MCP_REQUIRED=false                  # if true, fail startup when MCP is unreachable

# CORS
FRONTEND_ORIGIN=http://localhost:5173
```

## 9. Error Handling & Fallback

- Cloud call fails (auth, rate limit, network) → return a clear error to the frontend; optionally auto-fallback to local if configured.
- Local runtime unreachable → clear error; don't silently fall back to cloud (cost implications) unless explicitly configured to.
- MCP server unreachable → proceed without tools, but flag this in `/api/health` and optionally in the chat response metadata so the frontend can inform the user.

## 10. Implementation Milestones

1. Scaffold FastAPI app + `/api/health`.
2. `CloudProvider` working end-to-end, non-streaming first.
3. Add SSE streaming to `/api/chat`.
4. `LocalProvider` implemented with the same interface.
5. Provider switch logic (env default + per-request override).
6. MCP client wired up, tools listed on startup.
7. Tool-call loop implemented (model ↔ MCP round trip).
8. Connect real Svelte frontend, test end-to-end with both providers.
9. Error handling, fallback behavior, logging.
10. (Optional, later) auth, rate limiting, persistent chat history/session store.

## 11. Open Decisions (fill in before/while building)

- [Both] Cloud provider: Anthropic API? OpenAI? Both?
- [Ollama] Local runtime: Ollama, llama.cpp server, vLLM, LM Studio?
- [Both] Does the chosen local model actually support tool-calling well enough for MCP to work locally, or is MCP effectively cloud-only for now?
- [in-memory for now] Session/history storage: in-memory (fine for dev), Redis, or a DB?
- [local-dev-only] Any auth needed yet, or is this local-dev-only for now?
- [HTTP] MCP transport: local subprocess (stdio) or a remote MCP server (SSE/HTTP)?

## 12. Handoff Checklist

When starting the backend coding session, also provide:
- [ ] This file.
- [ ] The exact `fetch`/`EventSource` code from the Svelte side that will call `/api/chat` (so the response shape matches what the frontend already expects).
- [ ] Frontend dev port / origin (for `FRONTEND_ORIGIN` / CORS).
- [obsidian localhost] Which MCP server you're integrating with, and how it's normally launched (command, or URL).
