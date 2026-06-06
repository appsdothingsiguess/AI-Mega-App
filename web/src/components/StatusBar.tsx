import { useEffect, useState } from "react";
import { health, HealthResponse } from "../api/client";

const POLL_INTERVAL = 30_000;

export interface ModelLoadingState {
  model: string;
  estimated_seconds: number;
}

interface Props {
  modelLoading?: ModelLoadingState | null;
  debugTraceOpen?: boolean;
  onToggleDebugTrace?: () => void;
}

function isHealthy(h: HealthResponse): boolean {
  if (h.status) {
    return h.status === "healthy" || h.status === "degraded";
  }
  return h.ok === true;
}

function formatHealthLabel(h: HealthResponse): string {
  if (h.services) {
    const parts: string[] = [];
    const svc = h.services;
    if (svc.ollama) parts.push(`ollama:${svc.ollama.status}`);
    if (svc.qdrant) parts.push(`qdrant:${svc.qdrant.status}`);
    if (svc.litellm) parts.push(`litellm:${svc.litellm.status}`);
    if (svc.remote_provider) parts.push(`remote:${svc.remote_provider.status}`);
    const status = h.status ?? "unknown";
    return parts.length > 0 ? `${status} · ${parts.join(" · ")}` : status;
  }
  if (h.model !== undefined) {
    return `${h.model_loaded ? h.model : "no model"} · ${h.mode ?? ""} · ${h.base_url ?? ""}`;
  }
  return h.message ?? "Connected";
}

export default function StatusBar({
  modelLoading,
  debugTraceOpen = false,
  onToggleDebugTrace,
}: Props) {
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

  const healthy = !error && status && isHealthy(status);
  const dot = error || !healthy
    ? <span style={{ color: "var(--danger)" }}>●</span>
    : <span style={{ color: "var(--success)" }}>●</span>;

  const label = error
    ? "Backend: unreachable"
    : status
    ? formatHealthLabel(status)
    : "Connecting…";

  const degraded =
    status?.status === "degraded" ||
    (status?.services &&
      Object.values(status.services).some((s) => s?.status === "down"));

  return (
    <div style={styles.bar}>
      <span style={styles.dot}>{dot}</span>
      <span style={styles.label}>
        <strong>Prompter</strong>&nbsp;{label}
      </span>
      {degraded && !error && (
        <span style={{ color: "var(--warning)", marginLeft: 8 }}>
          ⚠ Degraded
        </span>
      )}
      {modelLoading && (
        <span style={styles.modelLoading}>
          Loading {modelLoading.model} (~{modelLoading.estimated_seconds}s)
        </span>
      )}
      {onToggleDebugTrace && (
        <button
          type="button"
          style={{
            ...styles.debugBtn,
            ...(debugTraceOpen ? styles.debugBtnActive : {}),
          }}
          onClick={onToggleDebugTrace}
        >
          Debug trace{debugTraceOpen ? " ▾" : ""}
        </button>
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
    flex: 1,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  modelLoading: {
    color: "var(--accent)",
    fontFamily: "var(--font-mono)",
    fontSize: 11,
    flexShrink: 0,
    marginLeft: "auto",
  },
  debugBtn: {
    marginLeft: 8,
    padding: "2px 8px",
    borderRadius: "var(--radius-sm)",
    background: "var(--bg-hover)",
    color: "var(--text-dim)",
    fontSize: 11,
    fontFamily: "var(--font-mono)",
    flexShrink: 0,
  },
  debugBtnActive: {
    background: "var(--accent-dim)",
    color: "var(--accent)",
  },
};
