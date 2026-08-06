/** DTOs mirroring Phase-1 backend contracts (app/main.py, app/chat/api.py, app/debug/api.py). */

export type ModelClass =
  | "general"
  | "reasoning"
  | "coding"
  | "vision"
  | "utility"
  | "embed"
  | "classifier"
  | "dispatcher"
  | string;

export interface HealthModel {
  name: string;
  class: ModelClass;
  enabled: boolean;
}

export interface Health {
  status: string;
  version: string;
  db: string;
  models: HealthModel[];
}

export interface ChatSummary {
  id: string;
  title: string | null;
  updated_at: number;
  summary?: string | null;
}

export interface Message {
  id: string;
  role: string;
  content: string;
  model: string | null;
  created_at: number;
  /** Client-only: set from done.trace_id for deep-link to Debug. */
  traceId?: string;
  /** Client-only: tokens/second from llama.cpp timings. */
  tokensPerSecond?: number;
}

export interface Usage {
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  total_tokens?: number | null;
}

export interface TokenEvent {
  text: string;
}

export interface ModelLoadingEvent {
  model: string;
}

export interface DoneEvent {
  message_id: string;
  model: string;
  usage?: Usage;
  timings?: { predicted_per_second?: number };
  trace_id: string;
  route?: { source?: string; intent?: string; model?: string; confidence?: number };
  citations?: unknown;
}

export interface ErrorEvent {
  kind: string;
  detail: string;
}

export type ChatSseEvent =
  | { event: "token"; data: TokenEvent }
  | { event: "model_loading"; data: ModelLoadingEvent }
  | { event: "done"; data: DoneEvent }
  | { event: "error"; data: ErrorEvent }
  | { event: string; data: Record<string, unknown> };

export interface Span {
  id: string;
  trace_id: string;
  stage: string;
  started_at: number;
  ended_at: number | null;
  data: Record<string, unknown>;
}

export interface Trace {
  trace_id: string;
  chat_id: string | null;
  started_at: number;
  spans: Span[];
}

/** Chat-facing model classes shown in the composer picker. */
export const PICKER_CLASSES = ["general", "reasoning", "coding", "vision"] as const;

export type PickerClass = (typeof PICKER_CLASSES)[number];

export const PICKER_GROUP_LABELS: Record<PickerClass, string> = {
  general: "General",
  reasoning: "Reasoning",
  coding: "Coding",
  vision: "Vision",
};
