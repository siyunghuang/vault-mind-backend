# Frontend Integration

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

That file must contain `MODEL_PROVIDER=cloud`, `CLOUD_PROVIDER=nvidia` or `CLOUD_PROVIDER=gemini`, and the matching server-side API key before the frontend can use cloud chat. The frontend never receives NVIDIA or Gemini keys.

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
5. Render sections below the answer and sources as clickable note references using the displayed `path`.
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

When `CHAT_MCP_ENABLED=true` is set in `/home/yung/.config/vault-mind-backend/runtime.env`, cloud chat can auto-call read-only Obsidian MCP tools. Manual MCP endpoints remain available for direct frontend workflows.

## Chat Errors

`POST http://127.0.0.1:8000/api/chat` streams structured error events:

```json
{"type":"error","message":"cloud provider unavailable: HTTP 429","code":"rate_limited","status_code":429}
```

Common `code` values include `rate_limited`, `provider_timeout`, `provider_http_error`, `provider_bad_response`, `mcp_unavailable`, and `mcp_tool_failed`.

## Docker Diagnostics

Watch backend diagnostics:

```bash
docker logs -f vault-mind-backend-local
```

Useful log keys:

```text
chat_start
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
