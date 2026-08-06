/** Composer model picker helpers (split from chat.ts for size). */

import { listModels, rosterToHealthModels, setChatModel } from "../api.js";
import { escapeHtml } from "../markdown.js";
import { get, set } from "../store.js";
import {
  PICKER_CLASSES,
  PICKER_GROUP_LABELS,
  type DoneEvent,
  type Message,
  type PickerClass,
} from "../types.js";

const AUTO_LABEL = "Auto (router)";

/** Client-only route from done.payload (types.ts is read-only). */
export type ChatMsg = Message & { route?: NonNullable<DoneEvent["route"]> };

export function selectedModelLabel(): string {
  return get().modelOverride ?? AUTO_LABEL;
}

/** Hover title from done.route: "coder · via classifier 0.92". */
export function routeHoverTitle(
  route: NonNullable<DoneEvent["route"]> | undefined,
  fallbackModel?: string | null,
): string | null {
  if (!route) return null;
  const model = route.model ?? fallbackModel ?? "";
  const source = route.source ?? "";
  if (!model && !source) return null;
  const conf =
    typeof route.confidence === "number"
      ? ` ${route.confidence.toFixed(2)}`
      : "";
  return `${model} · via ${source}${conf}`.replace(/^ · /, "").trim();
}

export async function refreshHealthModels(): Promise<void> {
  try {
    set({ healthModels: rosterToHealthModels(await listModels()) });
  } catch {
    /* keep prior */
  }
}

export function pickerGroups() {
  const models = get().healthModels.filter((m) =>
    (PICKER_CLASSES as readonly string[]).includes(m.class),
  );
  const groups: { cls: PickerClass; label: string; items: typeof models }[] = [];
  for (const cls of PICKER_CLASSES) {
    const items = models.filter((m) => m.class === cls);
    if (items.length) groups.push({ cls, label: PICKER_GROUP_LABELS[cls], items });
  }
  return groups;
}

export function renderPickerMenu(
  menu: Element,
  open: boolean,
  onPick: (model: string | null) => void,
): void {
  menu.replaceChildren();
  const el = menu as HTMLElement;
  if (!open) {
    el.hidden = true;
    return;
  }
  el.hidden = false;

  const auto = document.createElement("button");
  auto.type = "button";
  auto.className =
    "model-menu-row" + (get().modelOverride == null ? " selected" : "");
  auto.innerHTML =
    `<span>${AUTO_LABEL}</span><span class="muted" style="font-size:11px">router decides</span>`;
  auto.addEventListener("click", () => onPick(null));
  menu.appendChild(auto);

  for (const g of pickerGroups()) {
    const label = document.createElement("div");
    label.className = "model-menu-group";
    label.textContent = g.label;
    menu.appendChild(label);
    for (const m of g.items) {
      const row = document.createElement("button");
      row.type = "button";
      row.className =
        "model-menu-row" + (get().modelOverride === m.name ? " selected" : "");
      row.innerHTML = `<span class="name">${escapeHtml(m.name)}</span>`;
      row.addEventListener("click", () => onPick(m.name));
      menu.appendChild(row);
    }
  }
}

export async function applyModelPick(
  chatId: string | null,
  model: string | null,
): Promise<void> {
  set({ modelOverride: model });
  if (!chatId) return;
  try {
    await setChatModel(chatId, model);
  } catch (err) {
    console.error("setChatModel", err);
  }
}

export function applyTitleToStore(
  chatId: string,
  title: string,
  activeChatId: string | null | undefined,
  setHeader: (t: string) => void,
): void {
  set({
    chats: get().chats.map((c) => (c.id === chatId ? { ...c, title } : c)),
  });
  if (activeChatId === chatId) setHeader(title.trim() || "Chat");
}

/** Inner HTML for chat layout (keeps chat.ts under 300 lines). */
export function chatLayoutHtml(label: string): string {
  return `<div class="chat-layout"><div class="chat-column">
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
<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
</button>
<div class="model-picker">
<button type="button" class="model-picker-btn" id="model-picker-btn">
<span class="model-dot"></span>
<span class="model-picker-label">${escapeHtml(label)}</span>
<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"></polyline></svg>
</button>
<div class="model-menu" hidden></div>
</div>
<span class="send-hint">⏎ to send</span>
<button type="button" class="send-btn" id="send-btn" aria-label="Send">
<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="19" x2="12" y2="5"></line><polyline points="5 12 12 5 19 12"></polyline></svg>
</button>
<button type="button" class="stop-btn" id="stop-btn" aria-label="Stop" hidden>
<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><rect x="6" y="6" width="12" height="12" rx="1"></rect></svg>
</button>
</div></div></div></div></div>
<aside class="right-panel muted">Artifact panel — Phase 3</aside></div>`;
}
