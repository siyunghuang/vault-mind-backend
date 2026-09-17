// Source: /home/yung/MyGitRepo/vault-mind-backend/frontend/api.ts
// Suggested frontend destination: <frontend-repo>/src/lib/vaultMindApi.ts

const API_BASE =
  (import.meta as unknown as { env?: { VITE_API_BASE_URL?: string } }).env
    ?.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

export type Provider = "cloud" | "local";
export type ModelChoice = {
  id: string;
  provider: "local" | "nvidia" | "gemini" | "openai";
  model: string;
  available: boolean;
  default: boolean;
};
export type ModelsResponse = {
  default: Provider;
  default_model_id: string;
  models: ModelChoice[];
  providers: unknown[];
};
export type ChatOptions =
  | Provider
  | {
      provider?: Provider;
      model_id?: string;
    }
  | undefined;

export type VaultSource = {
  title: string;
  path: string;
  excerpt?: string;
  tool?: string;
};

export type VaultSection = {
  title: string;
  content: string;
};

export type ChatEvent = {
  type: "token" | "tool_call" | "tool_result" | "sources" | "sections" | "error" | "done" | string;
  content?: string;
  sources?: VaultSource[];
  sections?: VaultSection[];
  message?: string;
  code?: string;
  status_code?: number;
};

export type Health = {
  ok: boolean;
  provider: Provider;
  provider_status?: { ok?: boolean; [key: string]: unknown };
  mcp?: { ok?: boolean; configured?: boolean; tools?: string[]; [key: string]: unknown };
};

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) throw new Error(await response.text());
  return response.json() as Promise<T>;
}

export function getHealth() {
  return json<Health>("/api/health");
}

export function getModels() {
  return json<ModelsResponse>("/api/models");
}

export function getMcpTools() {
  return json<{ tools: unknown[] }>("/api/mcp/tools");
}

export function callMcpTool(toolName: string, args: Record<string, unknown>) {
  return json<Record<string, unknown>>(`/api/mcp/tools/${toolName}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(args),
  });
}

export async function streamChat(
  message: string,
  options: ChatOptions,
  onEvent: (event: ChatEvent) => void,
) {
  const request = typeof options === "string" ? { message, provider: options } : { message, ...options };
  const response = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  if (!response.ok || !response.body) throw new Error(await response.text());

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";

    for (const raw of events) {
      const line = raw.split("\n").find((item) => item.startsWith("data: "));
      if (line) onEvent(JSON.parse(line.slice(6)) as ChatEvent);
    }
  }
}
