import { useState } from "react";
import type { TodoItem } from "../api/client";

interface Props {
  todos: TodoItem[];
}

function StatusIndicator({ status }: { status: TodoItem["status"] }) {
  if (status === "completed") {
    return <span style={styles.iconCompleted}>✓</span>;
  }
  if (status === "in_progress") {
    return <span style={styles.iconInProgress}>◐</span>;
  }
  return <span style={styles.iconPending}>○</span>;
}

export default function TodoChecklist({ todos }: Props) {
  const [open, setOpen] = useState(true);
  if (todos.length === 0) return null;

  const completedCount = todos.filter((t) => t.status === "completed").length;

  return (
    <div style={styles.wrap}>
      <button type="button" style={styles.toggle} onClick={() => setOpen((v) => !v)}>
        {open ? "▾" : "▸"} Tasks ({completedCount}/{todos.length})
      </button>
      {open && (
        <div style={styles.list}>
          {todos.map((todo) => (
            <div
              key={todo.id}
              style={{
                ...styles.item,
                ...(todo.status === "completed" ? styles.itemCompleted : {}),
              }}
            >
              <StatusIndicator status={todo.status} />
              <span
                style={{
                  ...styles.content,
                  ...(todo.status === "completed" ? styles.contentCompleted : {}),
                  ...(todo.status === "in_progress" ? styles.contentInProgress : {}),
                }}
              >
                {todo.content}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  wrap: {
    marginBottom: 8,
  },
  toggle: {
    fontSize: 11,
    color: "var(--text-muted)",
    padding: "2px 0",
    background: "transparent",
  },
  list: {
    marginTop: 4,
    display: "flex",
    flexDirection: "column",
    gap: 4,
    border: "1px solid var(--border)",
    borderRadius: "var(--radius-sm)",
    overflow: "hidden",
    background: "var(--bg-sidebar)",
  },
  item: {
    display: "flex",
    alignItems: "flex-start",
    gap: 8,
    padding: "6px 10px",
    fontSize: 12,
    lineHeight: 1.45,
    borderBottom: "1px solid var(--border)",
  },
  itemCompleted: {
    opacity: 0.7,
  },
  content: {
    flex: 1,
    color: "var(--text-muted)",
  },
  contentCompleted: {
    textDecoration: "line-through",
    color: "var(--text-dim)",
  },
  contentInProgress: {
    color: "var(--accent)",
  },
  iconPending: {
    flexShrink: 0,
    color: "var(--text-dim)",
    fontSize: 12,
    lineHeight: "1.45",
  },
  iconInProgress: {
    flexShrink: 0,
    color: "var(--accent)",
    fontSize: 12,
    lineHeight: "1.45",
  },
  iconCompleted: {
    flexShrink: 0,
    color: "var(--accent)",
    fontSize: 12,
    lineHeight: "1.45",
    fontWeight: 700,
  },
};
