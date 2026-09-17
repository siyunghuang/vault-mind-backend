# Runtime Env

Persistent local secrets/config live outside this repo:

```text
/home/yung/.config/vault-mind-backend/runtime.env
```

Required variable names:

```env
LOG_LEVEL=INFO
LOG_PAYLOADS=false
LOG_PAYLOAD_CHARS=1000
CHAT_MCP_MAX_ROUNDS=3
MODEL_PROVIDER=cloud
CLOUD_PROVIDER=nvidia
NVIDIA_API_KEY=<secret>
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
CLOUD_MODEL=minimaxai/minimax-m3
NVIDIA_MODELS=minimaxai/minimax-m3

GEMINI_API_KEY=<secret>
GEMINI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
GEMINI_MODEL=gemini-3.5-flash
GEMINI_MODELS=gemini-3.5-flash

OPENAI_API_KEY=<secret>
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-5.4-mini
OPENAI_MODELS=gpt-5.4-mini

LOCAL_MODELS=llama3
DEFAULT_MODEL_ID=

MCP_SERVER_URL=https://127.0.0.1:27124/mcp/
MCP_API_KEY=<secret>
MCP_VERIFY_SSL=false
MCP_REQUIRED=true
CHAT_MCP_ENABLED=true

FRONTEND_ORIGIN=http://localhost:5173
```

Run with Compose:

```bash
docker compose up -d --build --force-recreate
```

Stop:

```bash
docker compose down
```

Do not commit or paste the real env values.

Keep `LOG_PAYLOADS=false` for normal runs. Set `LOG_PAYLOADS=true` only during short local debugging sessions; payload previews are redacted and truncated, but may still include prompt or vault content.

Keep `MCP_REQUIRED=true` when chat must be vault-grounded.

Keep `CHAT_MCP_MAX_ROUNDS=3` by default. Increase only when a prompt truly needs deeper vault exploration.
