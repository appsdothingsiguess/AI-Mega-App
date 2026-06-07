import { useEffect, useState } from "react";
import {
  listProjects,
  createProject,
  ProjectSummary,
} from "../api/client";

const HOME_PROJECT_NAME = "__home__";

interface Props {
  onSelectProject: (id: string) => void;
  threadsVersion?: number;
}

export default function ProjectGrid({
  onSelectProject,
  threadsVersion = 0,
}: Props) {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    try {
      const list = await listProjects();
      setProjects(list.filter((p) => p.name !== HOME_PROJECT_NAME));
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load projects");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  useEffect(() => {
    refresh();
  }, [threadsVersion]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleCreate = async () => {
    const name = newName.trim();
    if (!name) return;
    try {
      setError(null);
      const proj = await createProject(name);
      setProjects((prev) => [...prev, proj]);
      setCreating(false);
      setNewName("");
      onSelectProject(proj.id);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to create project");
    }
  };

  return (
    <div style={styles.root}>
      <div style={styles.header}>
        <h1 style={styles.title}>Projects</h1>
        <p style={styles.subtitle}>
          Organize chats with sources and custom instructions
        </p>
      </div>

      {error && <div style={styles.errorBanner}>{error}</div>}

      <div style={styles.grid}>
        {loading && <div style={styles.muted}>Loading projects…</div>}

        {!loading &&
          projects.map((p) => (
            <button
              key={p.id}
              style={styles.card}
              onClick={() => onSelectProject(p.id)}
            >
              <div style={styles.cardIcon}>📁</div>
              <div style={styles.cardName}>{p.name}</div>
              <div style={styles.cardMeta}>
                {p.file_count} file{p.file_count !== 1 ? "s" : ""} ·{" "}
                {p.thread_count} chat{p.thread_count !== 1 ? "s" : ""}
              </div>
            </button>
          ))}

        {creating ? (
          <div style={styles.createCard}>
            <input
              autoFocus
              style={styles.nameInput}
              value={newName}
              placeholder="Project name…"
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleCreate();
                if (e.key === "Escape") {
                  setCreating(false);
                  setNewName("");
                }
              }}
            />
            <div style={styles.createActions}>
              <button style={styles.confirmBtn} onClick={handleCreate}>
                Create
              </button>
              <button
                style={styles.cancelBtn}
                onClick={() => {
                  setCreating(false);
                  setNewName("");
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <button
            style={styles.newCard}
            onClick={() => {
              setCreating(true);
              setNewName("");
              setError(null);
            }}
          >
            <div style={styles.newIcon}>+</div>
            <div style={styles.newLabel}>New Project</div>
          </button>
        )}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  root: {
    flex: 1,
    overflowY: "auto",
    padding: "32px 40px",
    background: "var(--bg-panel)",
  },
  header: {
    marginBottom: 28,
  },
  title: {
    fontSize: 22,
    fontWeight: 700,
    margin: 0,
    marginBottom: 6,
  },
  subtitle: {
    margin: 0,
    fontSize: 14,
    color: "var(--text-muted)",
  },
  errorBanner: {
    marginBottom: 16,
    padding: "8px 12px",
    borderRadius: "var(--radius-sm)",
    background: "var(--danger-dim)",
    color: "var(--danger)",
    fontSize: 13,
  },
  grid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))",
    gap: 16,
    alignItems: "start",
  },
  muted: {
    gridColumn: "1 / -1",
    color: "var(--text-muted)",
    fontSize: 14,
  },
  card: {
    textAlign: "left",
    padding: "20px 18px",
    borderRadius: "var(--radius-md)",
    background: "var(--bg-sidebar)",
    border: "1px solid var(--border)",
    display: "flex",
    flexDirection: "column",
    gap: 8,
    minHeight: 120,
    transition: "border-color var(--transition), background var(--transition)",
  },
  cardIcon: {
    fontSize: 28,
    lineHeight: 1,
  },
  cardName: {
    fontSize: 15,
    fontWeight: 600,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  cardMeta: {
    fontSize: 12,
    color: "var(--text-muted)",
    marginTop: "auto",
  },
  newCard: {
    padding: "20px 18px",
    borderRadius: "var(--radius-md)",
    background: "transparent",
    border: "2px dashed var(--border)",
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    minHeight: 120,
    color: "var(--text-muted)",
  },
  newIcon: {
    fontSize: 28,
    fontWeight: 300,
    lineHeight: 1,
    color: "var(--accent)",
  },
  newLabel: {
    fontSize: 14,
    fontWeight: 500,
  },
  createCard: {
    padding: "20px 18px",
    borderRadius: "var(--radius-md)",
    background: "var(--bg-sidebar)",
    border: "1px solid var(--accent)",
    minHeight: 120,
    display: "flex",
    flexDirection: "column",
    gap: 10,
  },
  nameInput: {
    width: "100%",
    padding: "8px 10px",
    borderRadius: "var(--radius-sm)",
    fontSize: 14,
  },
  createActions: {
    display: "flex",
    gap: 8,
    marginTop: "auto",
  },
  confirmBtn: {
    flex: 1,
    padding: "6px 0",
    background: "var(--accent)",
    color: "#000",
    borderRadius: "var(--radius-sm)",
    fontWeight: 600,
    fontSize: 13,
  },
  cancelBtn: {
    flex: 1,
    padding: "6px 0",
    background: "var(--bg-hover)",
    borderRadius: "var(--radius-sm)",
    fontSize: 13,
  },
};
