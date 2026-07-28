/** Typed fetch wrappers + SSE via fetch+ReadableStream. */

import type {
  ChatSseEvent,
  ChatSummary,
  Health,
  Message,
  Span,
  Trace,
} from "./types.js";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new ApiError(body || res.statusText, res.status);
  }
  return (await res.json()) as T;
}

export async function getHealth(): Promise<Health> {
  return json(await fetch("/health"));
}

export async function listChats(): Promise<ChatSummary[]> {
  return json(await fetch("/api/chats"));
}

export async function createChat(projectId?: string | null): Promise<{ id: string }> {
  return json(
    await fetch("/api/chats", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_id: projectId ?? null }),
    }),
  );
}

export async function getMessages(chatId: string): Promise<Message[]> {
  return json(await fetch(`/api/chats/${encodeURIComponent(chatId)}/messages`));
}

export async function setChatModel(
  chatId: string,
  model: string | null,
): Promise<{ model_override: string | null }> {
  return json(
    await fetch(`/api/chats/${encodeURIComponent(chatId)}/model`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model }),
    }),
  );
}

export async function listTraces(opts?: {
  chatId?: string;
  limit?: number;
}): Promise<Trace[]> {
  const q = new URLSearchParams();
  if (opts?.chatId) q.set("chat_id", opts.chatId);
  if (opts?.limit != null) q.set("limit", String(opts.limit));
  const qs = q.toString();
  return json(await fetch(`/api/debug/traces${qs ? `?${qs}` : ""}`));
}

export async function getTrace(traceId: string): Promise<Trace> {
  return json(await fetch(`/api/debug/trace/${encodeURIComponent(traceId)}`));
}

export interface SseHandlers {
  onEvent: (ev: { event: string; data: Record<string, unknown> }) => void;
  onConnectionLost?: () => void;
  signal?: AbortSignal;
}

/** Parse one SSE block (event + data lines). */
function parseBlock(block: string): { event: string; data: string } | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith(":") || line.trim() === "") continue;
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  }
  if (dataLines.length === 0) return null;
  return { event, data: dataLines.join("\n") };
}

async function readSseStream(
  res: Response,
  handlers: SseHandlers,
  opts: { requireTerminal: boolean },
): Promise<"done" | "error" | "lost" | "aborted"> {
  if (!res.ok || !res.body) {
    handlers.onConnectionLost?.();
    return "lost";
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let terminal: "done" | "error" | null = null;
  const onAbort = () => {
    void reader.cancel();
  };
  handlers.signal?.addEventListener("abort", onAbort);
  if (handlers.signal?.aborted) onAbort();

  try {
    while (true) {
      if (handlers.signal?.aborted) return "aborted";
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let sep: number;
      while ((sep = buf.indexOf("\n\n")) >= 0) {
        const block = buf.slice(0, sep);
        buf = buf.slice(sep + 2);
        const parsed = parseBlock(block);
        if (!parsed) continue;
        let data: Record<string, unknown> = {};
        try {
          data = JSON.parse(parsed.data) as Record<string, unknown>;
        } catch {
          data = { raw: parsed.data };
        }
        handlers.onEvent({ event: parsed.event, data });
        if (parsed.event === "done") terminal = "done";
        if (parsed.event === "error") terminal = "error";
      }
    }
  } catch {
    if (handlers.signal?.aborted) return "aborted";
    if (opts.requireTerminal && !terminal) {
      handlers.onConnectionLost?.();
      return "lost";
    }
    return terminal ?? "lost";
  } finally {
    handlers.signal?.removeEventListener("abort", onAbort);
  }

  if (opts.requireTerminal && !terminal) {
    handlers.onConnectionLost?.();
    return "lost";
  }
  return terminal ?? "lost";
}

export type ChatStreamHandlers = {
  onEvent: (ev: ChatSseEvent) => void;
  onConnectionLost?: () => void;
  signal?: AbortSignal;
};

/** POST message stream. Ends without done/error → connection lost. */
export async function streamMessage(
  chatId: string,
  body: { content: string; attachments?: string[]; model?: string | null },
  handlers: ChatStreamHandlers,
): Promise<"done" | "error" | "lost" | "aborted"> {
  let res: Response;
  try {
    res = await fetch(`/api/chats/${encodeURIComponent(chatId)}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({
        content: body.content,
        attachments: body.attachments ?? [],
        model: body.model ?? null,
      }),
      signal: handlers.signal,
    });
  } catch {
    if (handlers.signal?.aborted) return "aborted";
    handlers.onConnectionLost?.();
    return "lost";
  }

  return readSseStream(
    res,
    {
      onEvent: (ev) => handlers.onEvent(ev as ChatSseEvent),
      onConnectionLost: handlers.onConnectionLost,
      signal: handlers.signal,
    },
    { requireTerminal: true },
  );
}

export type DebugStreamHandle = { stop: () => void };

/** Live debug SSE with reconnect backoff. Heartbeats ignored by caller. */
export function streamDebugSpans(
  onSpan: (span: Span) => void,
  opts?: { signal?: AbortSignal },
): DebugStreamHandle {
  let stopped = false;
  let attempt = 0;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  const ctrl = new AbortController();
  const onAbort = () => {
    ctrl.abort();
    if (reconnectTimer != null) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  };
  opts?.signal?.addEventListener("abort", onAbort);

  const stop = () => {
    stopped = true;
    onAbort();
    opts?.signal?.removeEventListener("abort", onAbort);
  };

  const loop = async () => {
    while (!stopped && !ctrl.signal.aborted) {
      try {
        const res = await fetch("/api/debug/stream", {
          headers: { Accept: "text/event-stream" },
          signal: ctrl.signal,
        });
        attempt = 0;
        await readSseStream(
          res,
          {
            onEvent: (ev) => {
              if (ev.event === "span") onSpan(ev.data as unknown as Span);
            },
            signal: ctrl.signal,
          },
          { requireTerminal: false },
        );
      } catch {
        /* reconnect below */
      }
      if (stopped || ctrl.signal.aborted) break;
      attempt += 1;
      const delay = Math.min(15_000, 500 * 2 ** Math.min(attempt, 5));
      await new Promise<void>((resolve) => {
        reconnectTimer = setTimeout(() => {
          reconnectTimer = null;
          resolve();
        }, delay);
      });
    }
  };

  void loop();
  return { stop };
}
