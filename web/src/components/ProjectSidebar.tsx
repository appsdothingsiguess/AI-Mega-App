import { useCallback, useEffect, useState } from "react";
import {
  listThreads,
  createThread,
  deleteThread,
  clearThreadMessages,
  ThreadSummary,
} from "../api/client";
import type { AppView } from "../App";

interface Props {
  view: AppView;
  projectId: string | null;
  onNavChange: (view: AppView) => void;
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
  view,
  projectId,
  onNavChange,
  threadId,
  onThreadSelect,
  onThreadsChange,
  threadsVersion = 0,
}: Props) {
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [threadsLoading, setThreadsLoading] = useState(false);
  const [threadBusy, setThreadBusy] = useState<string | null>(null);

  const loadThreads = useCallback(
    async (opts?: { autoSelect?: boolean }) => {
      if (!projectId) {
        setThreads([]);
        return;
      }
      setThreadsLoading(true);
      try {
        let list = sortThreads(await listThreads(projectId));
        if (list.length === 0 && opts?.autoSelect !== false) {
          const t = await createThread(projectId, "Chat");
          list = [t];
          onThreadsChange?.();
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
    [projectId, threadId, onThreadSelect, onThreadsChange],
  );

  useEffect(() => {
    setError(null);
    if (!projectId) {
      setThreads([]);
      return;
    }
    loadThreads({ autoSelect: true });
  }, [projectId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!projectId) return;
    loadThreads({ autoSelect: false });
  }, [threadsVersion]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleNewChat = async () => {
    if (!projectId) return;
    try {
      setError(null);
      const t = await createThread(projectId);
      setThreads((prev) => sortThreads([t, ...prev]));
      onThreadSelect(t.id);
      onThreadsChange?.();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to create chat");
    }
  };

  const handleClearThread = async (id: string) => {
    if (!projectId) return;
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
      const updated = await clearThreadMessages(projectId, id);
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
    if (!projectId) return;
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
      await deleteThread(projectId, id);
      const remaining = threads.filter((t) => t.id !== id);
      if (remaining.length === 0) {
        const t = await createThread(projectId, "Chat");
        setThreads([t]);
        onThreadSelect(t.id);
      } else {
        setThreads(remaining);
        if (threadId === id) onThreadSelect(remaining[0].id);
      }
      onThreadsChange?.();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to delete chat");
    } finally {
      setThreadBusy(null);
    }
  };

  const showThreads = view === "home-chat" || view === "project-workspace";

  return (
    <div style={styles.root}>
      <div style={styles.nav}>
        <button
          style={{
            ...styles.navItem,
            ...(view === "home-chat" ? styles.navItemActive : {}),
          }}
          onClick={() => onNavChange("home-chat")}
          title="New chat"
        >
          <span style={styles.navIcon}>💬</span>
          <span>New Chat</span>
        </button>
        <button
          style={{
            ...styles.navItem,
            ...(view === "projects" || view === "project-workspace"
              ? styles.navItemActive
              : {}),
          }}
          onClick={() => onNavChange("projects")}
          title="Projects"
        >
          <span style={styles.navIcon}>📁</span>
          <span>Projects</span>
        </button>
      </div>

      {showThreads && (
        <div style={styles.threadsSection}>
          <div style={styles.header}>
            <span style={styles.title}>Chats</span>
            <button
              style={styles.newBtn}
              onClick={handleNewChat}
              disabled={!projectId}
              title="New chat"
            >
              +
            </button>
          </div>

          {error && <div style={styles.errorBanner}>{error}</div>}

          <div style={styles.threadList}>
            {!projectId && (
              <div style={styles.muted}>Loading…</div>
            )}
            {projectId && threadsLoading && (
              <div style={styles.muted}>Loading chats…</div>
            )}
            {projectId && !threadsLoading && threads.length === 0 && (
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
    flex: 1,
    minHeight: 0,
    overflow: "hidden",
  },
  nav: {
    display: "flex",
    flexDirection: "column",
    gap: 2,
    padding: "10px 8px",
    borderBottom: "1px solid var(--border)",
    flexShrink: 0,
  },
  navItem: {
    display: "flex",
    alignItems: "center",
    gap: 10,
    width: "100%",
    textAlign: "left",
    padding: "9px 10px",
    borderRadius: "var(--radius-sm)",
    fontSize: 14,
    fontWeight: 500,
    color: "var(--text-muted)",
  },
  navItemActive: {
    background: "var(--accent-dim)",
    color: "var(--text)",
    borderLeft: "2px solid var(--accent)",
    paddingLeft: 8,
  },
  navIcon: {
    fontSize: 16,
    lineHeight: 1,
    flexShrink: 0,
  },
  threadsSection: {
    display: "flex",
    flexDirection: "column",
    flex: 1,
    minHeight: 0,
    overflow: "hidden",
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
  errorBanner: {
    margin: "0 10px 6px",
    padding: "6px 10px",
    borderRadius: "var(--radius-sm)",
    background: "var(--danger-dim)",
    color: "var(--danger)",
    fontSize: 11,
    flexShrink: 0,
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
