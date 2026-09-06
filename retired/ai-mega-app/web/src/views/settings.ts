/** Settings view: Models + Routing tabs; other tabs disabled stubs. */

import {
  ApiError,
  getGpuInventory,
  getSettings,
  getSwapConfig,
  listModels,
  type AppSettings,
  type GpuId,
  type GpuInfo,
  type RoutingBody,
} from "../api.js";
import type { Route, ViewHandle } from "../router.js";
import { mountModelsPanel, type ModelDraft } from "./settings_models.js";
import { mountRoutingPanel } from "./settings_routing.js";

type TabId = "models" | "routing" | "stub";

const TABS: { id: TabId; key: string; label: string; active: boolean }[] = [
  { id: "models", key: "models", label: "Models", active: true },
  { id: "stub", key: "providers", label: "Providers", active: false },
  { id: "routing", key: "routing", label: "Routing", active: true },
  { id: "stub", key: "tools", label: "Tools", active: false },
  { id: "stub", key: "opencode", label: "opencode", active: false },
  { id: "stub", key: "browseros", label: "BrowserOS", active: false },
  { id: "stub", key: "memory", label: "Memory", active: false },
  { id: "stub", key: "debug", label: "Debug", active: false },
  { id: "stub", key: "appearance", label: "Appearance", active: false },
];

function asGpu(v: unknown): GpuId {
  if (v === "cpu" || v === "CPU") return "cpu";
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : "cpu";
}

function parseModels(settings: AppSettings, roster: { alias: string; class: string; device: string | number; resident: boolean }[]): ModelDraft[] {
  const raw = Array.isArray(settings.models) ? settings.models : [];
  if (raw.length > 0) {
    return raw.map((m) => {
      const o = m as Record<string, unknown>;
      const name = String(o.name ?? o.alias ?? "");
      return {
        name,
        class: String(o.class ?? ""),
        gpu: asGpu(o.gpu ?? o.device),
        resident: Boolean(o.resident),
        // null/absent stays null — coercing it to 0 silently told
        // llama-swap "never unload this model" for every model the user
        // ever touched in Settings (swapgen emits `ttl: 0` for ttl_s == 0).
        ttl_s: o.ttl_s == null ? null : Number(o.ttl_s),
        enabled: o.enabled === undefined ? true : Boolean(o.enabled),
      };
    }).filter((m) => m.name);
  }
  return roster.map((m) => ({
    name: m.alias,
    class: m.class,
    gpu: asGpu(m.device),
    resident: m.resident,
    ttl_s: null,
    enabled: true,
  }));
}

function parseRouting(settings: AppSettings): RoutingBody {
  const r = settings.routing ?? {};
  return {
    rules: Array.isArray(r.rules) ? r.rules.map((x) => ({
      keywords: [...(x.keywords ?? [])],
      intent: String(x.intent ?? ""),
    })) : [],
    intents: { ...(r.intents ?? {}) },
    classifier: r.classifier ? { ...r.classifier } : undefined,
  };
}

export function createSettingsView(): ViewHandle {
  let root: HTMLElement | null = null;
  let tab: "models" | "routing" = "models";
  let toastTimer: ReturnType<typeof setTimeout> | null = null;
  let inventory: GpuInfo[] = [];
  let aliases: string[] = [];
  let baseline: ModelDraft[] = [];
  let drafts: ModelDraft[] = [];
  let routing: RoutingBody = { rules: [], intents: {} };
  let swapYaml = "";
  let loadError = "";

  const toast = (msg: string, kind: "ok" | "err" = "ok") => {
    if (!root) return;
    let el = root.querySelector<HTMLElement>(".settings-toast");
    if (!el) {
      el = document.createElement("div");
      el.className = "settings-toast";
      root.appendChild(el);
    }
    el.textContent = msg;
    el.dataset.kind = kind;
    el.hidden = false;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      el!.hidden = true;
    }, 2400);
  };

  const unmount = () => {
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = null;
    root = null;
  };

  function render(): void {
    if (!root) return;
    const rail = root.querySelector(".settings-rail");
    const panel = root.querySelector(".settings-panel");
    if (!rail || !panel) return;

    rail.replaceChildren();
    const heading = document.createElement("div");
    heading.className = "settings-rail-title";
    heading.textContent = "Settings";
    rail.appendChild(heading);
    for (const t of TABS) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "settings-tab";
      btn.textContent = t.label;
      if (!t.active) {
        btn.disabled = true;
        btn.classList.add("is-disabled");
      } else {
        if (t.id === tab) btn.classList.add("is-active");
        btn.addEventListener("click", () => {
          tab = t.id as "models" | "routing";
          render();
        });
      }
      rail.appendChild(btn);
    }

    panel.replaceChildren();
    if (loadError) {
      const err = document.createElement("p");
      err.className = "settings-error";
      err.textContent = loadError;
      panel.appendChild(err);
      return;
    }
    if (tab === "models") {
      mountModelsPanel(panel as HTMLElement, {
        drafts,
        baseline,
        inventory,
        swapYaml,
        toast,
        onDrafts: (next) => {
          drafts = next;
        },
        onBaseline: (next) => {
          baseline = next.map((d) => ({ ...d }));
          drafts = next.map((d) => ({ ...d }));
        },
        onSwapYaml: (yaml) => {
          swapYaml = yaml;
        },
      });
    } else {
      mountRoutingPanel(panel as HTMLElement, {
        routing,
        aliases,
        toast,
        onRouting: (next) => {
          routing = next;
        },
      });
    }
  }

  async function load(): Promise<void> {
    loadError = "";
    try {
      const [settings, roster, inv, yaml] = await Promise.all([
        getSettings(),
        listModels().catch(() => [] as Awaited<ReturnType<typeof listModels>>),
        getGpuInventory().catch(() => [] as GpuInfo[]),
        getSwapConfig().catch(() => ""),
      ]);
      inventory = inv;
      aliases = roster.map((m) => m.alias);
      if (aliases.length === 0) {
        aliases = parseModels(settings, []).map((m) => m.name);
      }
      baseline = parseModels(settings, roster);
      drafts = baseline.map((d) => ({ ...d }));
      routing = parseRouting(settings);
      swapYaml = yaml;
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : String(e);
      loadError = `Failed to load settings: ${msg}`;
    }
    render();
  }

  return {
    async mount(el: HTMLElement, _route: Route): Promise<void> {
      root = el;
      el.className = "settings-view";
      el.innerHTML = `
        <aside class="settings-rail" aria-label="Settings tabs"></aside>
        <div class="settings-panel"></div>`;
      await load();
    },
    unmount,
  };
}
