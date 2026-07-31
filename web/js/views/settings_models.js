/** Settings → Models: roster table, draft edits, Apply → PUTs + gpu/apply. */
import { ApiError, getSwapConfig, postGpuApply, putModelSettings, } from "../api.js";
import { escapeHtml } from "../markdown.js";
function gpuValue(g) {
    return g === "cpu" ? "cpu" : String(g);
}
function parseGpu(v) {
    if (v === "cpu")
        return "cpu";
    const n = Number(v);
    return Number.isFinite(n) ? n : "cpu";
}
function same(a, b) {
    return (a.gpu === b.gpu &&
        a.resident === b.resident &&
        a.ttl_s === b.ttl_s &&
        a.enabled === b.enabled);
}
function deviceOptions(inv, current) {
    const seen = new Set();
    const opts = [];
    for (const g of inv.slice().sort((a, b) => a.index - b.index)) {
        const v = String(g.index);
        seen.add(v);
        opts.push({ value: v, label: `GPU${g.index}` });
    }
    if (!seen.has("cpu")) {
        seen.add("cpu");
        opts.push({ value: "cpu", label: "cpu" });
    }
    const cur = gpuValue(current);
    if (!seen.has(cur)) {
        opts.unshift({
            value: cur,
            label: cur === "cpu" ? "cpu" : `GPU${cur}`,
        });
    }
    return opts;
}
function dirtyList(drafts, baseline) {
    return drafts.filter((d) => {
        const b = baseline.find((x) => x.name === d.name);
        return !b || !same(d, b);
    });
}
export function mountModelsPanel(panel, opts) {
    let applying = false;
    const render = () => {
        const dirty = dirtyList(opts.drafts, opts.baseline);
        const rows = opts.drafts
            .map((d, i) => {
            const isDirty = dirty.some((x) => x.name === d.name);
            const devices = deviceOptions(opts.inventory, d.gpu);
            const optsHtml = devices
                .map((o) => `<option value="${escapeHtml(o.value)}"${gpuValue(d.gpu) === o.value ? " selected" : ""}>${escapeHtml(o.label)}</option>`)
                .join("");
            return `<tr class="${isDirty ? "is-dirty" : ""}" data-i="${i}">
          <td class="mono alias">${escapeHtml(d.name)}</td>
          <td class="muted">${escapeHtml(d.class)}</td>
          <td><select class="field-device" data-i="${i}">${optsHtml}</select></td>
          <td>
            <button type="button" class="toggle ${d.resident ? "is-on" : ""}" data-i="${i}" data-act="resident" aria-pressed="${d.resident}" title="Resident">
              <span class="toggle-thumb"></span>
            </button>
          </td>
          <td><input class="field-ttl mono" type="number" min="0" data-i="${i}" value="${escapeHtml(String(d.ttl_s))}" /></td>
        </tr>`;
        })
            .join("");
        panel.innerHTML = `
      <div class="settings-section-head">
        <h2>Models</h2>
        <p class="settings-lead">Roster, device placement, residency. Edits stay draft until Apply.</p>
      </div>
      <div class="models-table-wrap">
        <div class="models-table-scroll">
          <table class="models-table">
            <thead><tr>
              <th>Alias</th><th>Class</th><th>Device</th><th>Resident</th><th>TTL</th>
            </tr></thead>
            <tbody>${rows || `<tr><td colspan="5" class="muted">No models from API.</td></tr>`}</tbody>
          </table>
        </div>
      </div>
      <div class="settings-actions">
        <button type="button" class="btn-apply" ${applying || dirty.length === 0 ? "disabled" : ""}>
          ${applying ? "Applying…" : `Apply${dirty.length ? ` (${dirty.length})` : ""}`}
        </button>
      </div>
      <div class="swap-config-block">
        <div class="swap-config-label">llama-swap config</div>
        <pre class="swap-config mono"></pre>
      </div>`;
        const pre = panel.querySelector(".swap-config");
        if (pre)
            pre.textContent = opts.swapYaml || "(empty)";
        panel.querySelectorAll(".field-device").forEach((sel) => {
            sel.addEventListener("change", () => {
                const i = Number(sel.dataset.i);
                const next = opts.drafts.map((d, idx) => idx === i ? { ...d, gpu: parseGpu(sel.value) } : d);
                opts.onDrafts(next);
                opts.drafts = next;
                render();
            });
        });
        panel.querySelectorAll("[data-act=resident]").forEach((btn) => {
            btn.addEventListener("click", () => {
                const i = Number(btn.dataset.i);
                const next = opts.drafts.map((d, idx) => idx === i ? { ...d, resident: !d.resident } : d);
                opts.onDrafts(next);
                opts.drafts = next;
                render();
            });
        });
        panel.querySelectorAll(".field-ttl").forEach((inp) => {
            inp.addEventListener("change", () => {
                const i = Number(inp.dataset.i);
                const ttl = Math.max(0, Number(inp.value) || 0);
                const next = opts.drafts.map((d, idx) => idx === i ? { ...d, ttl_s: ttl } : d);
                opts.onDrafts(next);
                opts.drafts = next;
                render();
            });
        });
        panel.querySelector(".btn-apply")?.addEventListener("click", () => {
            void apply();
        });
    };
    async function apply() {
        const dirty = dirtyList(opts.drafts, opts.baseline);
        if (dirty.length === 0 || applying)
            return;
        applying = true;
        render();
        try {
            for (const d of dirty) {
                await putModelSettings(d.name, {
                    gpu: d.gpu,
                    resident: d.resident,
                    ttl_s: d.ttl_s,
                    enabled: d.enabled,
                });
            }
            const result = await postGpuApply();
            let yaml = "";
            try {
                yaml = await getSwapConfig();
            }
            catch {
                yaml = opts.swapYaml;
            }
            opts.onSwapYaml(yaml);
            opts.swapYaml = yaml;
            opts.onBaseline(opts.drafts.map((d) => ({ ...d })));
            opts.baseline = opts.drafts.map((d) => ({ ...d }));
            const ok = result.ok !== false;
            opts.toast(ok ? `Applied — ${result.path || "swap-config updated"}` : "Apply returned ok=false", ok ? "ok" : "err");
        }
        catch (e) {
            const msg = e instanceof ApiError ? e.message : String(e);
            opts.toast(`Apply failed: ${msg}`, "err");
        }
        finally {
            applying = false;
            render();
        }
    }
    render();
    return { refresh: render };
}
