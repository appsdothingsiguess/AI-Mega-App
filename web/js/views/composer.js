/** Composer model picker helpers (split from chat.ts for size). */
import { setChatModel } from "../api.js";
import { escapeHtml } from "../markdown.js";
import { get, set } from "../store.js";
import { PICKER_CLASSES, PICKER_GROUP_LABELS, } from "../types.js";
export function selectedModelLabel() {
    return get().modelOverride ?? "Auto";
}
export function pickerGroups() {
    const models = get().healthModels.filter((m) => PICKER_CLASSES.includes(m.class));
    const groups = [];
    for (const cls of PICKER_CLASSES) {
        const items = models.filter((m) => m.class === cls);
        if (items.length)
            groups.push({ cls, label: PICKER_GROUP_LABELS[cls], items });
    }
    return groups;
}
export function renderPickerMenu(menu, open, onPick) {
    menu.replaceChildren();
    const el = menu;
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
export async function applyModelPick(chatId, model) {
    set({ modelOverride: model });
    if (!chatId)
        return;
    try {
        await setChatModel(chatId, model);
    }
    catch (err) {
        console.error("setChatModel", err);
    }
}
