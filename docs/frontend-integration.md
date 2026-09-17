# Frontend Integration

For response rendering, SSE handling, and the frontend agent's acceptance checklist, follow [Frontend Response Display Handoff](/home/yung/MyGitRepo/vault-mind-backend/docs/frontend-response-display.md). That document distinguishes frontend presentation fixes from the separate backend work needed for incremental model streaming.

Backend repo path:

```text
/home/yung/MyGitRepo/vault-mind-backend
```

This backend repo does not contain the Svelte app entrypoint. There is no known frontend `app.ts` or `src/routes/+page.svelte` path under `/home/yung/MyGitRepo/vault-mind-backend`.

Frontend helper source file:

```text
/home/yung/MyGitRepo/vault-mind-backend/frontend/api.ts
```

Suggested destination inside the Svelte/Vite frontend app:

```text
<frontend-repo>/src/lib/vaultMindApi.ts
```

Set this env var in the frontend app:

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
```

Copy `/home/yung/MyGitRepo/vault-mind-backend/frontend/api.ts` into `<frontend-repo>/src/lib/vaultMindApi.ts`.

Backend runtime secrets live outside the repo:

```text
/home/yung/.config/vault-mind-backend/runtime.env
```

That file must contain `MODEL_PROVIDER=cloud`, `CLOUD_PROVIDER=nvidia`, `CLOUD_PROVIDER=gemini`, or `CLOUD_PROVIDER=openai`, and the matching server-side API key before the frontend can use cloud chat. The frontend never receives NVIDIA, Gemini, or OpenAI keys.

Backend APIs used by the helper:

```text
GET  http://127.0.0.1:8000/api/health
GET  http://127.0.0.1:8000/api/models
POST http://127.0.0.1:8000/api/chat
GET  http://127.0.0.1:8000/api/mcp/tools
POST http://127.0.0.1:8000/api/mcp/tools/{tool_name}
```

## Health

```ts
import { getHealth } from "$lib/vaultMindApi";

const health = await getHealth();
```

Use `health.mcp.ok` to show Obsidian MCP connection state.

## Chat

```ts
import { getHealth, getModels, streamChat } from "$lib/vaultMindApi";

let answer = "";
const health = await getHealth();
const models = await getModels();
const selected = models.models.find((model) => model.default && model.available) ?? models.models.find((model) => model.available);

await streamChat("hello", { model_id: selected?.id }, (event) => {
if (event.type === "token") answer += event.content ?? "";
if (event.type === "error") console.error(event.code, event.status_code, event.message);
});
```

For a model selector, use `GET http://127.0.0.1:8000/api/models`. Each `models` item has `id`, `provider`, `model`, `available`, and `default`. Send the selected `id` as `model_id` to `POST http://127.0.0.1:8000/api/chat`.

`POST http://127.0.0.1:8000/api/chat` may also stream presentation events before `done`:

```json
{"type":"sources","sources":[{"title":"00-Index.md","path":"00-Index.md","excerpt":"...","tool":"vault_read"}]}
{"type":"sections","sections":[{"title":"Vault Listing","content":"- `00-Index.md`"}]}
```

Frontend implementation steps:

1. Copy updated helper from `/home/yung/MyGitRepo/vault-mind-backend/frontend/api.ts` to `<frontend-repo>/src/lib/vaultMindApi.ts`.
2. Keep appending `event.content` when `event.type === "token"`.
3. Store `event.sources ?? []` when `event.type === "sources"`.
4. Store `event.sections ?? []` when `event.type === "sections"`.
5. Render section content as Markdown inside a collapsed `Vault details` disclosure below the answer; show sources as compact note references. Follow the display handoff for safe links and full-path labels.
6. Do not parse source paths out of markdown response text.

## Obsidian MCP

```ts
import { callMcpTool, getMcpTools } from "$lib/vaultMindApi";

const tools = await getMcpTools();

const result = await callMcpTool("search_simple", {
  query: "vault-mind-backend",
  contextLength: 80,
});
```

When `CHAT_MCP_ENABLED=true` is set in `/home/yung/.config/vault-mind-backend/runtime.env`, cloud chat preloads Obsidian vault context before answering and can auto-call read-only Obsidian MCP tools. Keep `MCP_REQUIRED=true` if chat should fail instead of answering without vault access. Manual MCP endpoints remain available for direct frontend workflows.

### Project Context and Diagnostics

The backend decodes MCP text/structured results before extracting note contents, search paths, and source excerpts. It reads `00-Index.md` on every cloud chat and matches the question against project wikilink labels and capsule directory names. A unique match preloads that project's `README.md` and `state.md`. Ambiguous or unmatched questions receive the index; the model can search with targeted terms or ask for clarification. Preloaded context is limited to four notes and 12,000 characters.

Successful identical read-only tool calls are reused within a chat, including notes read during grounding. The cache is discarded when that chat ends. Manual MCP endpoints continue returning raw MCP envelopes. The existing SSE events and source fields are unchanged.

`POST /api/chat` returns a generated `X-Request-ID` header, exposed through CORS. Frontend code using `fetch` can read `response.headers.get("X-Request-ID")` and include it in a bug report; it matches `request_id` in backend logs. The helper in `/home/yung/MyGitRepo/vault-mind-backend/frontend/api.ts` continues working without changes.

Use `chat_complete` to compare total elapsed time, `tool_executions` (actual tool calls, excluding discovery), `cache_hits`, `ai_rounds`, `notes_extracted` (successful nonempty note extractions), and `context_chars` (the full preload message). `chat_mcp_grounding_complete` includes the number of matching projects. `provider_usage` logs provider-reported input/output token counts for each response, including finalization; absent usage is `unavailable`, not an estimate. Logs outside a chat use `request_id=-`. Note contents and raw user prompts remain excluded from default diagnostics.

## Chat Errors

`POST http://127.0.0.1:8000/api/chat` streams structured error events:

```json
{"type":"error","message":"cloud provider unavailable: HTTP 429","code":"rate_limited","status_code":429}
```

Common `code` values include `rate_limited`, `provider_timeout`, `provider_http_error`, `provider_bad_response`, `mcp_timeout`, and `mcp_unavailable`.

AI timeouts return `provider_timeout` with `status_code: 504`; Obsidian timeouts return `mcp_timeout` with `status_code: 504`. Obsidian connection failures return `mcp_unavailable` with `status_code: 502` (missing MCP configuration uses `503`). Each complete MCP operation has a 30-second deadline. No automatic retries are performed.

The backend sends one `error` event followed by one `done`, then closes the stream. Chat HTTP status remains `200` after streaming starts, so checking `response.ok` alone will not detect these failures. The helper in `/home/yung/MyGitRepo/vault-mind-backend/frontend/api.ts` forwards both events to `onEvent`:

- On `error`, show `event.message`, record the failed state, and clear loading.
- On `done`, clear loading without overwriting the failed state or discarding partial output.
- Catch fetch/stream errors too, since a disconnected browser cannot receive backend events.

Manual `GET /api/mcp/tools` and `POST /api/mcp/tools/{tool_name}` failures use actual HTTP `504`/`502` statuses and a JSON body such as:

```json
{"detail":{"message":"Obsidian request timed out. Please try again.","code":"mcp_timeout","status_code":504}}
```

Docker logs include `mcp_request_failed` with the operation/tool, error code, elapsed milliseconds, and underlying exception types.

## Docker Diagnostics

Watch backend diagnostics:

```bash
docker logs -f vault-mind-backend-local
```

Useful log keys:

```text
chat_start
chat_mcp_grounding_start
chat_mcp_grounding_complete
chat_mcp_tools_loaded
cloud_provider_tool_call_requested
chat_mcp_tool_request
chat_mcp_tool_response
cloud_provider_request
cloud_provider_response
cloud_provider_http_error
chat_error
```

Log lines include timestamps in Docker output.

Request/response payload previews are disabled by default. For temporary local debugging, set these in `/home/yung/.config/vault-mind-backend/runtime.env` and restart Docker:

```env
LOG_PAYLOADS=true
LOG_PAYLOAD_CHARS=1000
```
