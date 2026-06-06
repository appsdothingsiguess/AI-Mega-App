import { useEffect, useState } from "react";
import { health, HealthResponse } from "../api/client";

const POLL_INTERVAL = 30_000;

export default function StatusBar() {
  const [status, setStatus] = useState<HealthResponse | null>(null);
  const [error, setError] = useState(false);

  const poll = async () => {
    try {
      const h = await health();
      setStatus(h);
      setError(false);
    } catch {
      setError(true);
    }
  };

  useEffect(() => {
    poll();
    const id = setInterval(poll, POLL_INTERVAL);
    return () => clearInterval(id);
  }, []);

  const dot = error || !status?.ok
    ? <span style={{ color: "var(--danger)" }}>●</span>
    : <span style={{ color: "var(--success)" }}>●</span>;

  const label = error
    ? "LM Studio: unreachable"
    : status
    ? `${status.model_loaded ? status.model : "no model"} · ${status.mode} · ${status.base_url}`
    : "Connecting…";

  return (
    <div style={styles.bar}>
      <span style={styles.dot}>{dot}</span>
      <span style={styles.label}>
        <strong>LM Studio</strong>&nbsp;{label}
      </span>
      {status?.ok && !status.model_loaded && (
        <span style={{ color: "var(--warning)", marginLeft: 8 }}>
          ⚠ No model loaded
        </span>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  bar: {
    height: 32,
    background: "var(--bg-sidebar)",
    borderTop: "1px solid var(--border)",
    display: "flex",
    alignItems: "center",
    padding: "0 16px",
    gap: 8,
    flexShrink: 0,
    fontSize: 12,
    color: "var(--text-muted)",
  },
  dot: {
    fontSize: 10,
    lineHeight: 1,
  },
  label: {
    fontFamily: "var(--font-mono)",
    fontSize: 11,
  },
};
