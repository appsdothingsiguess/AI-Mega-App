/** Debug view: trace list, waterfall, span detail, live /api/debug/stream. */

import { getTrace, listTraces, streamDebugSpans } from "../api.js";
import { escapeHtml } from "../markdown.js";
import type { Route, ViewHandle } from "../router.js";
import type { Span, Trace } from "../types.js";

export function createDebugView(): ViewHandle {
  let root: HTMLElement | null = null;
  let traces: Trace[] = [];
  let selected: Trace | null = null;
  let selectedSpan: Span | null = null;
  let detailTab: "data" | "prompt" | "response" = "data";
  let live: { stop: () => void } | null = null;
  let abort: AbortController | null = null;

  const unmount = () => {
    live?.stop();
    live = null;
    abort?.abort();
    abort = null;
    root = null;
  };

  function spanMs(s: Span): number {
    if (s.ended_at == null) return 0;
    return Math.max(0, s.ended_at - s.started_at);
  }

  function mergeSpan(span: Span): void {
    let t = traces.find((x) => x.trace_id === span.trace_id);
    if (!t) {
      t = {
        trace_id: span.trace_id,
        chat_id: null,
        started_at: span.started_at,
        spans: [span],
      };
      traces = [t, ...traces];
    } else {
      const i = t.spans.findIndex((s) => s.id === span.id);
      if (i >= 0) t.spans[i] = span;
      else t.spans = [...t.spans, span].sort((a, b) => a.started_at - b.started_at);
    }
    if (selected?.trace_id === span.trace_id) {
      selected = traces.find((x) => x.trace_id === span.trace_id) ?? selected;
      if (selectedSpan?.id === span.id) selectedSpan = span;
    }
    render();
  }

  function renderList(): void {
    const list = root?.querySelector(".trace-list");
    if (!list) return;
    list.replaceChildren();
    if (!traces.length) {
      const empty = document.createElement("div");
      empty.className = "debug-empty";
      empty.textContent = "No traces yet.";
      list.appendChild(empty);
      return;
    }
    for (const t of traces) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className =
        "trace-row" + (selected?.trace_id === t.trace_id ? " active" : "");
      const stages = t.spans.map((s) => s.stage).join(" · ") || "empty";
      btn.innerHTML = `
        <div class="trace-row-title">
          <span class="trace-dot"></span>
          <span>${escapeHtml(t.chat_id ?? "—")}</span>
        </div>
        <div class="trace-summary">${escapeHtml(t.trace_id.slice(0, 8))} · ${escapeHtml(stages.slice(0, 48))}</div>`;
      btn.addEventListener("click", () => void selectTrace(t.trace_id));
      list.appendChild(btn);
    }
  }

  function renderWaterfall(): void {
    const pane = root?.querySelector(".waterfall-pane");
    if (!pane) return;
    pane.replaceChildren();
    if (!selected) {
      const empty = document.createElement("div");
      empty.className = "debug-empty";
      empty.textContent = "Select a trace.";
      pane.appendChild(empty);
      return;
    }
    const model =
      selected.spans.map((s) => s.data?.model).find((m) => typeof m === "string") ??
      "—";
    const meta = document.createElement("div");
    meta.className = "waterfall-meta";
    meta.innerHTML = `<span class="mono">${escapeHtml(String(model))}</span>
      <span class="muted">${selected.spans.length} spans</span>`;
    pane.appendChild(meta);
    const id = document.createElement("div");
    id.className = "waterfall-id";
    id.textContent = `trace_id: ${selected.trace_id}`;
    pane.appendChild(id);

    const maxMs = Math.max(1, ...selected.spans.map(spanMs));
    for (const sp of selected.spans) {
      const ms = spanMs(sp);
      const pct = Math.max(2, Math.round((ms / maxMs) * 100));
      const row = document.createElement("button");
      row.type = "button";
      row.className =
        "span-row" + (selectedSpan?.id === sp.id ? " active" : "");
      const modelTag =
        typeof sp.data?.model === "string" ? ` · ${sp.data.model}` : "";
      row.innerHTML = `
        <span class="span-name" title="${escapeHtml(sp.stage)}">${escapeHtml(sp.stage)}</span>
        <div class="span-track"><div class="span-bar" style="width:${pct}%"></div></div>
        <span class="span-ms">${ms}ms${escapeHtml(modelTag)}</span>`;
      row.addEventListener("click", () => {
        selectedSpan = sp;
        detailTab = "data";
        render();
      });
      pane.appendChild(row);
    }
  }

  function renderDetail(): void {
    const pane = root?.querySelector(".span-detail");
    if (!pane) return;
    pane.replaceChildren();
    if (!selectedSpan) {
      const empty = document.createElement("div");
      empty.className = "debug-empty";
      empty.textContent = "Select a span.";
      pane.appendChild(empty);
      return;
    }
    const h = document.createElement("h2");
    h.textContent = selectedSpan.stage;
    pane.appendChild(h);

    const toggles = document.createElement("div");
    toggles.className = "detail-toggles";
    for (const tab of ["data", "prompt", "response"] as const) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "detail-tab" + (detailTab === tab ? " active" : "");
      b.textContent = tab;
      b.addEventListener("click", () => {
        detailTab = tab;
        renderDetail();
      });
      toggles.appendChild(b);
    }
    pane.appendChild(toggles);

    const pre = document.createElement("pre");
    pre.className = "detail-pre";
    const data = selectedSpan.data ?? {};
    let body: string;
    if (detailTab === "prompt" && data.prompt != null) {
      body = String(data.prompt);
    } else if (detailTab === "response" && data.response != null) {
      body = String(data.response);
    } else {
      body = JSON.stringify(
        {
          id: selectedSpan.id,
          stage: selectedSpan.stage,
          started_at: selectedSpan.started_at,
          ended_at: selectedSpan.ended_at,
          duration_ms: spanMs(selectedSpan),
          data,
        },
        null,
        2,
      );
    }
    pre.textContent = body;
    pane.appendChild(pre);

    const usage = data.usage as Record<string, unknown> | undefined;
    const timings = data.timings as Record<string, unknown> | undefined;
    if (usage || timings) {
      const note = document.createElement("div");
      note.className = "muted";
      note.style.marginTop = "10px";
      note.style.fontSize = "11px";
      note.style.fontFamily = "var(--font-mono)";
      const parts: string[] = [];
      if (usage?.prompt_tokens != null) parts.push(`in ${usage.prompt_tokens}`);
      if (usage?.completion_tokens != null) parts.push(`out ${usage.completion_tokens}`);
      if (timings?.predicted_per_second != null) {
        parts.push(`${timings.predicted_per_second} tok/s`);
      }
      note.textContent = parts.length
        ? `llama.cpp: ${parts.join(" · ")}`
        : "timings from span data (llama.cpp), not client estimates";
      pane.appendChild(note);
    }
  }

  function render(): void {
    renderList();
    renderWaterfall();
    renderDetail();
  }

  async function selectTrace(traceId: string): Promise<void> {
    try {
      selected = await getTrace(traceId);
      const idx = traces.findIndex((t) => t.trace_id === traceId);
      if (idx >= 0) traces[idx] = selected;
      else traces = [selected, ...traces];
    } catch {
      selected = traces.find((t) => t.trace_id === traceId) ?? null;
    }
    selectedSpan = selected?.spans[0] ?? null;
    detailTab = "data";
    const hash = `#/debug?trace=${encodeURIComponent(traceId)}`;
    if (location.hash !== hash) history.replaceState(null, "", hash);
    render();
  }

  function buildDom(el: HTMLElement): void {
    el.innerHTML = `
      <div class="debug-view">
        <div class="debug-top">
          <h1>Debug</h1>
          <div class="debug-telemetry">
            <div class="gpu-bar">
              <span>GPU0</span>
              <div class="gpu-track"><div class="gpu-fill"></div></div>
              <span>— / — (Phase 2)</span>
            </div>
            <div class="gpu-bar">
              <span>GPU1</span>
              <div class="gpu-track"><div class="gpu-fill"></div></div>
              <span>— / — (Phase 2)</span>
            </div>
            <div class="swap-label">
              <span class="swap-dot"></span>
              <span>swap: — (Phase 2)</span>
            </div>
          </div>
        </div>
        <div class="debug-body">
          <div class="trace-list"></div>
          <div class="waterfall-pane"></div>
          <div class="span-detail"></div>
        </div>
      </div>`;
  }

  return {
    async mount(el, route: Route) {
      root = el;
      abort = new AbortController();
      buildDom(el);
      try {
        traces = await listTraces({ limit: 50 });
      } catch {
        traces = [];
      }
      render();
      if (route.traceId) await selectTrace(route.traceId);
      else if (traces[0]) await selectTrace(traces[0].trace_id);

      live = streamDebugSpans((span) => mergeSpan(span), { signal: abort.signal });
    },
    unmount,
  };
}
