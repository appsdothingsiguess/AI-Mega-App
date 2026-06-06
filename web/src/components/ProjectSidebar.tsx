import { useCallback, useEffect, useState } from "react";
import {
  listProjects,
  createProject,
  listThreads,
  createThread,
  deleteThread,
  clearThreadMessages,
  ProjectSummary,
  ThreadSummary,
} from "../api/client";

interface Props {
  selectedId: string | null;
  onSelect: (id: string) => void;
  threadId: string | null;
  onThreadSelect: (id: string | null) => void;
  onThreadsChange?: () => void;
  threadsVersion?: number;
}

function sortThreads(threads: ThreadSummary[]): ThreadSummary[] {
  return [...threads].sort(
    (a, b) =>
      new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  );
}

function threadLabel(t: ThreadSummary): string {
  return t.title?.trim() || t.id.slice(0, 12);
}

export default function ProjectSidebar({
  selectedId,
  onSelect,
  threadId,
  onThreadSelect,
  onThreadsChange,
  threadsVersion = 0,
}: Props) {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [threadsLoading, setThreadsLoading] = useState(false);
  const [threadBusy, setThreadBusy] = useState<string | null>(null);

  const refreshProjects = async () => {
    try {
      const list = await listProjects();
      setProjects(list);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  };

  const loadThreads = useCallback(
    async (opts?: { autoSelect?: boolean }) => {
      if (!selectedId) {
        setThreads([]);
        return;
      }
      setThreadsLoading(true);
      try {
        let list = sortThreads(await listThreads(selectedId));
        if (list.length === 0 && opts?.autoSelect !== false) {
          const t = await createThread(selectedId, "Chat");
          list = [t];
          onThreadsChange?.();
          await refreshProjects();
        }
        setThreads(list);
        if (opts?.autoSelect !== false) {
          const active =
            threadId && list.some((t) => t.id === threadId)
              ? threadId
              : list[0]?.id ?? null;
          if (active !== threadId) onThreadSelect(active);
        }
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : "Failed to load chats");
      } finally {
        setThreadsLoading(false);
      }
    },
    [selectedId, threadId, onThreadSelect, onThreadsChange],
  );

  useEffect(() => {
    refreshProjects();
  }, []);

  useEffect(() => {
    refreshProjects();
  }, [threadsVersion]);

  useEffect(() => {
    setError(null);
    if (!selectedId) {
      setThreads([]);
      return;
    }
    loadThreads({ autoSelect: true });
  }, [selectedId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!selectedId) return;
    loadThreads({ autoSelect: false });
  }, [threadsVersion]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleCreate = async () => {
    const name = newName.trim();
    if (!name) return;
    try {
      setError(null);
      const proj = await createProject(name);
      setProjects((p) => [...p, proj]);
      onSelect(proj.id);
      setCreating(false);
      setNewName("");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to create project");
    }
  };

  const handleNewChat = async () => {
    if (!selectedId) return;
    try {
      setError(null);
      const t = await createThread(selectedId);
      setThreads((prev) => sortThreads([t, ...prev]));
      onThreadSelect(t.id);
      onThreadsChange?.();
      await refreshProjects();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to create chat");
    }
  };

  const handleClearThread = async (id: string) => {
    if (!selectedId) return;
    if (
      !window.confirm(
        "Clear all messages in this chat? The chat will remain in the list.",
      )
    ) {
      return;
    }
    setThreadBusy(id);
    try {
      setError(null);
      const updated = await clearThreadMessages(selectedId, id);
      setThreads((prev) =>
        prev.map((t) => (t.id === id ? { ...t, ...updated } : t)),
      );
      onThreadsChange?.();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to clear chat");
    } finally {
      setThreadBusy(null);
    }
  };

  const handleDeleteThread = async (id: string) => {
    if (!selectedId) return;
    if (
      !window.confirm(
        "Delete this chat permanently? This cannot be undone.",
      )
    ) {
      return;
    }
    setThreadBusy(id);
    try {
      setError(null);
      await deleteThread(selectedId, id);
      const remaining = threads.filter((t) => t.id !== id);
      if (remaining.length === 0) {
        const t = await createThread(selectedId, "Chat");
        setThreads([t]);
        onThreadSelect(t.id);
      } else {
        setThreads(remaining);
        if (threadId === id) onThreadSelect(remaining[0].id);
      }
      onThreadsChange?.();
      await refreshProjects();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to delete chat");
    } finally {
      setThreadBusy(null);
    }
  };

  return (
    <div style={styles.root}>
      <div style={styles.projectsSection}>
        <div style={styles.header}>
          <span style={styles.title}>Projects</span>
          <button
            style={styles.newBtn}
            onClick={() => {
              setCreating(true);
              setNewName("");
              setError(null);
            }}
            title="New project"
          >
            +
          </button>
        </div>

        {creating && (
          <div style={styles.createBox}>
            <input
              autoFocus
              style={styles.nameInput}
              value={newName}
              placeholder="Project name…"
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleCreate();
                if (e.key === "Escape") setCreating(false);
              }}
            />
            <div style={{ display: "flex", gap: 4, marginTop: 6 }}>
              <button style={styles.confirmBtn} onClick={handleCreate}>
                Create
              </button>
              <button style={styles.cancelBtn} onClick={() => setCreating(false)}>
                Cancel
              </button>
            </div>
            {error && <div style={styles.errorText}>{error}</div>}
          </div>
        )}

        <div style={styles.projectList}>
          {loading && <div style={styles.muted}>Loading…</div>}
          {!loading && projects.length === 0 && (
            <div style={styles.muted}>No projects yet</div>
          )}
          {projects.map((p) => (
            <button
              key={p.id}
              style={{
                ...styles.projectItem,
                ...(p.id === selectedId ? styles.projectItemActive : {}),
              }}
              onClick={() => onSelect(p.id)}
            >
              <div style={styles.projectName}>{p.name}</div>
              <div style={styles.projectMeta}>
                {p.file_count} file{p.file_count !== 1 ? "s" : ""} ·{" "}
                {p.thread_count} chat{p.thread_count !== 1 ? "s" : ""}
              </div>
            </button>
          ))}
        </div>
      </div>

      {selectedId && (
        <div style={styles.threadsSection}>
          <div style={styles.header}>
            <span style={styles.title}>Chats</span>
            <button
              style={styles.newBtn}
              onClick={handleNewChat}
              title="New chat"
            >
              +
            </button>
          </div>

          {error && !creating && (
            <div style={styles.errorBanner}>{error}</div>
          )}

          <div style={styles.threadList}>
            {threadsLoading && <div style={styles.muted}>Loading chats…</div>}
            {!threadsLoading && threads.length === 0 && (
              <div style={styles.muted}>No chats yet</div>
            )}
            {threads.map((t) => (
              <div
                key={t.id}
                style={{
                  ...styles.threadRow,
                  ...(t.id === threadId ? styles.threadRowActive : {}),
                }}
              >
                <button
                  style={styles.threadSelectBtn}
                  onClick={() => onThreadSelect(t.id)}
                  title={t.title ?? t.id}
                >
                  <div style={styles.threadTitle}>{threadLabel(t)}</div>
                  <div style={styles.threadMeta}>
                    {t.message_count} msg{t.message_count !== 1 ? "s" : ""}
                  </div>
                </button>
                <div style={styles.threadActions}>
                  <button
                    style={styles.threadActionBtn}
                    onClick={() => handleClearThread(t.id)}
                    disabled={threadBusy === t.id}
                    title="Clear messages"
                  >
                    ⌫
                  </button>
                  <button
                    style={{ ...styles.threadActionBtn, color: "var(--danger)" }}
                    onClick={() => handleDeleteThread(t.id)}
                    disabled={threadBusy === t.id}
                    title="Delete chat"
                  >
                    ×
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  root: {
    display: "flex",
    flexDirection: "column",
    flex: "1 1 55%",
    minHeight: 120,
    overflow: "hidden",
    borderBottom: "1px solid var(--border)",
  },
  projectsSection: {
    display: "flex",
    flexDirection: "column",
    flex: "0 1 42%",
    minHeight: 96,
    maxHeight: "42%",
    overflow: "hidden",
  },
  threadsSection: {
    display: "flex",
    flexDirection: "column",
    flex: 1,
    minHeight: 0,
    overflow: "hidden",
    borderTop: "1px solid var(--border)",
  },
  header: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "12px 12px 8px",
    borderBottom: "1px solid var(--border)",
    flexShrink: 0,
  },
  title: {
    fontWeight: 600,
    fontSize: 12,
    textTransform: "uppercase",
    letterSpacing: "0.08em",
    color: "var(--text-muted)",
  },
  newBtn: {
    width: 22,
    height: 22,
    borderRadius: "var(--radius-sm)",
    background: "var(--bg-hover)",
    fontSize: 16,
    lineHeight: 1,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: "var(--accent)",
    fontWeight: 700,
  },
  createBox: {
    padding: "10px 10px 6px",
    borderBottom: "1px solid var(--border)",
    flexShrink: 0,
  },
  nameInput: {
    width: "100%",
    padding: "6px 8px",
    borderRadius: "var(--radius-sm)",
  },
  confirmBtn: {
    flex: 1,
    padding: "5px 0",
    background: "var(--accent)",
    color: "#000",
    borderRadius: "var(--radius-sm)",
    fontWeight: 600,
    fontSize: 12,
  },
  cancelBtn: {
    flex: 1,
    padding: "5px 0",
    background: "var(--bg-hover)",
    borderRadius: "var(--radius-sm)",
    fontSize: 12,
  },
  errorText: {
    marginTop: 4,
    fontSize: 11,
    color: "var(--danger)",
  },
  errorBanner: {
    margin: "0 10px 6px",
    padding: "6px 10px",
    borderRadius: "var(--radius-sm)",
    background: "var(--danger-dim)",
    color: "var(--danger)",
    fontSize: 11,
    flexShrink: 0,
  },
  projectList: {
    flex: 1,
    overflowY: "auto",
    padding: "4px 0",
  },
  threadList: {
    flex: 1,
    overflowY: "auto",
    padding: "4px 0 8px",
  },
  muted: {
    padding: "12px",
    color: "var(--text-muted)",
    fontSize: 12,
  },
  projectItem: {
    width: "100%",
    textAlign: "left",
    padding: "9px 12px",
    borderRadius: 0,
    borderBottom: "1px solid transparent",
    display: "block",
  },
  projectItemActive: {
    background: "var(--accent-dim)",
    borderLeft: "2px solid var(--accent)",
    paddingLeft: 10,
  },
  projectName: {
    fontWeight: 500,
    marginBottom: 2,
  },
  projectMeta: {
    fontSize: 11,
    color: "var(--text-muted)",
  },
  threadRow: {
    display: "flex",
    alignItems: "stretch",
    gap: 0,
    margin: "0 4px",
    borderRadius: "var(--radius-sm)",
  },
  threadRowActive: {
    background: "var(--accent-dim)",
    borderLeft: "2px solid var(--accent)",
  },
  threadSelectBtn: {
    flex: 1,
    textAlign: "left",
    padding: "8px 8px 8px 10px",
    minWidth: 0,
    display: "block",
  },
  threadTitle: {
    fontSize: 13,
    fontWeight: 500,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  threadMeta: {
    fontSize: 10,
    color: "var(--text-muted)",
    marginTop: 2,
  },
  threadActions: {
    display: "flex",
    alignItems: "center",
    gap: 2,
    paddingRight: 4,
    flexShrink: 0,
  },
  threadActionBtn: {
    width: 22,
    height: 22,
    borderRadius: "var(--radius-sm)",
    fontSize: 12,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: "var(--text-muted)",
    opacity: 0.85,
  },
};
