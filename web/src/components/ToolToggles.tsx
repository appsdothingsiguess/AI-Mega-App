export type ToolToggleKey =
  | "web_search"
  | "file_ops"
  | "bash"
  | "grep"
  | "glob"
  | "web_fetch";

export type ToolTogglesState = Record<ToolToggleKey, boolean>;

export const DEFAULT_TOOL_TOGGLES: ToolTogglesState = {
  web_search: true,
  file_ops: true,
  bash: true,
  grep: true,
  glob: true,
  web_fetch: true,
};

const TOOL_LABELS: Record<ToolToggleKey, string> = {
  web_search: "Web search",
  file_ops: "File ops",
  bash: "Bash",
  grep: "Grep",
  glob: "Glob",
  web_fetch: "Fetch URL",
};

interface Props {
  toggles: ToolTogglesState;
  onChange: (toggles: ToolTogglesState) => void;
  disabled?: boolean;
}

export default function ToolToggles({ toggles, onChange, disabled }: Props) {
  const handleToggle = (key: ToolToggleKey) => {
    onChange({ ...toggles, [key]: !toggles[key] });
  };

  return (
    <div style={styles.row}>
      {(Object.keys(TOOL_LABELS) as ToolToggleKey[]).map((key) => (
        <label key={key} style={styles.toggle}>
          <input
            type="checkbox"
            checked={toggles[key]}
            onChange={() => handleToggle(key)}
            disabled={disabled}
            style={styles.checkbox}
          />
          <span style={styles.label}>{TOOL_LABELS[key]}</span>
        </label>
      ))}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  row: {
    display: "flex",
    flexWrap: "wrap",
    gap: "8px 14px",
    alignItems: "center",
  },
  toggle: {
    display: "flex",
    alignItems: "center",
    gap: 5,
    fontSize: 11,
    color: "var(--text-muted)",
    cursor: "pointer",
    userSelect: "none",
  },
  checkbox: {
    accentColor: "var(--accent)",
    cursor: "pointer",
  },
  label: {
    whiteSpace: "nowrap",
  },
};
