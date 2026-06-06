import type { SourceChunk } from "../api/client";

interface Props {
  sources: SourceChunk[];
}

function displayTitle(chunk: SourceChunk): string {
  return chunk.title?.trim() || chunk.source_file || chunk.source || "Source";
}

function isUrl(value: string): boolean {
  return /^https?:\/\//i.test(value);
}

export default function SourceCitations({ sources }: Props) {
  if (sources.length === 0) return null;

  return (
    <div style={styles.wrap}>
      <div style={styles.heading}>Sources</div>
      {sources.map((chunk, i) => {
        const title = displayTitle(chunk);
        const path = chunk.source_file || chunk.source;
        const snippet = chunk.text?.trim().slice(0, 200);
        const score =
          typeof chunk.score === "number" && chunk.score > 0
            ? chunk.score.toFixed(2)
            : null;

        return (
          <div key={i} style={styles.card}>
            <div style={styles.cardHeader}>
              {isUrl(path) ? (
                <a href={path} style={styles.link} target="_blank" rel="noreferrer">
                  {title}
                </a>
              ) : (
                <span style={styles.fileName} title={path}>
                  {title}
                </span>
              )}
              {score && <span style={styles.score}>{score}</span>}
            </div>
            {!isUrl(path) && path && path !== title && (
              <div style={styles.path}>{path}</div>
            )}
            {snippet && <div style={styles.snippet}>{snippet}…</div>}
          </div>
        );
      })}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  wrap: {
    marginTop: 10,
    padding: "8px 10px",
    borderRadius: "var(--radius-md)",
    background: "var(--accent-dim2)",
    border: "1px solid var(--border)",
  },
  heading: {
    fontSize: 11,
    fontWeight: 600,
    color: "var(--text-muted)",
    textTransform: "uppercase",
    letterSpacing: "0.04em",
    marginBottom: 6,
  },
  card: {
    padding: "6px 0",
    borderTop: "1px solid var(--border)",
  },
  cardHeader: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 8,
  },
  fileName: {
    fontSize: 12,
    fontWeight: 600,
    color: "var(--accent)",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  link: {
    fontSize: 12,
    fontWeight: 600,
    color: "var(--accent)",
    textDecoration: "underline",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  path: {
    fontSize: 10,
    color: "var(--text-dim)",
    fontFamily: "var(--font-mono)",
    marginTop: 2,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  snippet: {
    fontSize: 11,
    color: "var(--text-muted)",
    marginTop: 4,
    lineHeight: 1.45,
  },
  score: {
    fontSize: 10,
    color: "var(--text-dim)",
    fontFamily: "var(--font-mono)",
    flexShrink: 0,
  },
};
