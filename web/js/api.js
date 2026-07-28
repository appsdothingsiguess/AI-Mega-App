/** Typed fetch wrappers + SSE via fetch+ReadableStream. */
export class ApiError extends Error {
    status;
    constructor(message, status) {
        super(message);
        this.status = status;
        this.name = "ApiError";
    }
}
async function json(res) {
    if (!res.ok) {
        const body = await res.text().catch(() => "");
        throw new ApiError(body || res.statusText, res.status);
    }
    return (await res.json());
}
export async function getHealth() {
    return json(await fetch("/health"));
}
export async function listChats() {
    return json(await fetch("/api/chats"));
}
export async function createChat(projectId) {
    return json(await fetch("/api/chats", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_id: projectId ?? null }),
    }));
}
export async function getMessages(chatId) {
    return json(await fetch(`/api/chats/${encodeURIComponent(chatId)}/messages`));
}
export async function setChatModel(chatId, model) {
    return json(await fetch(`/api/chats/${encodeURIComponent(chatId)}/model`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model }),
    }));
}
export async function listTraces(opts) {
    const q = new URLSearchParams();
    if (opts?.chatId)
        q.set("chat_id", opts.chatId);
    if (opts?.limit != null)
        q.set("limit", String(opts.limit));
    const qs = q.toString();
    return json(await fetch(`/api/debug/traces${qs ? `?${qs}` : ""}`));
}
export async function getTrace(traceId) {
    return json(await fetch(`/api/debug/trace/${encodeURIComponent(traceId)}`));
}
/** Parse one SSE block (event + data lines). */
function parseBlock(block) {
    let event = "message";
    const dataLines = [];
    for (const line of block.split("\n")) {
        if (line.startsWith(":") || line.trim() === "")
            continue;
        if (line.startsWith("event:"))
            event = line.slice(6).trim();
        else if (line.startsWith("data:"))
            dataLines.push(line.slice(5).trimStart());
    }
    if (dataLines.length === 0)
        return null;
    return { event, data: dataLines.join("\n") };
}
async function readSseStream(res, handlers, opts) {
    if (!res.ok || !res.body) {
        handlers.onConnectionLost?.();
        return "lost";
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let terminal = null;
    try {
        while (true) {
            if (handlers.signal?.aborted)
                return "aborted";
            const { done, value } = await reader.read();
            if (done)
                break;
            buf += decoder.decode(value, { stream: true });
            let sep;
            while ((sep = buf.indexOf("\n\n")) >= 0) {
                const block = buf.slice(0, sep);
                buf = buf.slice(sep + 2);
                const parsed = parseBlock(block);
                if (!parsed)
                    continue;
                let data = {};
                try {
                    data = JSON.parse(parsed.data);
                }
                catch {
                    data = { raw: parsed.data };
                }
                handlers.onEvent({ event: parsed.event, data });
                if (parsed.event === "done")
                    terminal = "done";
                if (parsed.event === "error")
                    terminal = "error";
            }
        }
    }
    catch {
        if (handlers.signal?.aborted)
            return "aborted";
        if (opts.requireTerminal && !terminal) {
            handlers.onConnectionLost?.();
            return "lost";
        }
        return terminal ?? "lost";
    }
    if (opts.requireTerminal && !terminal) {
        handlers.onConnectionLost?.();
        return "lost";
    }
    return terminal ?? "lost";
}
/** POST message stream. Ends without done/error → connection lost. */
export async function streamMessage(chatId, body, handlers) {
    let res;
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
    }
    catch {
        if (handlers.signal?.aborted)
            return "aborted";
        handlers.onConnectionLost?.();
        return "lost";
    }
    return readSseStream(res, {
        onEvent: (ev) => handlers.onEvent(ev),
        onConnectionLost: handlers.onConnectionLost,
        signal: handlers.signal,
    }, { requireTerminal: true });
}
/** Live debug SSE with reconnect backoff. Heartbeats ignored by caller. */
export function streamDebugSpans(onSpan, opts) {
    let stopped = false;
    let attempt = 0;
    const ctrl = new AbortController();
    const onAbort = () => ctrl.abort();
    opts?.signal?.addEventListener("abort", onAbort);
    const stop = () => {
        stopped = true;
        ctrl.abort();
        opts?.signal?.removeEventListener("abort", onAbort);
    };
    const loop = async () => {
        while (!stopped) {
            try {
                const res = await fetch("/api/debug/stream", {
                    headers: { Accept: "text/event-stream" },
                    signal: ctrl.signal,
                });
                attempt = 0;
                await readSseStream(res, {
                    onEvent: (ev) => {
                        if (ev.event === "span")
                            onSpan(ev.data);
                    },
                    signal: ctrl.signal,
                }, { requireTerminal: false });
            }
            catch {
                /* reconnect below */
            }
            if (stopped || ctrl.signal.aborted)
                break;
            attempt += 1;
            const delay = Math.min(15_000, 500 * 2 ** Math.min(attempt, 5));
            await new Promise((r) => setTimeout(r, delay));
        }
    };
    void loop();
    return { stop };
}
