import { useEffect, useMemo, useState } from "react";
import {
  getSettings,
  IntentLabel,
  ModelsConfig,
  SettingsSnapshot,
} from "../api/client";

const INTENT_LABELS: { key: IntentLabel; label: string }[] = [
  { key: "general_chat", label: "General chat" },
  { key: "web_search", label: "Web search" },
  { key: "deep_research", label: "Deep research" },
  { key: "coding_basic", label: "Coding (basic)" },
  { key: "coding_advanced", label: "Coding (advanced)" },
  { key: "bash", label: "Bash" },
  { key: "pdf_gen", label: "PDF gen" },
  { key: "file_ops", label: "File ops" },
  { key: "vision", label: "Vision" },
];

interface Props {
  modelOverride: string | null;
  onModelOverrideChange: (alias: string | null) => void;
  disabled?: boolean;
}

function uniqueModelAliases(models: ModelsConfig): string[] {
  return [...new Set(Object.values(models))].sort();
}

export default function ModelSelector({
  modelOverride,
  onModelOverrideChange,
  disabled,
}: Props) {
  const [settings, setSettings] = useState<SettingsSnapshot | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    getSettings()
      .then((s) => {
        setSettings(s);
        setLoadError(null);
      })
      .catch((e: unknown) => {
        setLoadError(e instanceof Error ? e.message : "Failed to load settings");
      });
  }, []);

  const aliases = useMemo(
    () => (settings ? uniqueModelAliases(settings.models) : []),
    [settings],
  );

  return (
    <div style={styles.wrap}>
      <div style={styles.row}>
        <label style={styles.field}>
          <span style={styles.fieldLabel}>Model override</span>
          <select
            style={styles.select}
            value={modelOverride ?? ""}
            onChange={(e) =>
              onModelOverrideChange(e.target.value || null)
            }
            disabled={disabled || aliases.length === 0}
          >
            <option value="">Auto (router)</option>
            {aliases.map((alias) => (
              <option key={alias} value={alias}>
                {alias}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          style={styles.expandBtn}
          onClick={() => setExpanded((v) => !v)}
          disabled={!settings}
        >
          {expanded ? "Hide routing" : "Routing map"}
        </button>
      </div>
      <div style={styles.hint}>
        Override is sent with each message; backend may ignore until supported.
      </div>
      {loadError && <div style={styles.error}>{loadError}</div>}
      {expanded && settings && (
        <div style={styles.map}>
          {INTENT_LABELS.map(({ key, label }) => (
            <div key={key} style={styles.mapRow}>
              <span style={styles.mapIntent}>{label}</span>
              <span style={styles.mapModel}>{settings.models[key]}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  wrap: {
    display: "flex",
    flexDirection: "column",
    gap: 4,
  },
  row: {
    display: "flex",
    flexWrap: "wrap",
    alignItems: "flex-end",
    gap: 10,
  },
  field: {
    display: "flex",
    flexDirection: "column",
    gap: 3,
    minWidth: 180,
  },
  fieldLabel: {
    fontSize: 10,
    color: "var(--text-dim)",
    textTransform: "uppercase",
    letterSpacing: "0.04em",
  },
  select: {
    padding: "5px 8px",
    borderRadius: "var(--radius-sm)",
    background: "var(--bg-input)",
    color: "var(--text-primary)",
    border: "1px solid var(--border)",
    fontSize: 12,
    fontFamily: "var(--font-mono)",
  },
  expandBtn: {
    padding: "5px 10px",
    borderRadius: "var(--radius-sm)",
    background: "var(--bg-hover)",
    color: "var(--text-muted)",
    fontSize: 11,
  },
  hint: {
    fontSize: 10,
    color: "var(--text-dim)",
    fontStyle: "italic",
  },
  error: {
    fontSize: 11,
    color: "var(--danger)",
  },
  map: {
    marginTop: 4,
    padding: "8px 10px",
    borderRadius: "var(--radius-sm)",
    background: "var(--bg-sidebar)",
    border: "1px solid var(--border)",
    maxHeight: 160,
    overflowY: "auto",
  },
  mapRow: {
    display: "flex",
    justifyContent: "space-between",
    gap: 12,
    fontSize: 11,
    padding: "3px 0",
    borderBottom: "1px solid var(--border)",
  },
  mapIntent: {
    color: "var(--text-muted)",
    flexShrink: 0,
  },
  mapModel: {
    color: "var(--accent)",
    fontFamily: "var(--font-mono)",
    textAlign: "right",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
};
