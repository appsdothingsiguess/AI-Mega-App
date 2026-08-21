/** Debug view: trace list, waterfall, span detail, live /api/debug/stream. */

import {
  getGpuInventory,
  getSummaryStatus,
  getTrace,
  listModels,
  listTraces,
  streamDebugSpans,
  type GpuInfo,
} from "../api.js";
import { escapeHtml } from "../markdown.js";
import type { Route, ViewHandle } from "../router.js";
import { get } from "../store.js";
import type { Span, SummaryStatus, Trace } from "../types.js";

function chatLabel(chatId: string | null): string {
  if (!chatId) return "—";
  const chat = get().chats.find((c) => c.id === chatId);
  return chat?.title?.trim() || `chat ${chatId.slice(0, 8)}`;
}

/** Background jobs (e.g. title generation) get their own single-span trace
 *  sharing the turn's chat_id — pick the substantive turn trace by default
 *  so opening Debug doesn't land on a lone "title" span. */
function isBackgroundOnly(t: Trace): boolean {
  return t.spans.length > 0 && t.spans.every((s) => s.stage === "title");
}

/** Route source chip (docs/FEATURES.md F19): which layer decided, what it
 *  decided, how sure it was, and — the part that was missing — why it fell
 *  back when it did. */
function routeChipHtml(t: Trace): string {
  const route = t.spans.find((s) => s.stage === "route");
  if (!route) return "";
  const d = route.data ?? {};
  const parts: string[] = [];
  if (d.source) parts.push(String(d.source));
  if (d.intent) parts.push(String(d.intent));
  if (typeof d.confidence === "number") parts.push(`${Math.round(d.confidence * 100)}%`);
  const fallback = d.fallback_reason ? String(d.fallback_reason) : "";
  const source = typeof d.source === "string" ? d.source : "classifier";
  const cls = fallback ? "route-chip is-fallback" : `route-chip is-${source}`;
  const label = parts.join(" · ") || "route";
  const suffix = fallback ? ` — fallback: ${fallback}` : "";
  return `<span class="${cls}" title="${escapeHtml(label + suffix)}">${escapeHtml(label + suffix)}</span>`;
}

function mb(n: unknown): string {
  const v = Number(n);
  return Number.isFinite(v) ? `${(v / 1024).toFixed(1)}GB` : "—";
}

/** Total chat context used vs. that turn's model ctx window (real
 *  prompt_tokens from llama.cpp's own usage field on the llm_stream span —
 *  never a client-side estimate — against the roster's configured ctx for
 *  the model that turn actually ran on). This is the same signal
 *  app/background/summaries.py triggers rolling-summary compaction from,
 *  so it doubles as "how close is this chat to summarizing / truncating." */
function contextUsageHtml(t: Trace, modelCtx: Record<string, number>): string {
  const llmSpan = t.spans.find((s) => {
    if (s.stage !== "llm_stream") return false;
    const usage = s.data?.usage as { prompt_tokens?: unknown } | undefined;
    return typeof usage?.prompt_tokens === "number";
  });
  if (!llmSpan) return "";
  const usage = llmSpan.data.usage as { prompt_tokens: number };
  const used = usage.prompt_tokens;
  const model = typeof llmSpan.data?.model === "string" ? llmSpan.data.model : null;
  const ctx = model ? modelCtx[model] : undefined;
  if (!ctx) {
    return `<span class="context-usage" title="model ctx unknown">${used} tok</span>`;
  }
  const pct = Math.round((used / ctx) * 100);
  const cls = pct >= 90 ? "context-usage is-hot" : pct >= 50 ? "context-usage is-warm" : "context-usage";
  return `<span class="${cls}" title="${used} of ${ctx} tokens used on ${escapeHtml(model ?? "")}">${used}/${ctx} tok (${pct}%)</span>`;
}

/** Rolling-summary trigger panel (docs/FEATURES.md F19): how close this
 *  chat is to the next auto-summary, and what the last regen actually did.
 *  Mirrors app/background/summaries.py:_trigger_state's fields exactly so
 *  "will it trigger" here can never drift from what actually happens. */
function summaryStatusHtml(status: SummaryStatus | null): string {
  if (!status) return "";
  const rows: string[] = [];

  if (status.source === "turn_count_fallback") {
    const n = status.summary_every_n_turns ?? "?";
    rows.push(
      `<div class="summary-status-row">
        <span class="summary-label">summary trigger</span>
        <span class="mono">turn ${status.turn_count} of ${n} (no usage data yet)</span>
        ${status.will_trigger ? '<span class="summary-badge is-due">due next turn</span>' : ""}
      </div>`,
    );
  } else {
    const used = status.latest_tokens ?? 0;
    const threshold = status.threshold_tokens ?? 0;
    const pct = threshold > 0 ? Math.min(100, Math.round((used / threshold) * 100)) : 0;
    const barCls = status.will_trigger
      ? "summary-fill is-due"
      : pct >= 75
        ? "summary-fill is-close"
        : "summary-fill";
    const floorNote =
      status.min_routable_ctx != null
        ? ` title="threshold = ${escapeHtml(status.source === "token_ctx_fraction" ? "fraction of tightest routable ctx" : "flat fallback")}, min routable ctx = ${status.min_routable_ctx}"`
        : "";
    rows.push(
      `<div class="summary-status-row"${floorNote}>
        <span class="summary-label">summary trigger</span>
        <div class="summary-track"><div class="${barCls}" style="width:${pct}%"></div></div>
        <span class="mono">${used}/${threshold} tok (${pct}%)</span>
        ${status.will_trigger ? '<span class="summary-badge is-due">due next turn</span>' : ""}
        ${status.in_flight ? '<span class="summary-badge is-running">running</span>' : ""}
      </div>`,
    );
  }

  const last = status.last_summary;
  if (last) {
    const when = new Date(last.started_at).toLocaleTimeString();
    const device = last.device ?? "?";
    const model = last.model ?? "?";
    const coverage =
      last.new_message_count != null && last.covered_message_count != null
        ? `covered ${last.new_message_count} new msg(s), ${last.covered_message_count} total`
        : "";
    const errNote = last.error ? ` — FAILED: ${escapeHtml(last.error)}` : "";
    rows.push(
      `<div class="summary-status-meta muted">last regen ${escapeHtml(when)} via ${escapeHtml(model)} (${escapeHtml(device)}) — ${escapeHtml(coverage)}${errNote}</div>`,
    );
  } else {
    rows.push(`<div class="summary-status-meta muted">no summary yet for this chat</div>`);
  }

  return `<div class="summary-status">${rows.join("")}</div>`;
}

export function createDebugView(): ViewHandle {
  let root: HTMLElement | null = null;
  let traces: Trace[] = [];
  let selected: Trace | null = null;
  let selectedSpan: Span | null = null;
  let detailTab: "data" | "prompt" | "response" = "data";
  let live: { stop: () => void } | null = null;
  let abort: AbortController | null = null;
  let gpus: GpuInfo[] = [];
  let gpuTimer: ReturnType<typeof setInterval> | null = null;
  let modelCtx: Record<string, number> = {};
  let summaryStatus: SummaryStatus | null = null;
  let summaryStatusChatId: string | null = null;

  const unmount = () => {
    live?.stop();
    live = null;
    abort?.abort();
    abort = null;
    if (gpuTimer !== null) clearInterval(gpuTimer);
    gpuTimer = null;
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
          <span>${escapeHtml(chatLabel(t.chat_id))}</span>
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
      ${routeChipHtml(selected)}
      ${contextUsageHtml(selected, modelCtx)}
      <span class="muted">${selected.spans.length} spans</span>`;
    pane.appendChild(meta);
    const id = document.createElement("div");
    id.className = "waterfall-id";
    id.textContent = `trace_id: ${selected.trace_id}`;
    pane.appendChild(id);
    if (selected.chat_id && summaryStatusChatId === selected.chat_id) {
      const summaryEl = document.createElement("div");
      summaryEl.innerHTML = summaryStatusHtml(summaryStatus);
      pane.appendChild(summaryEl.firstElementChild ?? summaryEl);
    }

    const maxMs = Math.max(1, ...selected.spans.map(spanMs));
    for (const sp of selected.spans) {
      const ms = spanMs(sp);
      const pct = Math.max(2, Math.round((ms / maxMs) * 100));
      const row = document.createElement("button");
      row.type = "button";
      row.className =
        "span-row" + (selectedSpan?.id === sp.id ? " active" : "");
      const gpuTag = sp.data?.gpu != null ? ` [GPU${sp.data.gpu}]` : "";
      const modelTag =
        typeof sp.data?.model === "string" ? ` · ${sp.data.model}${gpuTag}` : "";
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
    if (detailTab === "prompt") {
      // `prompt`/`response` are stripped server-side when
      // debug.store_prompts is false — say so instead of silently falling
      // back to the raw data dump, which read as "prompts are broken".
      body =
        data.prompt != null
          ? String(data.prompt)
          : `No prompt recorded for the "${selectedSpan.stage}" stage.\n\nOnly stages that send a model a prompt record one (llm_request, route), and only when debug.store_prompts is true in config.yaml.`;
    } else if (detailTab === "response") {
      body =
        data.response != null
          ? String(data.response)
          : data.raw_response != null
            ? String(data.raw_response)
            : `No response recorded for the "${selectedSpan.stage}" stage.\n\nOnly stages that receive model output record one (llm_stream, route), and only when debug.store_prompts is true in config.yaml.`;
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

  /** Live nvidia-smi telemetry (docs/FEATURES.md A3): real used/total VRAM
   *  per device, plus the model llama-swap most recently served. */
  function renderTelemetry(): void {
    const pane = root?.querySelector(".debug-telemetry");
    if (!pane) return;
    pane.replaceChildren();
    const wrap = document.createElement("div");
    if (!gpus.length) {
      wrap.innerHTML = `<div class="muted">no GPU telemetry</div>`;
    } else {
      wrap.innerHTML = gpus
        .slice()
        .sort((a, b) => a.index - b.index)
        .map((g) => {
          const used = Math.max(0, g.mem_total_mb - g.mem_free_mb);
          const pct = g.mem_total_mb ? Math.round((used / g.mem_total_mb) * 100) : 0;
          return `<div class="gpu-bar" title="${escapeHtml(g.name)}">
            <span>GPU${g.index}</span>
            <div class="gpu-track"><div class="gpu-fill" style="width:${pct}%"></div></div>
            <span class="mono">${mb(used)} / ${mb(g.mem_total_mb)}</span>
          </div>`;
        })
        .join("");
    }
    const swap = document.createElement("div");
    swap.className = "swap-label";
    const lastModel =
      traces
        .flatMap((t) => t.spans)
        .filter((s) => s.stage === "llm_request" || s.stage === "llm_stream")
        .sort((a, b) => b.started_at - a.started_at)
        .map((s) => s.data?.model)
        .find((m) => typeof m === "string") ?? "—";
    swap.innerHTML = `<span class="swap-dot"></span><span>swap: ${escapeHtml(String(lastModel))}</span>`;
    wrap.appendChild(swap);
    pane.appendChild(wrap);
  }

  async function refreshTelemetry(): Promise<void> {
    try {
      gpus = await getGpuInventory();
    } catch {
      gpus = [];
    }
    renderTelemetry();
  }

  function render(): void {
    renderList();
    renderWaterfall();
    renderDetail();
    renderTelemetry();
  }

  async function loadSummaryStatus(chatId: string): Promise<void> {
    try {
      summaryStatus = await getSummaryStatus(chatId);
    } catch {
      summaryStatus = null;
    }
    summaryStatusChatId = chatId;
    render();
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
    if (selected?.chat_id && selected.chat_id !== summaryStatusChatId) {
      summaryStatus = null;
      summaryStatusChatId = null;
      void loadSummaryStatus(selected.chat_id);
    }
    render();
  }

  function buildDom(el: HTMLElement): void {
    el.innerHTML = `
      <div class="debug-view">
        <div class="debug-top">
          <h1>Debug</h1>
          <div class="debug-telemetry"></div>
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
      try {
        const roster = await listModels();
        modelCtx = Object.fromEntries(roster.map((m) => [m.alias, m.ctx]));
      } catch {
        modelCtx = {};
      }
      render();
      if (route.traceId) await selectTrace(route.traceId);
      else {
        const initial = traces.find((t) => !isBackgroundOnly(t)) ?? traces[0];
        if (initial) await selectTrace(initial.trace_id);
      }

      live = streamDebugSpans((span) => mergeSpan(span), { signal: abort.signal });
      void refreshTelemetry();
      gpuTimer = setInterval(() => {
        void refreshTelemetry();
        // Token pressure and last-regen state change as new turns stream
        // in for the selected chat -- keep the summary-trigger panel live
        // rather than frozen at whatever it showed when the trace was
        // first opened.
        if (selected?.chat_id) void loadSummaryStatus(selected.chat_id);
      }, 5000);
    },
    unmount,
  };
}
