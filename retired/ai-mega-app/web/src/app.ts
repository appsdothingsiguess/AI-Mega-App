/** Boot: shell chrome, views, router. */

import { createChat, getHealth, listChats } from "./api.js";
import { navigate, registerView, startRouter, type Route } from "./router.js";
import { get, set, subscribe } from "./store.js";
import { createChatView } from "./views/chat.js";
import { createDebugView } from "./views/debug.js";
import { createSettingsView } from "./views/settings.js";

function $(id: string): HTMLElement {
  const el = document.getElementById(id);
  if (!el) throw new Error(`#${id} missing`);
  return el;
}

function renderRecents(): void {
  const list = $("recents-list");
  const { chats, activeChatId } = get();
  list.replaceChildren();
  for (const c of chats.slice(0, 40)) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "recent-item" + (c.id === activeChatId ? " active" : "");
    btn.textContent = c.title?.trim() || "Untitled chat";
    btn.title = c.title?.trim() || c.id;
    btn.addEventListener("click", () => navigate(`#/chat/${c.id}`));
    list.appendChild(btn);
  }
}

function setNavActive(route: Route): void {
  const chats = document.getElementById("nav-chats");
  const debug = document.getElementById("nav-debug");
  const settings = document.getElementById("nav-settings");
  chats?.classList.toggle("active", route.name === "chat");
  debug?.classList.toggle("active", route.name === "debug");
  settings?.classList.toggle("active", route.name === "settings");
}

async function refreshChats(): Promise<void> {
  try {
    const chats = await listChats();
    set({ chats });
  } catch {
    /* backend may be down during static open */
  }
}

async function onNewChat(): Promise<void> {
  try {
    const { id } = await createChat();
    await refreshChats();
    navigate(`#/chat/${id}`);
  } catch (err) {
    console.error("createChat failed", err);
  }
}

async function boot(): Promise<void> {
  const app = $("app");
  $("sidebar-toggle").addEventListener("click", () => {
    const next = !get().sidebarCollapsed;
    set({ sidebarCollapsed: next });
    app.classList.toggle("sidebar-collapsed", next);
  });

  $("nav-new-chat").addEventListener("click", () => void onNewChat());
  $("nav-chats").addEventListener("click", () => navigate("#/"));
  $("nav-debug").addEventListener("click", () => navigate("#/debug"));
  $("nav-settings").addEventListener("click", () => navigate("#/settings"));

  subscribe(() => renderRecents());

  registerView("chat", createChatView);
  registerView("debug", createDebugView);
  registerView("settings", createSettingsView);

  try {
    const health = await getHealth();
    set({ healthModels: health.models.filter((m) => m.enabled) });
  } catch {
    set({ healthModels: [] });
  }

  await refreshChats();

  startRouter($("view"), {
    onRoute: (route) => {
      set({ activeChatId: route.chatId });
      setNavActive(route);
      void refreshChats();
    },
  });
}

void boot();
