/** Chat view: history, SSE stream, picker from /api/models, route/title. */
import { createChat, getMessages, listChats, streamMessage } from "../api.js";
import { escapeHtml, renderMarkdown } from "../markdown.js";
import { navigate, replaceHash } from "../router.js";
import { get, set } from "../store.js";
import { applyModelPick, applyTitleToStore, chatLayoutHtml, refreshHealthModels, renderPickerMenu, routeHoverTitle, selectedModelLabel, } from "./composer.js";
export function createChatView() {
    let root = null;
    let route = null;
    let messages = [];
    let abort = null;
    let pickerOpen = false;
    let loadingModel = null;
    let bannerError = null;
    let streaming = false;
    let onDocClick = null;
    const unmount = () => {
        abort?.abort();
        abort = null;
        if (onDocClick)
            document.removeEventListener("click", onDocClick);
        onDocClick = null;
        root = null;
        route = null;
    };
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
    function renderMessages(scroll) {
        const box = root?.querySelector(".messages-inner");
        if (!box)
            return;
        box.replaceChildren();
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
            if (m.role === "assistant")
                bubble.innerHTML = renderMarkdown(m.content);
            else
                bubble.textContent = m.content;
            if (streaming && m === messages[messages.length - 1] && m.role === "assistant") {
                const cur = document.createElement("span");
                cur.className = "stream-cursor";
                bubble.appendChild(cur);
            }
            row.appendChild(bubble);
            if (m.role === "assistant" && (m.model || m.traceId)) {
                const meta = document.createElement("div");
                meta.className = "msg-meta";
                if (m.model) {
                    const modelEl = document.createElement("span");
                    modelEl.textContent = m.model;
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
            box.appendChild(b);
        }
        if (scroll) {
            const sc = root?.querySelector(".messages");
            if (sc)
                sc.scrollTop = sc.scrollHeight;
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
                    if (d.route)
                        assistant.route = d.route;
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
        loadingModel = null;
        if (result === "lost" && !bannerError)
            bannerError = "connection lost";
        renderMessages(true);
        try {
            set({ chats: await listChats() });
        }
        catch { /* ignore */ }
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
            syncPicker();
            if (r.chatId)
                await loadHistory(r.chatId);
            else {
                messages = [];
                renderMessages(false);
            }
        },
        unmount,
    };
}
