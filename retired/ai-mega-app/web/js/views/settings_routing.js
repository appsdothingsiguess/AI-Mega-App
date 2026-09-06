/** Settings → Routing: keyword rules editor + intents map; Save → PUT. */
import { ApiError, putRouting } from "../api.js";
import { escapeHtml } from "../markdown.js";
/** Frozen classifier classes (not model names) for intent dropdowns. */
const INTENT_CLASSES = [
    "chat",
    "chit_chat",
    "code_task",
    "tool_call_needed",
    "reasoning_task",
    "vision_task",
];
function clone(r) {
    return {
        rules: (r.rules ?? []).map((x) => ({
            keywords: [...x.keywords],
            intent: x.intent,
        })),
        intents: { ...(r.intents ?? {}) },
        classifier: r.classifier ? { ...r.classifier } : undefined,
    };
}
function intentKeys(routing) {
    const fromMap = Object.keys(routing.intents ?? {});
    const set = new Set([...INTENT_CLASSES, ...fromMap]);
    return [...set];
}
export function mountRoutingPanel(panel, opts) {
    let draft = clone(opts.routing);
    let saving = false;
    const sync = () => {
        opts.onRouting(clone(draft));
    };
    const render = () => {
        const rules = draft.rules ?? [];
        const intents = draft.intents ?? {};
        const keys = intentKeys(draft);
        const aliasOpts = opts.aliases
            .map((a) => `<option value="${escapeHtml(a)}">${escapeHtml(a)}</option>`)
            .join("");
        const ruleRows = rules
            .map((rule, i) => {
            const intentOpts = INTENT_CLASSES.map((c) => `<option value="${c}"${rule.intent === c ? " selected" : ""}>${c}</option>`).join("");
            return `<div class="rule-row" data-i="${i}">
          <input class="rule-keywords mono" type="text" data-i="${i}"
            value="${escapeHtml(rule.keywords.join(", "))}"
            placeholder="keyword, another keyword" />
          <span class="rule-arrow">→</span>
          <select class="rule-intent" data-i="${i}">${intentOpts}</select>
          <button type="button" class="btn-icon" data-act="del-rule" data-i="${i}" title="Remove">×</button>
        </div>`;
        })
            .join("");
        const intentRows = keys
            .map((cls) => {
            const cur = intents[cls] ?? "";
            const options = `<option value="">—</option>` +
                opts.aliases
                    .map((a) => `<option value="${escapeHtml(a)}"${a === cur ? " selected" : ""}>${escapeHtml(a)}</option>`)
                    .join("");
            return `<div class="intent-row">
          <span class="mono intent-class">${escapeHtml(cls)}</span>
          <span class="rule-arrow">→</span>
          <select class="intent-alias" data-class="${escapeHtml(cls)}">${options || aliasOpts}</select>
        </div>`;
        })
            .join("");
        panel.innerHTML = `
      <div class="settings-section-head">
        <h2>Routing</h2>
        <p class="settings-lead">Keyword rules and intent → alias map. Save writes the overlay.</p>
      </div>
      <div class="settings-block">
        <div class="block-label">Keyword rules</div>
        <div class="rule-list">${ruleRows || `<p class="muted">No rules yet.</p>`}</div>
        <button type="button" class="btn-secondary" data-act="add-rule">+ Add rule</button>
      </div>
      <div class="settings-block">
        <div class="block-label">Intents map</div>
        <div class="intent-list">${intentRows}</div>
      </div>
      <div class="settings-actions">
        <button type="button" class="btn-apply" data-act="save" ${saving ? "disabled" : ""}>
          ${saving ? "Saving…" : "Save"}
        </button>
      </div>`;
        panel.querySelectorAll(".rule-keywords").forEach((inp) => {
            inp.addEventListener("change", () => {
                const i = Number(inp.dataset.i);
                const keywords = inp.value
                    .split(",")
                    .map((s) => s.trim())
                    .filter(Boolean);
                draft.rules = (draft.rules ?? []).map((r, idx) => idx === i ? { ...r, keywords } : r);
                sync();
            });
        });
        panel.querySelectorAll(".rule-intent").forEach((sel) => {
            sel.addEventListener("change", () => {
                const i = Number(sel.dataset.i);
                draft.rules = (draft.rules ?? []).map((r, idx) => idx === i ? { ...r, intent: sel.value } : r);
                sync();
            });
        });
        panel.querySelectorAll("[data-act=del-rule]").forEach((btn) => {
            btn.addEventListener("click", () => {
                const i = Number(btn.dataset.i);
                draft.rules = (draft.rules ?? []).filter((_, idx) => idx !== i);
                sync();
                render();
            });
        });
        panel.querySelector("[data-act=add-rule]")?.addEventListener("click", () => {
            draft.rules = [...(draft.rules ?? []), { keywords: [], intent: "chat" }];
            sync();
            render();
        });
        panel.querySelectorAll(".intent-alias").forEach((sel) => {
            sel.addEventListener("change", () => {
                const cls = sel.dataset.class ?? "";
                if (!cls)
                    return;
                const next = { ...(draft.intents ?? {}) };
                if (sel.value)
                    next[cls] = sel.value;
                else
                    delete next[cls];
                draft.intents = next;
                sync();
            });
        });
        panel.querySelector("[data-act=save]")?.addEventListener("click", () => {
            void save();
        });
    };
    async function save() {
        if (saving)
            return;
        saving = true;
        render();
        const body = {
            rules: draft.rules ?? [],
            intents: draft.intents ?? {},
        };
        if (draft.classifier)
            body.classifier = draft.classifier;
        try {
            await putRouting(body);
            opts.routing = clone(draft);
            opts.toast("Routing saved", "ok");
        }
        catch (e) {
            const msg = e instanceof ApiError ? e.message : String(e);
            opts.toast(`Save failed: ${msg}`, "err");
        }
        finally {
            saving = false;
            render();
        }
    }
    render();
    return { refresh: render };
}
