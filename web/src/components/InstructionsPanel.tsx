import { useEffect, useState } from "react";
import { getInstructions, updateInstructions } from "../api/client";

interface Props {
  projectId: string | null;
}

export default function InstructionsPanel({ projectId }: Props) {
  const [content, setContent] = useState("");
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) {
      setContent("");
      setDirty(false);
      return;
    }
    getInstructions(projectId)
      .then((r) => {
        setContent(r.content);
        setDirty(false);
        setError(null);
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : "Load failed");
      });
  }, [projectId]);

  const handleSave = async () => {
    if (!projectId) return;
    setSaving(true);
    setError(null);
    try {
      await updateInstructions(projectId, content);
      setDirty(false);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const handleChange = (v: string) => {
    setContent(v);
    setDirty(true);
    setSaved(false);
  };

  return (
    <div style={styles.root}>
      <div style={styles.header}>
        <span style={styles.title}>Instructions</span>
        {projectId && (
          <button
            style={{
              ...styles.saveBtn,
              ...(dirty ? styles.saveBtnDirty : {}),
            }}
            onClick={handleSave}
            disabled={saving || !dirty}
          >
            {saving ? "Saving…" : saved ? "✓ Saved" : "Save"}
          </button>
        )}
      </div>

      {error && <div style={styles.errorBanner}>{error}</div>}

      {!projectId ? (
        <div style={styles.placeholder}>Select a project to edit instructions</div>
      ) : (
        <textarea
          style={styles.textarea}
          value={content}
          onChange={(e) => handleChange(e.target.value)}
          placeholder="Describe how the assistant should behave…"
          spellCheck={false}
        />
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  root: {
    display: "flex",
    flexDirection: "column",
    flex: 1,
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
  saveBtn: {
    padding: "4px 12px",
    borderRadius: "var(--radius-sm)",
    background: "var(--bg-hover)",
    fontSize: 12,
    color: "var(--text-muted)",
    fontWeight: 500,
  },
  saveBtnDirty: {
    background: "var(--accent)",
    color: "#000",
  },
  errorBanner: {
    margin: "6px 10px",
    padding: "6px 10px",
    borderRadius: "var(--radius-sm)",
    background: "rgba(224,85,85,0.12)",
    color: "var(--danger)",
    fontSize: 12,
  },
  textarea: {
    flex: 1,
    resize: "none",
    border: "none",
    borderRadius: 0,
    background: "transparent",
    padding: "12px",
    fontSize: 13,
    lineHeight: 1.65,
    color: "var(--text-primary)",
    outline: "none",
    fontFamily: "var(--font-mono)",
  },
  placeholder: {
    flex: 1,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: "var(--text-dim)",
    fontSize: 12,
  },
};
