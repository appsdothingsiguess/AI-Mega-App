/** Chat view: history, SSE stream, model_loading, picker from /health.models. */

import {
  createChat,
  getMessages,
  listChats,
  streamMessage,
} from "../api.js";
import { escapeHtml, renderMarkdown } from "../markdown.js";
import { navigate, replaceHash, type Route, type ViewHandle } from "../router.js";
import { get, set } from "../store.js";
import type { DoneEvent, Message } from "../types.js";
import {
  applyModelPick,
  renderPickerMenu,
  selectedModelLabel,
} from "./composer.js";

export function createChatView(): ViewHandle {
  let root: HTMLElement | null = null;
  let route: Route | null = null;
  let messages: Message[] = [];
  let abort: AbortController | null = null;
  let pickerOpen = false;
  let loadingModel: string | null = null;
  let bannerError: string | null = null;
  let streaming = false;
  let onDocClick: ((e: Event) => void) | null = null;

  const unmount = () => {
    abort?.abort();
    abort = null;
    if (onDocClick) document.removeEventListener("click", onDocClick);
    onDocClick = null;
    root = null;
    route = null;
  };

  function syncPicker(): void {
    const menu = root?.querySelector(".model-menu");
    if (!menu) return;
    renderPickerMenu(menu, pickerOpen, (model) => void pickModel(model));
  }

  async function pickModel(model: string | null): Promise<void> {
    pickerOpen = false;
    await applyModelPick(route?.chatId ?? null, model);
    const label = root?.querySelector(".model-picker-label");
    if (label) label.textContent = selectedModelLabel();
    syncPicker();
  }

  function renderMessages(scroll: boolean): void {
    const box = root?.querySelector(".messages-inner");
    if (!box) return;
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
      if (m.role === "assistant") bubble.innerHTML = renderMarkdown(m.content);
      else bubble.textContent = m.content;
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
          meta.appendChild(modelEl);
        }
        if (m.traceId) {
          const link = document.createElement("a");
          link.href = `#/debug?trace=${encodeURIComponent(m.traceId)}`;
          link.textContent = "trace →";
          link.addEventListener("click", (e) => {
            e.preventDefault();
            navigate(`#/debug?trace=${encodeURIComponent(m.traceId!)}`);
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
      if (sc) sc.scrollTop = sc.scrollHeight;
    }
  }

  async function loadHistory(chatId: string): Promise<void> {
    try {
      messages = await getMessages(chatId);
      bannerError = null;
    } catch (err) {
      messages = [];
      bannerError = err instanceof Error ? err.message : "Failed to load messages";
    }
    renderMessages(true);
  }

  async function ensureChat(): Promise<string | null> {
    if (route?.chatId) return route.chatId;
    try {
      const { id } = await createChat();
      // replaceHash (not navigate): navigate remounts the view and aborts the
      // in-flight AbortController / SSE started by send() right after this.
      route = { name: "chat", chatId: id, traceId: null };
      set({ chats: await listChats(), activeChatId: id });
      replaceHash(`#/chat/${id}`);
      const title = root?.querySelector(".chat-header-title");
      if (title) title.textContent = "Chat";
      return id;
    } catch (err) {
      bannerError = err instanceof Error ? err.message : "Could not create chat";
      renderMessages(false);
      return null;
    }
  }

  async function send(): Promise<void> {
    if (streaming) return;
    const ta = root?.querySelector("textarea") as HTMLTextAreaElement | null;
    const text = ta?.value.trim() ?? "";
    if (!text) return;
    const chatId = await ensureChat();
    if (!chatId) return;
    if (ta) ta.value = "";
    bannerError = null;
    loadingModel = null;
    messages.push({
      id: `local-u-${Date.now()}`,
      role: "user",
      content: text,
      model: null,
      created_at: Date.now(),
    });
    const assistant: Message = {
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
    const result = await streamMessage(
      chatId,
      { content: text, model: get().modelOverride },
      {
        signal: abort.signal,
        onConnectionLost: () => {
          bannerError = "connection lost";
          loadingModel = null;
        },
        onEvent: (ev) => {
          if (ev.event === "token") {
            loadingModel = null;
            assistant.content += (ev.data as { text?: string }).text ?? "";
            renderMessages(true);
          } else if (ev.event === "model_loading") {
            loadingModel = (ev.data as { model?: string }).model ?? "model";
            renderMessages(true);
          } else if (ev.event === "done") {
            const d = ev.data as DoneEvent;
            loadingModel = null;
            assistant.id = d.message_id ?? assistant.id;
            assistant.model = d.model ?? assistant.model;
            assistant.traceId = d.trace_id;
            set({ lastTraceId: d.trace_id });
            renderMessages(true);
          } else if (ev.event === "error") {
            loadingModel = null;
            const d = ev.data as { kind?: string; detail?: string };
            bannerError = d.detail || d.kind || "error";
            renderMessages(true);
          }
        },
      },
    );
    streaming = false;
    loadingModel = null;
    if (result === "lost" && !bannerError) bannerError = "connection lost";
    renderMessages(true);
    try { set({ chats: await listChats() }); } catch { /* ignore */ }
  }

  function buildDom(el: HTMLElement): void {
    el.innerHTML = `
      <div class="chat-layout">
        <div class="chat-column">
          <div class="chat-header"><span class="chat-header-title">Chat</span></div>
          <div class="messages"><div class="messages-inner"></div></div>
          <div class="composer-wrap"><div class="composer-inner">
            <div class="tool-chips">
              <span class="tool-chip" title="Phase 3">web_search</span>
              <span class="tool-chip" title="Phase 5">browser</span>
              <span class="tool-chip" title="Phase 3">file_ops</span>
            </div>
            <div class="composer">
              <textarea rows="1" placeholder="Message Local LLM…"></textarea>
              <div class="composer-toolbar">
                <button type="button" class="attach-btn" disabled title="Attachments — later phase" aria-label="Attach">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>
                  </svg>
                </button>
                <div class="model-picker">
                  <button type="button" class="model-picker-btn" id="model-picker-btn">
                    <span class="model-dot"></span>
                    <span class="model-picker-label">${escapeHtml(selectedModelLabel())}</span>
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"></polyline></svg>
                  </button>
                  <div class="model-menu" hidden></div>
                </div>
                <span class="send-hint">⏎ to send</span>
                <button type="button" class="send-btn" id="send-btn" aria-label="Send">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                    <line x1="12" y1="19" x2="12" y2="5"></line>
                    <polyline points="5 12 12 5 19 12"></polyline>
                  </svg>
                </button>
              </div>
            </div>
          </div></div>
        </div>
        <aside class="right-panel muted">Artifact panel — Phase 3</aside>
      </div>`;

    const title = el.querySelector(".chat-header-title");
    if (title) {
      const chat = get().chats.find((c) => c.id === route?.chatId);
      title.textContent = chat?.title?.trim() || (route?.chatId ? "Chat" : "New chat");
    }

    el.querySelector("#model-picker-btn")?.addEventListener("click", (e) => {
      e.stopPropagation();
      pickerOpen = !pickerOpen;
      syncPicker();
    });
    el.querySelector("#send-btn")?.addEventListener("click", () => void send());
    const ta = el.querySelector("textarea") as HTMLTextAreaElement;
    ta.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        void send();
      }
    });
    onDocClick = (e: Event) => {
      if (!pickerOpen) return;
      const t = e.target as Node | null;
      if (root?.querySelector(".model-picker")?.contains(t)) return;
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
      syncPicker();
      if (r.chatId) await loadHistory(r.chatId);
      else {
        messages = [];
        renderMessages(false);
      }
    },
    unmount,
  };
}
