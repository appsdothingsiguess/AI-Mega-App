/** Chat view: history, SSE stream, picker from /api/models, route/title. */
import { createChat, getMessages, listChats, streamMessage } from "../api.js";
import { addCopyButtons, escapeHtml, renderMarkdown } from "../markdown.js";
import { navigate, replaceHash } from "../router.js";
import { get, set } from "../store.js";
import { applyModelPick, applyTitleToStore, chatLayoutHtml, refreshHealthModels, renderPickerMenu, routeHoverTitle, selectedModelLabel, } from "./composer.js";
/** Refresh the chat list now and again after a delay.
 *
 * The auto-title job (app/background/titles.py) runs in a background queue
 * *after* the turn's SSE stream has already closed (done/error are terminal
 * per the frozen contract) -- there's no live channel left to push a title
 * update through even in principle (the server's own emit_chat_sse hook is
 * never wired to anything, so it's a no-op today regardless). An immediate
 * refresh right after `done` mostly loses the race against the ~1-1.5s the
 * title job takes; the delayed one catches it without requiring a manual
 * page reload. */
function refreshChatsSoon() {
    void listChats().then((chats) => set({ chats })).catch(() => { });
    setTimeout(() => {
        void listChats().then((chats) => set({ chats })).catch(() => { });
    }, 2500);
}
function formatElapsed(ms) {
    if (ms < 1000)
        return `${ms}ms`;
    if (ms < 60_000)
        return `${(ms / 1000).toFixed(1)}s`;
    const mins = Math.floor(ms / 60_000);
    const secs = Math.round((ms % 60_000) / 1000);
    return `${mins}m${secs}s`;
}
function formatTokens(n) {
    if (n >= 1_000_000)
        return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1000)
        return `${(n / 1000).toFixed(1)}k`;
    return String(n);
}
export function createChatView() {
    let root = null;
    let route = null;
    let messages = [];
    let abort = null;
    let pickerOpen = false;
    let loadingModel = null;
    let bannerError = null;
    let onDocClick = null;
    // Track streaming state globally so it persists across view unmount/remount
    // (HANDOFF 2026-08-06 #2: navigating to Debug shouldn't interrupt streaming).
    let streaming = get().activeChatStreaming ?? false;
    const unmount = () => {
        // Don't abort the SSE stream on unmount — let it complete in the background
        // so the user can navigate away and back without interrupting generation.
        // The abort controller is only used for the explicit stop button.
        set({ activeChatStreaming: streaming });
        if (onDocClick)
            document.removeEventListener("click", onDocClick);
        onDocClick = null;
        root = null;
        route = null;
    };
    function syncStopBtn() {
        const sendBtn = root?.querySelector("#send-btn");
        const stopBtn = root?.querySelector("#stop-btn");
        if (sendBtn)
            sendBtn.hidden = streaming;
        if (stopBtn)
            stopBtn.hidden = !streaming;
    }
    function syncPicker() {
        const menu = root?.querySelector(".model-menu");
        if (!menu)
            return;
        renderPickerMenu(menu, pickerOpen, (model) => void pickModel(model));
    }
    async function pickModel(model) {
        pickerOpen = false;
        await applyModelPick(route?.chatId ?? null, model);
        const label = root?.querySelector(".model-picker-label");
        if (label)
            label.textContent = selectedModelLabel();
        syncPicker();
    }
    function setHeaderTitle(text) {
        const el = root?.querySelector(".chat-header-title");
        if (el)
            el.textContent = text;
    }
    function renderSummaryBanner() {
        const existing = root?.querySelector(".summary-banner");
        if (existing)
            existing.remove();
        const chat = get().chats.find((c) => c.id === route?.chatId);
        if (!chat?.summary)
            return;
        const wrap = root?.querySelector(".messages");
        if (!wrap)
            return;
        const banner = document.createElement("details");
        banner.className = "summary-banner";
        const s = document.createElement("summary");
        s.textContent = "Conversation summary";
        banner.appendChild(s);
        const body = document.createElement("p");
        body.textContent = chat.summary;
        banner.appendChild(body);
        wrap.prepend(banner);
    }
    function renderMessages(scroll) {
        const box = root?.querySelector(".messages-inner");
        if (!box)
            return;
        const sc = root?.querySelector(".messages");
        const wasNearBottom = sc
            ? sc.scrollHeight - sc.scrollTop - sc.clientHeight < 80
            : true;
        box.replaceChildren();
        renderSummaryBanner();
        if (!messages.length && !streaming) {
            const empty = document.createElement("div");
            empty.className = "empty-state";
            empty.textContent = route?.chatId
                ? "Send a message to start."
                : "New chat — pick a model or leave Auto, then send.";
            box.appendChild(empty);
        }
        for (const m of messages) {
            const row = document.createElement("div");
            row.className = `msg ${m.role === "user" ? "user" : "assistant"}`;
            const bubble = document.createElement("div");
            bubble.className = "msg-bubble";
            if (m.role === "assistant") {
                bubble.innerHTML = renderMarkdown(m.content);
                addCopyButtons(bubble);
            }
            else
                bubble.textContent = m.content;
            if (streaming && m === messages[messages.length - 1] && m.role === "assistant") {
                const cur = document.createElement("span");
                cur.className = "stream-cursor";
                bubble.appendChild(cur);
            }
            row.appendChild(bubble);
            if (m.role === "assistant" && (m.model || m.traceId || m.tokensPerSecond || m.elapsedMs != null || m.promptTokens != null || m.completionTokens != null)) {
                const meta = document.createElement("div");
                meta.className = "msg-meta";
                if (m.model) {
                    const modelEl = document.createElement("span");
                    let modelText = m.model;
                    const parts = [];
                    if (m.elapsedMs != null)
                        parts.push(formatElapsed(m.elapsedMs));
                    if (m.tokensPerSecond)
                        parts.push(`${m.tokensPerSecond} tok/s`);
                    if (m.promptTokens != null || m.completionTokens != null) {
                        const p = m.promptTokens != null ? formatTokens(m.promptTokens) : "?";
                        const c = m.completionTokens != null ? formatTokens(m.completionTokens) : "?";
                        parts.push(`${p}/${c} tok`);
                    }
                    if (parts.length)
                        modelText += ` · ${parts.join(" · ")}`;
                    modelEl.textContent = modelText;
                    const tip = routeHoverTitle(m.route, m.model);
                    if (tip)
                        modelEl.title = tip;
                    meta.appendChild(modelEl);
                }
                if (m.traceId) {
                    const link = document.createElement("a");
                    link.href = `#/debug?trace=${encodeURIComponent(m.traceId)}`;
                    link.textContent = "trace →";
                    link.addEventListener("click", (e) => {
                        e.preventDefault();
                        navigate(`#/debug?trace=${encodeURIComponent(m.traceId)}`);
                    });
                    meta.appendChild(link);
                }
                row.appendChild(meta);
            }
            // Regenerate button: only for non-streaming assistant messages
            if (m.role === "assistant" && !streaming) {
                const userMsg = messages[messages.indexOf(m) - 1];
                if (userMsg && userMsg.role === "user") {
                    const regenBtn = document.createElement("button");
                    regenBtn.type = "button";
                    regenBtn.className = "regen-btn";
                    regenBtn.title = "Regenerate response";
                    regenBtn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>`;
                    regenBtn.addEventListener("click", () => void regenerate(userMsg.content));
                    row.appendChild(regenBtn);
                }
            }
            box.appendChild(row);
        }
        if (loadingModel) {
            const b = document.createElement("div");
            b.className = "banner loading";
            b.innerHTML = `<div class="spinner"></div><span>loading ${escapeHtml(loadingModel)}…</span>`;
            box.appendChild(b);
        }
        if (bannerError) {
            const b = document.createElement("div");
            b.className = "banner error";
            b.textContent = bannerError;
            const retry = document.createElement("button");
            retry.type = "button";
            retry.className = "banner-retry-btn";
            retry.textContent = "Retry";
            retry.addEventListener("click", () => {
                const lastUser = [...messages].reverse().find((m) => m.role === "user");
                if (lastUser) {
                    void regenerate(lastUser.content);
                }
                else if (route?.chatId) {
                    void loadHistory(route.chatId);
                }
            });
            b.appendChild(retry);
            box.appendChild(b);
        }
        if (scroll && sc) {
            // Only auto-scroll if the user was near the bottom BEFORE the update.
            // This prevents yanking the user back to the bottom if they scrolled
            // up mid-stream (HANDOFF 2026-08-06 #3).
            if (wasNearBottom) {
                const scrollTopAtUpdate = sc.scrollTop;
                // Double-rAF to ensure layout is fully committed before reading
                // scrollHeight. Re-check scrollTop (not "near bottom") against its
                // value at update time: streaming growth itself pushes scrollHeight
                // well past any near-bottom threshold every time a chunk lands, so
                // re-deriving "still near bottom" from the grown scrollHeight while
                // scrollTop hasn't moved yet always fails and permanently pins the
                // view at its pre-stream position (repro: new chat, scrollTop stuck
                // at 0 while scrollHeight grew from 524 to 3364 over a stream).
                // scrollTop changing between the two checks is what actually means
                // "the user scrolled manually" -- that's the only case to respect.
                requestAnimationFrame(() => {
                    requestAnimationFrame(() => {
                        if (sc && sc.scrollTop === scrollTopAtUpdate) {
                            sc.scrollTop = sc.scrollHeight;
                        }
                    });
                });
            }
        }
    }
    async function loadHistory(chatId) {
        try {
            messages = await getMessages(chatId);
            bannerError = null;
        }
        catch (err) {
            messages = [];
            bannerError = err instanceof Error ? err.message : "Failed to load messages";
        }
        renderMessages(true);
    }
    async function ensureChat() {
        if (route?.chatId)
            return route.chatId;
        try {
            const { id } = await createChat();
            // replaceHash (not navigate): navigate remounts and aborts in-flight SSE.
            route = { name: "chat", chatId: id, traceId: null };
            set({ chats: await listChats(), activeChatId: id });
            replaceHash(`#/chat/${id}`);
            setHeaderTitle("Chat");
            return id;
        }
        catch (err) {
            bannerError = err instanceof Error ? err.message : "Could not create chat";
            renderMessages(false);
            return null;
        }
    }
    async function send() {
        if (streaming)
            return;
        const ta = root?.querySelector("textarea");
        const text = ta?.value.trim() ?? "";
        if (!text)
            return;
        const chatId = await ensureChat();
        if (!chatId)
            return;
        if (ta)
            ta.value = "";
        bannerError = null;
        loadingModel = null;
        messages.push({
            id: `local-u-${Date.now()}`,
            role: "user",
            content: text,
            model: null,
            created_at: Date.now(),
        });
        const assistant = {
            id: `local-a-${Date.now()}`,
            role: "assistant",
            content: "",
            model: get().modelOverride,
            created_at: Date.now(),
        };
        messages.push(assistant);
        streaming = true;
        syncStopBtn();
        renderMessages(true);
        abort = new AbortController();
        const result = await streamMessage(chatId, { content: text, model: get().modelOverride }, {
            signal: abort.signal,
            onConnectionLost: () => {
                bannerError = "connection lost";
                loadingModel = null;
            },
            onEvent: (ev) => {
                if (ev.event === "token") {
                    loadingModel = null;
                    assistant.content += ev.data.text ?? "";
                    renderMessages(true);
                }
                else if (ev.event === "model_loading") {
                    loadingModel = ev.data.model ?? "model";
                    renderMessages(true);
                }
                else if (ev.event === "title") {
                    const d = ev.data;
                    if (d.chat_id && typeof d.title === "string") {
                        applyTitleToStore(d.chat_id, d.title, route?.chatId, setHeaderTitle);
                    }
                }
                else if (ev.event === "done") {
                    const d = ev.data;
                    loadingModel = null;
                    assistant.id = d.message_id ?? assistant.id;
                    assistant.model = d.model ?? assistant.model;
                    assistant.traceId = d.trace_id;
                    assistant.elapsedMs = Date.now() - assistant.created_at;
                    assistant.promptTokens = d.usage?.prompt_tokens ?? undefined;
                    assistant.completionTokens = d.usage?.completion_tokens ?? undefined;
                    if (d.route)
                        assistant.route = d.route;
                    if (d.timings?.predicted_per_second) {
                        assistant.tokensPerSecond = Math.round(d.timings.predicted_per_second * 10) / 10;
                    }
                    set({ lastTraceId: d.trace_id });
                    renderMessages(true);
                }
                else if (ev.event === "error") {
                    loadingModel = null;
                    const d = ev.data;
                    bannerError = d.detail || d.kind || "error";
                    renderMessages(true);
                }
            },
        });
        streaming = false;
        set({ activeChatStreaming: false });
        loadingModel = null;
        syncStopBtn();
        if (result === "lost" && !bannerError)
            bannerError = "connection lost";
        renderMessages(true);
        refreshChatsSoon();
    }
    async function regenerate(userContent) {
        if (streaming)
            return;
        const chatId = await ensureChat();
        if (!chatId)
            return;
        bannerError = null;
        loadingModel = null;
        const assistant = {
            id: `local-a-${Date.now()}`,
            role: "assistant",
            content: "",
            model: get().modelOverride,
            created_at: Date.now(),
        };
        messages.push(assistant);
        streaming = true;
        syncStopBtn();
        renderMessages(true);
        abort = new AbortController();
        const result = await streamMessage(chatId, { content: userContent, model: get().modelOverride }, {
            signal: abort.signal,
            onConnectionLost: () => {
                bannerError = "connection lost";
                loadingModel = null;
            },
            onEvent: (ev) => {
                if (ev.event === "token") {
                    loadingModel = null;
                    assistant.content += ev.data.text ?? "";
                    renderMessages(true);
                }
                else if (ev.event === "model_loading") {
                    loadingModel = ev.data.model ?? "model";
                    renderMessages(true);
                }
                else if (ev.event === "done") {
                    const d = ev.data;
                    loadingModel = null;
                    assistant.id = d.message_id ?? assistant.id;
                    assistant.model = d.model ?? assistant.model;
                    assistant.traceId = d.trace_id;
                    assistant.elapsedMs = Date.now() - assistant.created_at;
                    assistant.promptTokens = d.usage?.prompt_tokens ?? undefined;
                    assistant.completionTokens = d.usage?.completion_tokens ?? undefined;
                    if (d.route)
                        assistant.route = d.route;
                    if (d.timings?.predicted_per_second) {
                        assistant.tokensPerSecond = Math.round(d.timings.predicted_per_second * 10) / 10;
                    }
                    set({ lastTraceId: d.trace_id });
                    renderMessages(true);
                }
                else if (ev.event === "error") {
                    loadingModel = null;
                    const d = ev.data;
                    bannerError = d.detail || d.kind || "error";
                    renderMessages(true);
                }
            },
        });
        streaming = false;
        set({ activeChatStreaming: false });
        loadingModel = null;
        syncStopBtn();
        if (result === "lost" && !bannerError)
            bannerError = "connection lost";
        renderMessages(true);
        refreshChatsSoon();
    }
    function buildDom(el) {
        el.innerHTML = chatLayoutHtml(selectedModelLabel());
        const chat = get().chats.find((c) => c.id === route?.chatId);
        setHeaderTitle(chat?.title?.trim() || (route?.chatId ? "Chat" : "New chat"));
        el.querySelector("#model-picker-btn")?.addEventListener("click", (e) => {
            e.stopPropagation();
            pickerOpen = !pickerOpen;
            syncPicker();
        });
        el.querySelector("#send-btn")?.addEventListener("click", () => void send());
        el.querySelector("#stop-btn")?.addEventListener("click", () => {
            abort?.abort();
            abort = null;
            streaming = false;
            set({ activeChatStreaming: false });
            loadingModel = null;
            syncStopBtn();
            renderMessages(false);
        });
        const ta = el.querySelector("textarea");
        ta.addEventListener("keydown", (e) => {
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send();
            }
        });
        onDocClick = (e) => {
            if (!pickerOpen)
                return;
            if (root?.querySelector(".model-picker")?.contains(e.target))
                return;
            pickerOpen = false;
            syncPicker();
        };
        document.addEventListener("click", onDocClick);
    }
    return {
        async mount(el, r) {
            root = el;
            route = r;
            set({ activeChatId: r.chatId });
            buildDom(el);
            await refreshHealthModels();
            if (r.chatId) {
                // Hydrate the model picker from the chat's persisted override —
                // store.modelOverride is otherwise pure client state that resets to
                // null/"Auto" on every reload, silently diverging from what the
                // backend actually enforces (HANDOFF: model-picker showed "Auto"
                // for a chat Debug confirmed was locked to a manual override).
                const chats = get().chats.length ? get().chats : await listChats().catch(() => []);
                if (!get().chats.length)
                    set({ chats });
                const chat = chats.find((c) => c.id === r.chatId);
                set({ modelOverride: chat?.model_override ?? null });
                const label = root?.querySelector(".model-picker-label");
                if (label)
                    label.textContent = selectedModelLabel();
                await loadHistory(r.chatId);
            }
            else {
                set({ modelOverride: null });
                messages = [];
                renderMessages(false);
            }
            syncPicker();
        },
        unmount,
    };
}
