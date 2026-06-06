import { useState } from "react";

export interface TraceEntry {
  id: string;
  timestamp: string;
  stage: string;
  data: Record<string, unknown>;
}

interface Props {
  entries: TraceEntry[];
  visible: boolean;
  sseTraceEnabled: boolean;
  onClear: () => void;
}

function TraceRow({ entry }: { entry: TraceEntry }) {
  const [open, setOpen] = useState(false);
  const time = new Date(entry.timestamp).toLocaleTimeString();

  return (
    <div style={styles.row}>
      <button
        type="button"
        style={styles.rowHeader}
        onClick={() => setOpen((v) => !v)}
      >
        <span style={styles.time}>{time}</span>
        <span style={styles.badge}>{entry.stage}</span>
        <span style={styles.chevron}>{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <pre style={styles.body}>
          {JSON.stringify(entry.data, null, 2)}
        </pre>
      )}
    </div>
  );
}

export default function DebugTracePanel({
  entries,
  visible,
  sseTraceEnabled,
  onClear,
}: Props) {
  if (!visible) return null;

  return (
    <div style={styles.drawer}>
      <div style={styles.header}>
        <span style={styles.title}>Debug trace</span>
        <button type="button" style={styles.clearBtn} onClick={onClear}>
          Clear
        </button>
      </div>
      <div style={styles.list}>
        {entries.length === 0 ? (
          <div style={styles.empty}>
            {sseTraceEnabled
              ? "Send a message to capture trace events"
              : "Enable Settings → Show debug trace in chat UI, then send a message"}
          </div>
        ) : (
          entries.map((entry) => <TraceRow key={entry.id} entry={entry} />)
        )}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  drawer: {
    flexShrink: 0,
    height: 220,
    borderTop: "1px solid var(--border)",
    background: "var(--bg-sidebar)",
    display: "flex",
    flexDirection: "column",
    overflow: "hidden",
  },
  header: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "6px 12px",
    borderBottom: "1px solid var(--border)",
    flexShrink: 0,
  },
  title: {
    fontSize: 12,
    fontWeight: 600,
    color: "var(--text-muted)",
    fontFamily: "var(--font-mono)",
  },
  clearBtn: {
    fontSize: 11,
    padding: "2px 8px",
    borderRadius: "var(--radius-sm)",
    background: "var(--bg-hover)",
    color: "var(--text-dim)",
  },
  list: {
    flex: 1,
    overflowY: "auto",
    padding: "6px 8px",
  },
  empty: {
    color: "var(--text-dim)",
    fontSize: 12,
    padding: "12px 8px",
    textAlign: "center",
  },
  row: {
    marginBottom: 4,
    borderRadius: "var(--radius-sm)",
    background: "var(--bg-panel)",
    border: "1px solid var(--border)",
  },
  rowHeader: {
    width: "100%",
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: "4px 8px",
    textAlign: "left",
  },
  time: {
    fontSize: 10,
    fontFamily: "var(--font-mono)",
    color: "var(--text-dim)",
    flexShrink: 0,
  },
  badge: {
    fontSize: 10,
    fontFamily: "var(--font-mono)",
    padding: "1px 6px",
    borderRadius: 8,
    background: "var(--accent-dim)",
    color: "var(--accent)",
    fontWeight: 600,
  },
  chevron: {
    marginLeft: "auto",
    fontSize: 10,
    color: "var(--text-dim)",
  },
  body: {
    margin: 0,
    padding: "6px 10px 8px",
    fontSize: 11,
    fontFamily: "var(--font-mono)",
    color: "var(--text-muted)",
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
    borderTop: "1px solid var(--border)",
    maxHeight: 140,
    overflowY: "auto",
  },
};
