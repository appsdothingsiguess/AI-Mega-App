import { useCallback, useEffect, useRef, useState } from "react";
import {
  getSources,
  updateSources,
  uploadSources,
  deleteSource,
  syncProject,
  DocFileInfo,
  SourcesState,
} from "../api/client";

interface Props {
  projectId: string | null;
  onSourcesChange?: () => void;
}

function fileIcon(name: string): string {
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  if (ext === "pdf") return "📄";
  if (ext === "md") return "📝";
  return "📃";
}

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function SourcesPanel({ projectId, onSourcesChange }: Props) {
  const [state, setState] = useState<SourcesState | null>(null);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [syncMsg, setSyncMsg] = useState<string | null>(null);
  const [deletingName, setDeletingName] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    try {
      const s = await getSources(projectId);
      setState(s);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load sources");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    setState(null);
    setSyncMsg(null);
    setError(null);
    refresh();
  }, [projectId, refresh]);

  const handleToggle = (file: DocFileInfo) => {
    if (!state || !projectId) return;
    const files = state.files;
    const allEnabled = files.length > 0 && files.every((f) => f.enabled);

    let newEnabled: string[];
    if (file.enabled) {
      // Disabling one: if currently "all enabled" (empty list) semantics,
      // switch to explicit list with this one removed
      if (state.files.every((f) => f.enabled)) {
        newEnabled = files.filter((f) => f.name !== file.name).map((f) => f.name);
      } else {
        newEnabled = files.filter((f) => f.enabled && f.name !== file.name).map((f) => f.name);
      }
    } else {
      newEnabled = [...files.filter((f) => f.enabled).map((f) => f.name), file.name];
    }

    // Optimistic update
    setState((prev) =>
      prev
        ? {
            ...prev,
            files: prev.files.map((f) =>
              f.name === file.name ? { ...f, enabled: !f.enabled } : f,
            ),
          }
        : prev,
    );

    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      try {
        const updated = await updateSources(projectId, newEnabled, state.default_new_enabled);
        setState(updated);
        onSourcesChange?.();
      } catch {
        refresh();
      }
    }, 300);
  };

  const handleSelectAll = async (enable: boolean) => {
    if (!state || !projectId) return;
    const enabled = enable ? state.files.map((f) => f.name) : [];
    setState((prev) =>
      prev ? { ...prev, files: prev.files.map((f) => ({ ...f, enabled: enable })) } : prev,
    );
    try {
      const updated = await updateSources(projectId, enabled, state.default_new_enabled);
      setState(updated);
      onSourcesChange?.();
    } catch {
      refresh();
    }
  };

  const handleResetAll = async () => {
    if (!state || !projectId) return;
    setState((prev) =>
      prev ? { ...prev, files: prev.files.map((f) => ({ ...f, enabled: true })) } : prev,
    );
    try {
      const updated = await updateSources(projectId, [], state.default_new_enabled);
      setState(updated);
      onSourcesChange?.();
    } catch {
      refresh();
    }
  };

  const handleUpload = async (files: FileList | File[]) => {
    if (!projectId) return;
    const arr = Array.from(files);
    if (arr.length === 0) return;
    setUploading(true);
    setError(null);
    try {
      const updated = await uploadSources(projectId, arr);
      setState(updated);
      onSourcesChange?.();
      setSyncMsg(`Uploaded & indexed ${arr.length} file${arr.length > 1 ? "s" : ""}`);
      setTimeout(() => setSyncMsg(null), 3000);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  const handleSync = async () => {
    if (!projectId) return;
    setSyncing(true);
    setError(null);
    try {
      const r = await syncProject(projectId);
      await refresh();
      onSourcesChange?.();
      setSyncMsg(`Synced — ${r.chunk_count} chunk${r.chunk_count !== 1 ? "s" : ""} indexed`);
      setTimeout(() => setSyncMsg(null), 3000);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Sync failed");
    } finally {
      setSyncing(false);
    }
  };

  const handleDelete = async (name: string) => {
    if (!projectId) return;
    if (!window.confirm(`Delete "${name}" and remove its index chunks?`)) return;
    setDeletingName(name);
    try {
      await deleteSource(projectId, name);
      setState((prev) =>
        prev ? { ...prev, files: prev.files.filter((f) => f.name !== name) } : prev,
      );
      onSourcesChange?.();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Delete failed");
    } finally {
      setDeletingName(null);
    }
  };

  if (!projectId) {
    return (
      <div style={styles.empty}>
        <span style={{ color: "var(--text-dim)" }}>Select a project</span>
      </div>
    );
  }

  const files = state?.files ?? [];
  const enabledCount = files.filter((f) => f.enabled).length;

  return (
    <div
      style={{ ...styles.root, ...(dragOver ? styles.dragActive : {}) }}
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        handleUpload(e.dataTransfer.files);
      }}
    >
      <div style={styles.header}>
        <span style={styles.title}>Sources</span>
        <div style={{ display: "flex", gap: 4 }}>
          <button
            style={styles.iconBtn}
            onClick={handleSync}
            disabled={syncing}
            title="Re-index all files"
          >
            {syncing ? "⟳" : "↺"}
          </button>
          <button
            style={{ ...styles.iconBtn, color: "var(--accent)" }}
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading}
            title="Add source files"
          >
            +
          </button>
        </div>
      </div>

      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept=".txt,.md,.pdf"
        style={{ display: "none" }}
        onChange={(e) => e.target.files && handleUpload(e.target.files)}
      />

      {files.length > 0 && (
        <div style={styles.toolbar}>
          <button style={styles.tinyBtn} onClick={() => handleSelectAll(true)}>
            All
          </button>
          <button
            style={styles.tinyBtn}
            onClick={handleResetAll}
            title="Enable all sources (empty selection = all documents active for RAG)"
          >
            Reset
          </button>
          <span style={styles.countChip}>
            {enabledCount}/{files.length} active
          </span>
        </div>
      )}

      {error && <div style={styles.errorBanner}>{error}</div>}
      {syncMsg && <div style={styles.successBanner}>{syncMsg}</div>}

      <div style={styles.list}>
        {loading && <div style={styles.muted}>Loading…</div>}
        {!loading && files.length === 0 && (
          <div style={styles.dropHint}>
            <div style={{ fontSize: 28, marginBottom: 8 }}>📂</div>
            <div>Drop files here or click <strong style={{ color: "var(--accent)" }}>+</strong></div>
            <div style={{ fontSize: 11, marginTop: 4, color: "var(--text-dim)" }}>
              .txt · .md · .pdf
            </div>
          </div>
        )}
        {files.map((file) => (
          <div key={file.name} style={styles.fileRow}>
            <label style={styles.fileLabel}>
              <input
                type="checkbox"
                checked={file.enabled}
                onChange={() => handleToggle(file)}
                style={styles.checkbox}
              />
              <span style={styles.fileIcon}>{fileIcon(file.name)}</span>
              <span style={styles.fileName} title={file.name}>
                {file.name}
              </span>
            </label>
            <div style={styles.fileMeta}>
              <span style={{ color: file.ingested ? "var(--success)" : "var(--text-dim)" }}>
                {file.ingested ? "●" : "○"}
              </span>
              <span style={styles.fileSize}>{humanSize(file.size)}</span>
              <button
                style={styles.deleteBtn}
                onClick={() => handleDelete(file.name)}
                disabled={deletingName === file.name}
                title={`Delete ${file.name}`}
              >
                ×
              </button>
            </div>
          </div>
        ))}
      </div>

      {uploading && (
        <div style={styles.uploadingOverlay}>Uploading & indexing…</div>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  root: {
    display: "flex",
    flexDirection: "column",
    flex: 1,
    minHeight: 140,
    overflow: "hidden",
    position: "relative",
    transition: "background var(--transition)",
  },
  dragActive: {
    background: "var(--accent-dim2)",
    outline: "2px dashed var(--accent)",
    outlineOffset: -2,
  },
  header: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "10px 12px 8px",
    flexShrink: 0,
  },
  title: {
    fontWeight: 600,
    fontSize: 12,
    textTransform: "uppercase",
    letterSpacing: "0.08em",
    color: "var(--text-muted)",
  },
  iconBtn: {
    width: 24,
    height: 24,
    borderRadius: "var(--radius-sm)",
    background: "var(--bg-hover)",
    fontSize: 14,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    fontWeight: 700,
    color: "var(--text-muted)",
  },
  toolbar: {
    display: "flex",
    gap: 6,
    alignItems: "center",
    padding: "0 12px 8px",
    flexShrink: 0,
  },
  tinyBtn: {
    padding: "2px 7px",
    fontSize: 11,
    background: "var(--bg-hover)",
    borderRadius: "var(--radius-sm)",
    color: "var(--text-muted)",
  },
  countChip: {
    marginLeft: "auto",
    fontSize: 11,
    color: "var(--accent)",
    background: "var(--accent-dim)",
    padding: "2px 7px",
    borderRadius: 10,
  } as React.CSSProperties,
  errorBanner: {
    margin: "0 10px 6px",
    padding: "6px 10px",
    borderRadius: "var(--radius-sm)",
    background: "var(--danger-dim)",
    color: "var(--danger)",
    fontSize: 12,
  },
  successBanner: {
    margin: "0 10px 6px",
    padding: "6px 10px",
    borderRadius: "var(--radius-sm)",
    background: "rgba(34,197,94,0.1)",
    color: "var(--success)",
    fontSize: 12,
  },
  list: {
    flex: 1,
    overflowY: "auto",
    padding: "2px 0 8px",
  },
  dropHint: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    padding: "24px 16px",
    color: "var(--text-muted)",
    fontSize: 13,
    textAlign: "center",
    gap: 0,
  },
  muted: {
    padding: 12,
    color: "var(--text-muted)",
    fontSize: 12,
  },
  fileRow: {
    display: "flex",
    alignItems: "center",
    padding: "6px 10px",
    gap: 6,
    borderBottom: "1px solid transparent",
    cursor: "default",
  },
  fileLabel: {
    display: "flex",
    alignItems: "center",
    gap: 6,
    flex: 1,
    minWidth: 0,
    cursor: "pointer",
  },
  checkbox: {
    flexShrink: 0,
    accentColor: "var(--accent)",
  },
  fileIcon: {
    flexShrink: 0,
    fontSize: 14,
  },
  fileName: {
    flex: 1,
    minWidth: 0,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
    fontSize: 12,
  },
  fileMeta: {
    display: "flex",
    alignItems: "center",
    gap: 6,
    flexShrink: 0,
  },
  fileSize: {
    fontSize: 10,
    color: "var(--text-dim)",
  },
  deleteBtn: {
    width: 18,
    height: 18,
    borderRadius: "var(--radius-sm)",
    fontSize: 14,
    lineHeight: 1,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: "var(--text-dim)",
    opacity: 0.6,
  },
  empty: {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    flex: 1,
    fontSize: 12,
  },
  uploadingOverlay: {
    position: "absolute",
    inset: 0,
    background: "rgba(0,0,0,0.6)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    fontSize: 13,
    color: "var(--accent)",
    backdropFilter: "blur(2px)",
  },
};
