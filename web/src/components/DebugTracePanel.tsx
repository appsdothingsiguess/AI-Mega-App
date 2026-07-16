import { useEffect, useRef, useState } from "react";

export interface TraceEntry {
  id: string;
  timestamp: string;
  stage: string;
  data: Record<string, unknown>;
  elapsed_ms?: number;
}

interface TurnRecordSummary {
  turn_id: string;
  timestamp: string;
  intent: string;
  route_source: string;
  route_confidence: number;
  model_alias: string;
  rag_chunks_retrieved: number;
  llm_iterations: number;
  total_elapsed_ms: number;
  phase_timings: Record<string, number>;
  error?: string | null;
}

interface Props {
  entries: TraceEntry[];
  visible: boolean;
  sseTraceEnabled: boolean;
  onClear: () => void;
  lastTurnUrl?: string;
}

type FilterKey = "All" | "Route" | "RAG" | "LLM" | "Tools";

const FILTER_STAGES: Record<FilterKey, string[]> = {
  All: [],
  Route: ["route"],
  RAG: ["rag"],
  LLM: ["llm_request", "llm_complete", "llm_response", "llm_reasoning", "tool_call_fallback"],
  Tools: ["tools", "tool_call", "tool_result", "tool_dispatch"],
};

const BADGE_COLORS: Record<string, string> = {
  route: "#3b82f6",
  rag: "#22c55e",
  llm_request: "#f97316",
  llm_complete: "#f97316",
  llm_response: "#f97316",
  llm_reasoning: "#fb923c",
  tool_call_fallback: "#fdba74",
  tools: "#a855f7",
  tool_call: "#a855f7",
  tool_result: "#a855f7",
  tool_dispatch: "#a855f7",
  messages: "#6b7280",
  error: "#ef4444",
};

const SWIMLANE_COLORS: Record<string, string> = {
  route: "#3b82f6",
  rag: "#22c55e",
  llm: "#f97316",
  tools: "#a855f7",
};

function badgeColor(stage: string): string {
  return BADGE_COLORS[stage] ?? "#6b7280";
}

function formatOneLiner(entry: TraceEntry): string {
  const d = entry.data;
  const ms = entry.elapsed_ms != null ? ` | ${entry.elapsed_ms}ms` : "";
  switch (entry.stage) {
    case "route": {
      const sources = (d.sources as string[] | undefined)?.join(", ") ?? "";
      return `intent=${d.intent ?? "?"} | source=${d.source ?? "?"} | confidence=${
        typeof d.confidence === "number" ? d.confidence.toFixed(2) : "?"
      } | model=${d.model_alias ?? "?"}${ms}`;
    }
    case "rag": {
      const srcs = Array.isArray(d.sources)
        ? (d.sources as Array<{ source?: string }>)
            .map((s) => s.source ?? "?")
            .join(", ")
        : "";
      const topScore =
        Array.isArray(d.sources) && (d.sources as Array<{ score?: number }>).length > 0
          ? (d.sources as Array<{ score?: number }>)[0].score?.toFixed(2) ?? "?"
          : "?";
      return `${d.chunk_count ?? 0} chunks | top score=${topScore}${srcs ? ` | sources: ${srcs}` : ""}${ms}`;
    }
    case "llm_request":
      return `alias=${d.alias ?? "?"} | resolved=${d.resolved_model ?? "?"} | iteration=${d.iteration ?? 1}`;
    case "llm_complete": {
      const tokens =
        d.token_usage && typeof d.token_usage === "object"
          ? (d.token_usage as Record<string, number>).total_tokens ?? "?"
          : "?";
      return `${tokens} tokens${ms} | iteration=${d.iteration ?? 1}`;
    }
    case "llm_response": {
      const preview =
        typeof d.text_preview === "string"
          ? d.text_preview
          : typeof d.text === "string"
            ? d.text.slice(0, 80)
            : "";
      const toolCount = Array.isArray(d.tool_calls) ? d.tool_calls.length : 0;
      const fallback = d.fallback_used ? " | fallback" : "";
      return `${preview || "(empty)"}${fallback} | tools=${toolCount}${ms}`;
    }
    case "llm_reasoning":
      return `reasoning: ${String(d.reasoning_preview ?? d.reasoning ?? "").slice(0, 80)}${ms}`;
    case "tool_call_fallback":
      return `text_json → ${Array.isArray(d.tool_names) ? (d.tool_names as string[]).join(", ") : "?"}`;
    case "tools":
      return `available: ${Array.isArray(d.tool_names) ? (d.tool_names as string[]).join(", ") : "?"}`;
    case "tool_call":
      return `${d.name ?? "?"}(${JSON.stringify(d.input ?? {}).slice(0, 60)})`;
    case "tool_result":
      return `${d.name ?? "?"} → ${String(d.output ?? "").slice(0, 80)}${ms}`;
    case "tool_dispatch":
      return `${d.name ?? "?"} dispatched${ms}`;
    case "messages": {
      const msgs = Array.isArray(d.messages)
        ? (d.messages as Array<{ role?: string; content?: string }>)
        : [];
      const sysMsg = msgs.find((m) => m.role === "system");
      const sysPreview =
        sysMsg?.content != null ? String(sysMsg.content).slice(0, 80) : "";
      if (sysPreview) {
        return `system: ${sysPreview}${sysMsg!.content!.length > 80 ? "…" : ""}`;
      }
      const sys = msgs.filter((m) => m.role === "system").length;
      const hist = msgs.filter((m) => m.role === "user" || m.role === "assistant").length;
      return `system × ${sys} + history × ${hist}`;
    }
    default:
      return JSON.stringify(d).slice(0, 100);
  }
}

function formatKeyValues(data: Record<string, unknown>): string {
  return Object.entries(data)
    .map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v, null, 2) : String(v)}`)
    .join("\n");
}

function filterTraceEntries(entries: TraceEntry[], filter: FilterKey): TraceEntry[] {
  if (filter === "All") {
    return entries;
  }
  const stages = FILTER_STAGES[filter];
  return entries.filter((entry) => stages.includes(entry.stage));
}

function formatTraceEntryBlock(entry: TraceEntry): string {
  const time = new Date(entry.timestamp).toLocaleTimeString();
  const label = entry.stage.toUpperCase().replace(/_/g, " ");
  const lines = [
    `${time}`,
    label,
    formatOneLiner(entry),
    "",
    formatKeyValues(entry.data),
  ];
  if (entry.elapsed_ms != null) {
    lines.push("", `elapsed_ms: ${entry.elapsed_ms}`);
  }
  return lines.join("\n");
}

function formatTraceExport(entries: TraceEntry[], filter: FilterKey): string {
  const header = `Debug trace export (${filter}) — ${entries.length} event(s)\n${"=".repeat(48)}\n\n`;
  return header + entries.map(formatTraceEntryBlock).join("\n\n---\n\n");
}

async function copyTextToClipboard(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  document.body.removeChild(textarea);
}

function EventCard({ entry }: { entry: TraceEntry }) {
  const [open, setOpen] = useState(false);
  const time = new Date(entry.timestamp).toLocaleTimeString();
  const color = badgeColor(entry.stage);
  const label = entry.stage.toUpperCase().replace("_", " ");

  return (
    <div style={styles.card}>
      <button
        type="button"
        style={styles.cardHeader}
        onClick={() => setOpen((v) => !v)}
      >
        <span style={styles.timeStamp}>{time}</span>
        <span style={{ ...styles.badge, background: color }}>{label}</span>
        <span style={styles.oneLiner}>{formatOneLiner(entry)}</span>
        <span style={styles.chevron}>{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <pre style={styles.cardBody}>{formatKeyValues(entry.data)}</pre>
      )}
    </div>
  );
}

function SwimLane({ entries }: { entries: TraceEntry[] }) {
  const timed = entries.filter((e) => e.elapsed_ms != null);
  if (timed.length === 0) return null;

  const groups: Record<string, number> = { route: 0, rag: 0, llm: 0, tools: 0 };
  for (const e of timed) {
    if (e.stage === "route") groups.route += e.elapsed_ms!;
    else if (e.stage === "rag") groups.rag += e.elapsed_ms!;
    else if (e.stage === "llm_complete") groups.llm += e.elapsed_ms!;
    else if (["tool_call", "tool_result", "tool_dispatch"].includes(e.stage))
      groups.tools += e.elapsed_ms!;
  }
  const total = Object.values(groups).reduce((a, b) => a + b, 0);
  if (total === 0) return null;

  return (
    <div style={styles.swimLane} title="Phase timing breakdown">
      {(Object.entries(groups) as [string, number][])
        .filter(([, ms]) => ms > 0)
        .map(([phase, ms]) => (
          <div
            key={phase}
            style={{
              width: `${(ms / total) * 100}%`,
              background: SWIMLANE_COLORS[phase] ?? "#6b7280",
              height: "100%",
            }}
            title={`${phase}: ${Math.round(ms)}ms`}
          />
        ))}
    </div>
  );
}

function TurnSummaryCard({ record }: { record: TurnRecordSummary }) {
  return (
    <div style={styles.turnCard}>
      <div style={styles.turnTitle}>Last Turn</div>
      <div style={styles.turnRow}><b>intent</b>: {record.intent}</div>
      <div style={styles.turnRow}><b>model</b>: {record.model_alias}</div>
      <div style={styles.turnRow}><b>source</b>: {record.route_source} ({record.route_confidence.toFixed(2)})</div>
      <div style={styles.turnRow}><b>RAG chunks</b>: {record.rag_chunks_retrieved}</div>
      <div style={styles.turnRow}><b>LLM iterations</b>: {record.llm_iterations}</div>
      <div style={styles.turnRow}><b>total</b>: {Math.round(record.total_elapsed_ms)}ms</div>
      {record.phase_timings && (
        <div style={styles.turnRow}>
          <b>phases</b>: {Object.entries(record.phase_timings)
            .map(([k, v]) => `${k}=${Math.round(v)}ms`)
            .join(" | ")}
        </div>
      )}
      {record.error && (
        <div style={{ ...styles.turnRow, color: "#ef4444" }}><b>error</b>: {record.error}</div>
      )}
    </div>
  );
}

export default function DebugTracePanel({
  entries,
  visible,
  sseTraceEnabled,
  onClear,
  lastTurnUrl,
}: Props) {
  const [filter, setFilter] = useState<FilterKey>("All");
  const [height, setHeight] = useState(220);
  const [turnRecord, setTurnRecord] = useState<TurnRecordSummary | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [copyStatus, setCopyStatus] = useState<string | null>(null);
  const dragging = useRef(false);
  const dragStartY = useRef(0);
  const dragStartH = useRef(0);
  const copyStatusTimer = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (copyStatusTimer.current != null) {
        window.clearTimeout(copyStatusTimer.current);
      }
    };
  }, []);

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!dragging.current) return;
      const delta = dragStartY.current - e.clientY;
      const max = Math.floor(window.innerHeight * 0.5);
      setHeight(Math.max(120, Math.min(max, dragStartH.current + delta)));
    };
    const onUp = () => { dragging.current = false; };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    return () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
  }, []);

  if (!visible) return null;

  const cappedEntries = entries.slice(-200);
  const filtered = filterTraceEntries(cappedEntries, filter);

  const handleFetchLastTurn = async () => {
    if (!lastTurnUrl) return;
    setFetchError(null);
    try {
      const res = await fetch(lastTurnUrl);
      if (res.status === 204) {
        setFetchError("No turns recorded yet.");
        return;
      }
      if (!res.ok) {
        setFetchError(`${res.status} ${res.statusText}`);
        return;
      }
      const data = await res.json();
      setTurnRecord(data as TurnRecordSummary);
    } catch (err) {
      setFetchError(String(err));
    }
  };

  const handleCopyTrace = async () => {
    if (filtered.length === 0) return;
    setCopyStatus(null);
    try {
      await copyTextToClipboard(formatTraceExport(filtered, filter));
      setCopyStatus("Copied!");
      if (copyStatusTimer.current != null) {
        window.clearTimeout(copyStatusTimer.current);
      }
      copyStatusTimer.current = window.setTimeout(() => {
        setCopyStatus(null);
        copyStatusTimer.current = null;
      }, 2000);
    } catch (err) {
      setCopyStatus("Copy failed");
      if (copyStatusTimer.current != null) {
        window.clearTimeout(copyStatusTimer.current);
      }
      copyStatusTimer.current = window.setTimeout(() => {
        setCopyStatus(null);
        copyStatusTimer.current = null;
      }, 3000);
    }
  };

  return (
    <div style={{ ...styles.drawer, height }}>
      {/* Drag handle */}
      <div
        style={styles.dragHandle}
        onMouseDown={(e) => {
          dragging.current = true;
          dragStartY.current = e.clientY;
          dragStartH.current = height;
          e.preventDefault();
        }}
      />

      {/* Header */}
      <div style={styles.header}>
        <span style={styles.title}>Debug</span>
        <div style={styles.chips}>
          {(Object.keys(FILTER_STAGES) as FilterKey[]).map((f) => (
            <button
              key={f}
              type="button"
              style={{
                ...styles.chip,
                ...(filter === f ? styles.chipActive : {}),
              }}
              onClick={() => setFilter(f)}
            >
              {f}
            </button>
          ))}
        </div>
        <div style={styles.headerActions}>
          {lastTurnUrl && (
            <button type="button" style={styles.actionBtn} onClick={handleFetchLastTurn}>
              Fetch last turn
            </button>
          )}
          <button
            type="button"
            style={{
              ...styles.actionBtn,
              ...(filtered.length === 0 ? styles.actionBtnDisabled : {}),
            }}
            onClick={handleCopyTrace}
            disabled={filtered.length === 0}
            title={
              filtered.length === 0
                ? "No trace events in this filter"
                : `Copy ${filter} trace (${filtered.length})`
            }
          >
            {copyStatus ?? "Copy"}
          </button>
          <button type="button" style={styles.actionBtn} onClick={() => { onClear(); setTurnRecord(null); setFetchError(null); setCopyStatus(null); }}>
            Clear
          </button>
        </div>
      </div>

      {/* Swim lane */}
      <SwimLane entries={cappedEntries} />

      {/* Content */}
      <div style={styles.list}>
        {fetchError && (
          <div style={styles.fetchError}>{fetchError}</div>
        )}
        {turnRecord && (
          <TurnSummaryCard record={turnRecord} />
        )}
        {filtered.length === 0 ? (
          <div style={styles.empty}>
            {sseTraceEnabled
              ? "Send a message to capture trace events"
              : "Enable Settings → Show debug trace in chat UI, then send a message"}
          </div>
        ) : (
          filtered.map((entry) => <EventCard key={entry.id} entry={entry} />)
        )}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  drawer: {
    flexShrink: 0,
    borderTop: "1px solid var(--border)",
    background: "var(--bg-sidebar)",
    display: "flex",
    flexDirection: "column",
    overflow: "hidden",
    minHeight: 120,
    position: "relative",
  },
  dragHandle: {
    height: 6,
    cursor: "ns-resize",
    flexShrink: 0,
    background: "transparent",
  },
  dragHandleBar: {
    width: 32,
    height: 3,
    borderRadius: 2,
    background: "var(--border)",
    margin: "0 auto",
    marginTop: 1,
  },
  header: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: "4px 10px",
    borderBottom: "1px solid var(--border)",
    flexShrink: 0,
    flexWrap: "wrap",
  },
  title: {
    fontSize: 12,
    fontWeight: 600,
    color: "var(--text-muted)",
    fontFamily: "var(--font-mono)",
    marginRight: 4,
  },
  chips: {
    display: "flex",
    gap: 4,
    flex: 1,
    flexWrap: "wrap",
  },
  chip: {
    fontSize: 10,
    padding: "2px 8px",
    borderRadius: 12,
    background: "var(--bg-hover)",
    color: "var(--text-dim)",
    fontFamily: "var(--font-mono)",
    cursor: "pointer",
    border: "1px solid transparent",
  },
  chipActive: {
    background: "var(--accent-dim)",
    color: "var(--accent)",
    border: "1px solid var(--accent)",
  },
  headerActions: {
    display: "flex",
    gap: 6,
    marginLeft: "auto",
  },
  actionBtn: {
    fontSize: 11,
    padding: "2px 8px",
    borderRadius: "var(--radius-sm)",
    background: "var(--bg-hover)",
    color: "var(--text-dim)",
    cursor: "pointer",
  },
  actionBtnDisabled: {
    opacity: 0.45,
    cursor: "not-allowed",
  },
  swimLane: {
    height: 20,
    display: "flex",
    flexShrink: 0,
    borderBottom: "1px solid var(--border)",
    overflow: "hidden",
  },
  list: {
    flex: 1,
    overflowY: "auto",
    padding: "4px 8px",
  },
  empty: {
    color: "var(--text-dim)",
    fontSize: 12,
    padding: "12px 8px",
    textAlign: "center",
  },
  card: {
    marginBottom: 3,
    borderRadius: "var(--radius-sm)",
    background: "var(--bg-panel)",
    border: "1px solid var(--border)",
  },
  cardHeader: {
    width: "100%",
    display: "flex",
    alignItems: "center",
    gap: 6,
    padding: "3px 8px",
    textAlign: "left",
    overflow: "hidden",
  },
  timeStamp: {
    fontSize: 10,
    fontFamily: "var(--font-mono)",
    color: "var(--text-dim)",
    flexShrink: 0,
  },
  badge: {
    fontSize: 10,
    fontFamily: "var(--font-mono)",
    padding: "1px 5px",
    borderRadius: 8,
    color: "#fff",
    fontWeight: 600,
    flexShrink: 0,
  },
  oneLiner: {
    fontSize: 11,
    fontFamily: "var(--font-mono)",
    color: "var(--text-muted)",
    flex: 1,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  chevron: {
    marginLeft: 4,
    fontSize: 10,
    color: "var(--text-dim)",
    flexShrink: 0,
  },
  cardBody: {
    margin: 0,
    padding: "6px 10px 8px",
    fontSize: 11,
    fontFamily: "var(--font-mono)",
    color: "var(--text-muted)",
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
    borderTop: "1px solid var(--border)",
    maxHeight: 180,
    overflowY: "auto",
  },
  turnCard: {
    margin: "4px 0",
    padding: "6px 10px",
    borderRadius: "var(--radius-sm)",
    background: "var(--bg-panel)",
    border: "1px solid var(--accent-dim)",
    fontSize: 11,
    fontFamily: "var(--font-mono)",
  },
  turnTitle: {
    fontWeight: 700,
    color: "var(--accent)",
    marginBottom: 4,
    fontSize: 12,
  },
  turnRow: {
    color: "var(--text-muted)",
    marginBottom: 2,
  },
  fetchError: {
    color: "#ef4444",
    fontSize: 11,
    fontFamily: "var(--font-mono)",
    padding: "4px 8px",
  },
};
