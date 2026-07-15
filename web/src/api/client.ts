/** Typed API client for Prompter backend. */

// ---- Types ----

export interface HealthServices {
  ollama?: { status: string; error?: string };
  qdrant?: { status: string; error?: string };
  litellm?: { status: string; error?: string };
  remote_provider?: { status: string; error?: string };
}

export interface HealthResponse {
  /** Phase 1 health shape */
  status?: "healthy" | "degraded" | "down";
  services?: HealthServices;
  /** Legacy LM Studio shape (optional fallback) */
  ok?: boolean;
  mode?: string;
  base_url?: string;
  model?: string;
  model_loaded?: boolean;
  message?: string;
  available_models?: string[];
}

export interface ProjectSummary {
  id: string;
  name: string;
  created_at: string;
  file_count: number;
  thread_count: number;
}

export interface ProjectDetail extends ProjectSummary {
  system_prompt: string;
  config: Record<string, unknown>;
  docs_path: string;
  instructions_path: string;
}

export interface ThreadSummary {
  id: string;
  title: string | null;
  created_at: string;
  message_count: number;
}

export interface MessageRecord {
  role: "user" | "assistant" | "system";
  content: string;
  created_at: string;
  model?: string;
}

export interface DocFileInfo {
  name: string;
  size: number;
  mtime: number;
  enabled: boolean;
  ingested: boolean;
}

export interface SourcesState {
  files: DocFileInfo[];
  default_new_enabled: boolean;
}

export interface InstructionsResponse {
  content: string;
}

export interface SyncResponse {
  chunk_count: number;
}

// ---- Phase 1 settings schema ----

export type IntentLabel =
  | "general_chat"
  | "web_search"
  | "deep_research"
  | "coding_basic"
  | "coding_advanced"
  | "bash"
  | "pdf_gen"
  | "file_ops"
  | "vision"
  | "reasoning_medium"
  | "reasoning_heavy";

export interface ModelsConfig {
  general_chat: string;
  web_search: string;
  deep_research: string;
  coding_basic: string;
  coding_advanced: string;
  bash: string;
  pdf_gen: string;
  file_ops: string;
  vision: string;
  reasoning_medium: string;
  reasoning_heavy: string;
}

export interface RoutingRule {
  patterns: string[];
  intent: IntentLabel;
  tools: string[];
}

export interface RouterSettings {
  classifier: string;
  classifier_prompt: string;
  rules_enabled: boolean;
  rules: RoutingRule[];
}

export interface AssistantSettings {
  system_prompt: string;
}

export interface EmbeddingSettings {
  model: string;
  max_tokens: number;
}

export interface SearchProviders {
  web_search: string;
  deep_research: string;
}

export interface SearchSettings {
  providers: SearchProviders;
  tavily_api_key: string;
  tavily_api_key_set?: boolean;
}

export interface OllamaSettings {
  base_url: string;
  keep_alive: number;
  scheduler_enabled: boolean;
}

export interface VisionSettings {
  local_model: string;
  remote_model: string;
}

export interface OpenCodeGoSettings {
  base_url: string;
  api_key: string;
  api_key_set?: boolean;
  enabled: boolean;
}

export interface QdrantSettings {
  url: string;
}

export interface RagSettings {
  chunk_size: number;
  chunk_overlap_ratio: number;
  top_k: number;
}

export interface HealthSettings {
  classifier_timeout_s: number;
  ollama_fallback_to_remote: boolean;
}

export interface LoggingSubsystems {
  router: boolean;
  scheduler: boolean;
  embedding: boolean;
  search: boolean;
  rag: boolean;
  orchestrator: boolean;
}

export interface LoggingSettings {
  level: string;
  file_enabled: boolean;
  subsystems: LoggingSubsystems;
}

export interface DebugSettings {
  router_decisions: boolean;
  sse_trace: boolean;
}

export interface SettingsSnapshot {
  models: ModelsConfig;
  ollama_model_names: Record<string, string>;
  vision: VisionSettings;
  router: RouterSettings;
  assistant?: AssistantSettings;
  embedding: EmbeddingSettings;
  search: SearchSettings;
  ollama: OllamaSettings;
  opencode_go: OpenCodeGoSettings;
  qdrant: QdrantSettings;
  rag: RagSettings;
  health: HealthSettings;
  logging: LoggingSettings;
  debug: DebugSettings;
}

export type SettingsUpdate = {
  [K in keyof SettingsSnapshot]?: SettingsSnapshot[K] extends object
    ? Partial<SettingsSnapshot[K]>
    : SettingsSnapshot[K];
};

// ---- SSE streaming chat ----

export interface SourceChunk {
  text: string;
  source: string;
  source_file: string;
  title: string;
  score: number;
  metadata: Record<string, unknown>;
}

export interface ChatStreamRequest {
  content: string;
  model_override?: string;
  enabled_tools?: Record<string, boolean>;
}

export type TodoItem = {
  content: string;
  status: "pending" | "in_progress" | "completed";
  id: string;
};

export type SseEvent =
  | { type: "chunk"; content: string }
  | { type: "tool_call"; name: string; input: Record<string, unknown> }
  | { type: "tool_result"; name: string; output: string }
  | { type: "sources"; chunks: SourceChunk[] }
  | { type: "model_loading"; model: string; estimated_seconds: number }
  | { type: "routed"; model: string; intent: string }
  | { type: "debug"; stage: string; data: Record<string, unknown> }
  | { type: "ask_user"; question: string; options: string[] }
  | { type: "todos"; todos: TodoItem[] }
  | { type: "done"; usage: Record<string, unknown>; model?: string }
  | { type: "error"; message: string };

export class StreamChatError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "StreamChatError";
  }
}

function parseSseFrame(frame: string): SseEvent | null {
  for (const line of frame.split("\n")) {
    if (!line.startsWith("data: ")) continue;
    const payload = line.slice(6).trim();
    if (!payload) return null;
    try {
      return JSON.parse(payload) as SseEvent;
    } catch {
      return { type: "error", message: "Invalid SSE JSON frame" };
    }
  }
  return null;
}

/**
 * POST SSE chat stream. Yields typed events until the stream ends or is aborted.
 */
export async function* streamChat(
  projectId: string,
  threadId: string,
  body: ChatStreamRequest,
  signal?: AbortSignal,
): AsyncGenerator<SseEvent, void, undefined> {
  const res = await fetch(`/api/chat/${projectId}/${threadId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const errBody = await res.json();
      const raw = errBody.detail ?? errBody.message ?? detail;
      if (typeof raw === "string") detail = raw;
      else if (Array.isArray(raw)) {
        detail = raw.map((x: { msg?: string }) => x.msg ?? String(x)).join("; ");
      }
    } catch {
      // ignore
    }
    throw new StreamChatError(res.status, detail);
  }

  if (!res.body) {
    throw new StreamChatError(0, "No response body from chat stream");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let sep = buffer.indexOf("\n\n");
      while (sep !== -1) {
        const frame = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        const event = parseSseFrame(frame);
        if (event) yield event;
        sep = buffer.indexOf("\n\n");
      }
    }

    if (buffer.trim()) {
      const event = parseSseFrame(buffer);
      if (event) yield event;
    }
  } finally {
    reader.releaseLock();
  }
}

// ---- Core fetch ----

class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {};
  if (!(init?.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, { ...init, headers: { ...headers, ...init?.headers } });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      const raw = body.detail ?? body.message ?? detail;
      if (typeof raw === "string") {
        detail = raw;
      } else if (Array.isArray(raw)) {
        detail = raw.map((x: { msg?: string }) => x.msg ?? String(x)).join("; ");
      }
    } catch {
      // ignore
    }
    if (res.status === 404 && detail === "Not Found") {
      detail =
        "API route not found. Use the Prompter server (port 8000) or run Vite dev with the backend up.";
    }
    if (res.status === 405) {
      detail =
        "Method not allowed — ensure you are using the Prompter API server, not the Vite dev server alone.";
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// ---- Health ----

export async function health(): Promise<HealthResponse> {
  const res = await fetch("/health");
  if (res.status === 200 || res.status === 503) {
    return res.json() as Promise<HealthResponse>;
  }
  let detail = res.statusText;
  try {
    const body = await res.json();
    const raw = body.detail ?? body.message ?? detail;
    if (typeof raw === "string") detail = raw;
  } catch {
    // ignore
  }
  throw new ApiError(res.status, detail);
}

// ---- Settings ----

export const getSettings = () => apiFetch<SettingsSnapshot>("/settings");
export const updateSettings = (updates: SettingsUpdate) =>
  apiFetch<SettingsSnapshot>("/settings", {
    method: "PUT",
    body: JSON.stringify(updates),
  });

export const getOllamaModels = () =>
  apiFetch<{ reachable: boolean; models: string[] }>("/ollama/models");

export const getTools = () =>
  apiFetch<{ tools: string[] }>("/tools");

export const TIER_ALIASES = [
  "local/coding-light",
  "local/coding-medium",
  "local/coding-heavy",
  "local/reasoning-medium",
  "local/reasoning-heavy",
  "local/vision-light",
  "local/vision-medium",
  "local/vision-heavy",
  "local/tool-calling-medium",
] as const;

// ---- Projects ----

export const listProjects = () => apiFetch<ProjectSummary[]>("/projects");
export const createProject = (name: string, system_prompt?: string) =>
  apiFetch<ProjectDetail>("/projects/init", {
    method: "POST",
    body: JSON.stringify({ name, system_prompt: system_prompt ?? "" }),
  });

// ---- Instructions ----

export const getInstructions = (projectId: string) =>
  apiFetch<InstructionsResponse>(`/projects/${projectId}/instructions`);
export const updateInstructions = (projectId: string, content: string) =>
  apiFetch<InstructionsResponse>(`/projects/${projectId}/instructions`, {
    method: "PUT",
    body: JSON.stringify({ content }),
  });

// ---- Sync ----

export const syncProject = (projectId: string) =>
  apiFetch<SyncResponse>(`/projects/${projectId}/sync`, { method: "POST" });

// ---- Sources ----

export const getSources = (projectId: string) =>
  apiFetch<SourcesState>(`/projects/${projectId}/sources`);

export const updateSources = (
  projectId: string,
  enabled: string[],
  default_new_enabled = true,
) =>
  apiFetch<SourcesState>(`/projects/${projectId}/sources`, {
    method: "PUT",
    body: JSON.stringify({ enabled, default_new_enabled }),
  });

export const uploadSources = (projectId: string, files: File[]) => {
  const form = new FormData();
  for (const f of files) form.append("files", f);
  return apiFetch<SourcesState>(`/projects/${projectId}/sources`, {
    method: "POST",
    body: form,
  });
};

export const deleteSource = (projectId: string, filename: string) =>
  apiFetch<undefined>(
    `/projects/${projectId}/sources/${encodeURIComponent(filename)}`,
    { method: "DELETE" },
  );

// ---- Threads ----

export const listThreads = (projectId: string) =>
  apiFetch<ThreadSummary[]>(`/projects/${projectId}/threads`);

export const createThread = (projectId: string, title?: string) =>
  apiFetch<ThreadSummary>(`/projects/${projectId}/threads`, {
    method: "POST",
    body: JSON.stringify({ title }),
  });

export const deleteThread = (projectId: string, threadId: string) =>
  apiFetch<undefined>(
    `/projects/${projectId}/threads/${encodeURIComponent(threadId)}`,
    { method: "DELETE" },
  );

export const clearThreadMessages = (projectId: string, threadId: string) =>
  apiFetch<ThreadSummary>(
    `/projects/${projectId}/threads/${encodeURIComponent(threadId)}/messages`,
    { method: "DELETE" },
  );

export const renameThread = (
  projectId: string,
  threadId: string,
  title: string,
) =>
  apiFetch<ThreadSummary>(
    `/projects/${projectId}/threads/${encodeURIComponent(threadId)}`,
    { method: "PATCH", body: JSON.stringify({ title }) },
  );

// ---- Messages ----

export const getMessages = (projectId: string, threadId: string) =>
  apiFetch<MessageRecord[]>(
    `/projects/${projectId}/threads/${threadId}/messages`,
  );
