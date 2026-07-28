/** Composer model picker helpers (split from chat.ts for size). */

import { setChatModel } from "../api.js";
import { escapeHtml } from "../markdown.js";
import { get, set } from "../store.js";
import {
  PICKER_CLASSES,
  PICKER_GROUP_LABELS,
  type PickerClass,
} from "../types.js";

export function selectedModelLabel(): string {
  return get().modelOverride ?? "Auto";
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
    `<span>Auto</span><span class="muted" style="font-size:11px">router decides</span>`;
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
