/** Typed API client for Prompter backend. */

// ---- Types ----

export interface HealthResponse {
  ok: boolean;
  mode: string;
  base_url: string;
  model: string;
  model_loaded: boolean;
  message: string;
  available_models: string[];
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
}

export interface ChatResponse {
  thread_id: string;
  reply: string;
  retrieved_chunks: Array<Record<string, unknown>>;
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
  | "vision";

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
}

export interface SettingsSnapshot {
  models: ModelsConfig;
  ollama_model_names: Record<string, string>;
  vision: VisionSettings;
  router: RouterSettings;
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

export interface LmModelInfo {
  key: string;
  display_name: string;
  type: string;
  loaded: boolean;
  vision: boolean;
  params_string: string | null;
}

export interface LmModelsResponse {
  models: LmModelInfo[];
  selected_model: string;
  mode: string;
}

export interface LmModelLoadResponse {
  ok: boolean;
  model: string;
  status: string;
  instance_id: string | null;
  load_time_seconds: number | null;
  message: string;
}

export interface LmServerStatus {
  config_found: boolean;
  config_path: string | null;
  port: number;
  network_interface: string;
  serve_on_local_network: boolean;
  access_urls: string[];
  restart_required_note: string | null;
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

export const health = () => apiFetch<HealthResponse>("/health");

// ---- Settings ----

export const getSettings = () => apiFetch<SettingsSnapshot>("/settings");
export const updateSettings = (updates: SettingsUpdate) =>
  apiFetch<SettingsSnapshot>("/settings", {
    method: "PUT",
    body: JSON.stringify(updates),
  });

export const getLmModels = () => apiFetch<LmModelsResponse>("/lmstudio/models");

export const loadLmModel = (model: string, context_length?: number) =>
  apiFetch<LmModelLoadResponse>("/lmstudio/models/load", {
    method: "POST",
    body: JSON.stringify({ model, context_length }),
  });

export const getLmServer = () => apiFetch<LmServerStatus>("/lmstudio/server");

export const updateLmServer = (serve_on_local_network: boolean) =>
  apiFetch<LmServerStatus>("/lmstudio/server", {
    method: "PUT",
    body: JSON.stringify({ serve_on_local_network }),
  });

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

export const sendMessage = (
  projectId: string,
  threadId: string,
  content: string,
) =>
  apiFetch<ChatResponse>(
    `/projects/${projectId}/threads/${threadId}/messages`,
    { method: "POST", body: JSON.stringify({ content }) },
  });
